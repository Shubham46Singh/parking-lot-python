"""Pure parking fees and slot allocation. No database, no HTTP.

Money is in paise (integers) all the way through. A float rupee is a bug
waiting for a 26 hour stay to expose it.

The timezone is a fixed +05:30 offset rather than a named zone, because the
container runs in UTC and a slim base image may not carry a timezone
database at all. The overnight discount is a local-clock rule, so it has to
be decided against local time, not UTC.

What is worth testing
---------------------
* ``fee`` under 30 minutes - free, and exactly 30 minutes is still free.
* ``fee`` at 31 minutes - one charged hour, not zero and not two.
* Part hours round up: 61 minutes of billable time is two hours.
* The overnight rate - a stay entirely inside 22:00-06:00 local, a stay that
  straddles 06:00, and a stay that straddles 22:00. Each hour is charged at
  the rate of the hour it begins in.
* The daily cap - a 14 hour daytime stay costs the cap, not 14 hourly units.
* **The 26 hour question.** Blocks roll from the entry time, so a 26 hour
  stay is one full capped block plus a 2 hour block charged on its own. The
  cap applies per block, so it can apply twice, but the second block only
  pays for the 2 hours it actually used. Test 24h, 24h01m, 26h and 49h.
* ``fit`` and ``allocate`` - a bike may take a car slot when nothing smaller
  is free, a truck may never take a car slot, the smallest fitting slot wins,
  and the lowest floor breaks a tie.
* ``validate_stay`` - an exit before the entry, and a stay beyond the maximum.
"""
from datetime import timedelta, timezone

# The lot is in India. Fixed offset on purpose - see the module docstring.
LOCAL = timezone(timedelta(hours=5, minutes=30))

FREE_MINUTES = 30

DAY_RATE_PAISE = 4000        # Rs 40 per hour, 06:00-22:00 local
NIGHT_RATE_PAISE = 2000      # Rs 20 per hour, 22:00-06:00 local
DAILY_CAP_PAISE = 30000      # Rs 300 for any one 24 hour block

NIGHT_FROM = 22              # local hour the overnight rate starts
NIGHT_TO = 6                 # local hour it ends

BLOCK_HOURS = 24
MAX_STAY_DAYS = 30           # beyond this it is an abandoned vehicle, not parking

# Smallest first. A vehicle fits a slot of its own size or anything larger.
SIZES = ("bike", "compact", "large", "oversize")

VEHICLE_SIZE = {
    "bike": "bike", "scooter": "bike", "motorcycle": "bike",
    "hatchback": "compact", "sedan": "compact", "car": "compact",
    "suv": "large", "van": "large", "pickup": "large",
    "truck": "oversize", "bus": "oversize",
}


class PricingError(ValueError):
    """A stay or allocation that must be refused, with a readable reason."""


def _local(ts):
    """View an instant on the lot's local clock, assuming UTC if naive."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(LOCAL)


def is_overnight(ts):
    """Does this instant fall in the overnight window on the local clock?

    The window wraps midnight, so the test is an OR, not a BETWEEN. That
    wrap is the bug students ship.
    """
    hour = _local(ts).hour
    return hour >= NIGHT_FROM or hour < NIGHT_TO


def rate_for(ts):
    return NIGHT_RATE_PAISE if is_overnight(ts) else DAY_RATE_PAISE


def validate_stay(entry, exit_at):
    """Reject a stay that cannot have happened."""
    if exit_at < entry:
        raise PricingError("a vehicle cannot leave before it arrived")
    if exit_at - entry > timedelta(days=MAX_STAY_DAYS):
        raise PricingError("a stay of more than %d days is not parking"
                           % MAX_STAY_DAYS)
    return int((exit_at - entry).total_seconds() // 60)


def billable_hours(entry, exit_at):
    """The charged hours of a stay, each with the instant it starts.

    The first FREE_MINUTES are free and they are free ONCE, at the start of
    the stay - not once per day, which would make a week of parking free.
    After that, every started hour is charged in full, which is how every
    car park in the world does it.

    Returns a list of (index_from_entry, starts_at, rate_paise).
    """
    total_minutes = validate_stay(entry, exit_at)
    chargeable = total_minutes - FREE_MINUTES
    if chargeable <= 0:
        return []
    billing_from = entry + timedelta(minutes=FREE_MINUTES)
    count = -(-chargeable // 60)          # round up: a part hour is an hour
    out = []
    for i in range(count):
        starts = billing_from + timedelta(hours=i)
        out.append((i, starts, rate_for(starts)))
    return out


def fee(entry, exit_at):
    """Work out what this stay costs, and show the working.

    **The 26 hour question.** The cap is per 24 hour block, and the blocks
    roll forward from the moment the vehicle entered - not from midnight,
    which would make an overnight stay cost two days for one night. So a 26
    hour stay is block 1 (capped) plus block 2, which contains only 2 hours
    and therefore costs 2 hours, not another full cap. The cap can apply
    more than once, but only to a block that actually earned it.

    Returns a dict with the total and a per-block breakdown, so the receipt
    can explain itself when a customer argues.
    """
    minutes = validate_stay(entry, exit_at)
    hours = billable_hours(entry, exit_at)

    blocks = {}
    for index, starts, rate in hours:
        # Hour `index` begins FREE_MINUTES + index*60 minutes after entry.
        offset = FREE_MINUTES + index * 60
        block = offset // (BLOCK_HOURS * 60)
        b = blocks.setdefault(block, {"block": block + 1, "hours": 0,
                                      "gross_paise": 0, "night_hours": 0})
        b["hours"] += 1
        b["gross_paise"] += rate
        if rate == NIGHT_RATE_PAISE:
            b["night_hours"] += 1

    total = 0
    breakdown = []
    for block in sorted(blocks):
        b = blocks[block]
        capped = b["gross_paise"] > DAILY_CAP_PAISE
        charged = DAILY_CAP_PAISE if capped else b["gross_paise"]
        b["capped"] = capped
        b["charged_paise"] = charged
        b["charged_rupees"] = round(charged / 100, 2)
        total += charged
        breakdown.append(b)

    return {
        "minutes": minutes,
        "free_minutes": min(minutes, FREE_MINUTES),
        "billable_hours": len(hours),
        "blocks": breakdown,
        "total_paise": total,
        "total_rupees": round(total / 100, 2),
        "capped": any(b["capped"] for b in breakdown),
    }


def normalise_vehicle(kind):
    k = str(kind or "").strip().lower()
    if k in SIZES:
        return k
    if k in VEHICLE_SIZE:
        return VEHICLE_SIZE[k]
    raise PricingError("%r is not a vehicle we have a slot size for" % (kind,))


def size_index(size):
    size = str(size or "").strip().lower()
    if size not in SIZES:
        raise PricingError("%r is not a slot size" % (size,))
    return SIZES.index(size)


def fits(vehicle_size, slot_size):
    """A vehicle fits a slot of its own size or anything bigger. Never smaller."""
    return size_index(vehicle_size) <= size_index(slot_size)


def allocate(vehicle_kind, free_slots):
    """Pick the slot to give this vehicle.

    Smallest slot that fits, so a scooter does not burn the only truck bay;
    then the lowest floor, so the lot fills from the bottom and the walk is
    short; then the slot number, so the choice is deterministic and a test
    can assert on it.

    ``free_slots`` is [{"id":..., "floor":..., "size":..., "code":...}, ...].
    Returns the chosen slot, or None when the lot is full for this size.
    """
    want = normalise_vehicle(vehicle_kind)
    candidates = [s for s in free_slots if fits(want, s["size"])]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (size_index(s["size"]),
                                          int(s["floor"]),
                                          str(s.get("code", s["id"]))))


def occupancy(slots, occupied_ids):
    """Live counts by floor and by slot size, plus the headline percentage."""
    occupied = set(occupied_ids)
    by_floor, by_size = {}, {}
    for s in slots:
        floor = str(s["floor"])
        size = str(s["size"])
        f = by_floor.setdefault(floor, {"total": 0, "occupied": 0})
        z = by_size.setdefault(size, {"total": 0, "occupied": 0})
        f["total"] += 1
        z["total"] += 1
        if s["id"] in occupied:
            f["occupied"] += 1
            z["occupied"] += 1
    for bucket in (by_floor, by_size):
        for v in bucket.values():
            v["free"] = v["total"] - v["occupied"]
            v["percent_full"] = round(100 * v["occupied"] / v["total"], 1) if v["total"] else 0.0
    total = len(slots)
    used = len(occupied & {s["id"] for s in slots})
    return {
        "total": total,
        "occupied": used,
        "free": total - used,
        "percent_full": round(100 * used / total, 1) if total else 0.0,
        "by_floor": dict(sorted(by_floor.items())),
        "by_size": {k: by_size[k] for k in SIZES if k in by_size},
    }


def estimate(entry, hours_ahead):
    """What it will cost if the vehicle leaves in N hours - for the sign at
    the barrier. Same maths as ``fee``, so the estimate cannot drift."""
    try:
        hours_ahead = float(hours_ahead)
    except (TypeError, ValueError):
        raise PricingError("%r is not a number of hours" % (hours_ahead,))
    if hours_ahead < 0:
        raise PricingError("cannot estimate backwards in time")
    return fee(entry, entry + timedelta(hours=hours_ahead))
