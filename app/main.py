import json
from datetime import datetime, timezone

import psycopg
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import cache, db
from .pricing import (
    DAILY_CAP_PAISE,
    DAY_RATE_PAISE,
    FREE_MINUTES,
    NIGHT_RATE_PAISE,
    SIZES,
    PricingError,
    allocate,
    estimate,
    fee,
    normalise_vehicle,
    occupancy,
)

app = FastAPI(title="parking-lot")

OCCUPIED = "occ:occupied"
TOTALS = "occ:total"
READY = "occ:ready"


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def rebuild_occupancy():
    """Recount from Postgres. The counters are a cache; the tickets are the truth."""
    slots = db.query("SELECT id, code, floor, size FROM slots")
    taken = [r["slot_id"] for r in
             db.query("SELECT slot_id FROM tickets WHERE exited_at IS NULL")]
    snap = occupancy(slots, taken)

    totals = {"all": snap["total"]}
    occupied = {"all": snap["occupied"]}
    for floor, v in snap["by_floor"].items():
        totals["floor:" + floor] = v["total"]
        occupied["floor:" + floor] = v["occupied"]
    for size, v in snap["by_size"].items():
        totals["size:" + size] = v["total"]
        occupied["size:" + size] = v["occupied"]

    r = cache.client()
    pipe = r.pipeline()
    pipe.delete(TOTALS, OCCUPIED)
    pipe.hset(TOTALS, mapping={k: str(v) for k, v in totals.items()})
    pipe.hset(OCCUPIED, mapping={k: str(v) for k, v in occupied.items()})
    pipe.set(READY, datetime.now(timezone.utc).isoformat())
    pipe.execute()
    return snap


def ensure_occupancy():
    """Recount if the counters are missing. Returns True when it did.

    That return value matters: a rebuild reads Postgres, which has already
    committed the caller's own entry or exit, so the caller must NOT then
    move the counter a second time. HINCRBY is not idempotent.
    """
    if not cache.client().exists(READY):
        rebuild_occupancy()
        return True
    return False


def bump(slot, delta):
    """Move the live counters by one, in the three places that matter."""
    r = cache.client()
    pipe = r.pipeline()
    for field in ("all", "floor:%s" % slot["floor"], "size:%s" % slot["size"]):
        pipe.hincrby(OCCUPIED, field, delta)
    pipe.execute()


def open_ticket(plate):
    return db.one(
        "SELECT t.id, t.plate, t.vehicle_kind, t.entered_at, s.code, s.floor, s.size"
        " FROM tickets t JOIN slots s ON s.id=t.slot_id"
        " WHERE t.plate=%s AND t.exited_at IS NULL", (plate,))


@app.post("/checkin", status_code=201)
def checkin(payload: dict = Body(...)):
    plate = str(payload.get("plate", "")).strip().upper()
    if not plate:
        raise HTTPException(400, "a vehicle needs a number plate")
    try:
        size = normalise_vehicle(payload.get("kind"))
    except PricingError as e:
        raise HTTPException(400, str(e))

    if open_ticket(plate):
        raise HTTPException(409, "%s is already parked here" % plate)

    free = db.query(
        "SELECT s.id, s.code, s.floor, s.size FROM slots s"
        " WHERE NOT EXISTS (SELECT 1 FROM tickets t"
        "                   WHERE t.slot_id=s.id AND t.exited_at IS NULL)")
    slot = allocate(size, free)
    if slot is None:
        raise HTTPException(409, "the lot is full for a %s vehicle" % size)

    try:
        ticket = db.one(
            "INSERT INTO tickets (plate, vehicle_kind, slot_id)"
            " VALUES (%s,%s,%s) RETURNING id, entered_at",
            (plate, size, slot["id"]))
    except psycopg.errors.UniqueViolation:
        # A partial unique index holds the line when two barriers race.
        raise HTTPException(409, "that slot was taken while you were arriving -"
                                 " try again")

    if not ensure_occupancy():
        bump(slot, 1)
    return {"ticket_id": ticket["id"], "plate": plate, "size": size,
            "slot": {k: slot[k] for k in ("id", "code", "floor", "size")},
            "entered_at": ticket["entered_at"].isoformat(),
            "free_minutes": FREE_MINUTES}


@app.post("/checkout")
def checkout(payload: dict = Body(...)):
    plate = str(payload.get("plate", "")).strip().upper()
    if not plate:
        raise HTTPException(400, "a vehicle needs a number plate")

    # Stamp the exit and claim the ticket in one statement, so a second
    # barrier pressing the button cannot bill the same stay twice.
    row = db.one(
        "UPDATE tickets SET exited_at=now() WHERE plate=%s AND exited_at IS NULL"
        " RETURNING id, slot_id, vehicle_kind, entered_at, exited_at", (plate,))
    if row is None:
        settled = db.one("SELECT id, fee_paise, exited_at FROM tickets"
                         " WHERE plate=%s ORDER BY id DESC LIMIT 1", (plate,))
        if settled:
            raise HTTPException(409, "%s has already left (ticket %d)"
                                % (plate, settled["id"]))
        raise HTTPException(404, "no vehicle here with plate %s" % plate)

    try:
        bill = fee(row["entered_at"], row["exited_at"])
    except PricingError as e:
        raise HTTPException(400, str(e))

    db.query("UPDATE tickets SET fee_paise=%s, receipt=%s::jsonb WHERE id=%s",
             (bill["total_paise"], json.dumps(bill, default=str), row["id"]),
             fetch=False)

    slot = db.one("SELECT id, code, floor, size FROM slots WHERE id=%s",
                  (row["slot_id"],))
    if not ensure_occupancy():
        bump(slot, -1)
    return {"ticket_id": row["id"], "plate": plate,
            "slot": slot["code"], "vehicle": row["vehicle_kind"],
            "entered_at": row["entered_at"].isoformat(),
            "exited_at": row["exited_at"].isoformat(), **bill}


@app.get("/occupancy")
def live_occupancy():
    ensure_occupancy()
    r = cache.client()
    totals = r.hgetall(TOTALS)
    used = r.hgetall(OCCUPIED)

    def block(field):
        total = int(totals.get(field, 0))
        occ = int(used.get(field, 0))
        return {"total": total, "occupied": occ, "free": total - occ,
                "percent_full": round(100 * occ / total, 1) if total else 0.0}

    return {
        "source": "redis",
        **block("all"),
        "by_floor": {k.split(":", 1)[1]: block(k)
                     for k in sorted(totals) if k.startswith("floor:")},
        "by_size": {s: block("size:" + s) for s in SIZES if "size:" + s in totals},
        "tariff": {"free_minutes": FREE_MINUTES,
                   "day_rate_rupees": DAY_RATE_PAISE / 100,
                   "night_rate_rupees": NIGHT_RATE_PAISE / 100,
                   "daily_cap_rupees": DAILY_CAP_PAISE / 100},
    }


@app.get("/tickets/{plate}")
def ticket(plate: str):
    plate = plate.strip().upper()
    row = open_ticket(plate)
    if not row:
        raise HTTPException(404, "no vehicle here with plate %s" % plate)
    so_far = fee(row["entered_at"], datetime.now(timezone.utc))
    return {"ticket_id": row["id"], "plate": plate, "vehicle": row["vehicle_kind"],
            "slot": {"code": row["code"], "floor": row["floor"], "size": row["size"]},
            "entered_at": row["entered_at"].isoformat(),
            "fee_so_far": so_far}


@app.get("/quote")
def quote(hours: float = Query(...), kind: str = Query("car"),
          start: str = Query(None)):
    """What would a stay of this length cost? Same maths as the real bill."""
    try:
        normalise_vehicle(kind)
        entry = datetime.fromisoformat(start) if start else datetime.now(timezone.utc)
    except PricingError as e:
        raise HTTPException(400, str(e))
    except ValueError:
        raise HTTPException(400, "start must be an ISO timestamp")
    try:
        return {"kind": kind, "hours": hours, "from": entry.isoformat(),
                **estimate(entry, hours)}
    except PricingError as e:
        raise HTTPException(400, str(e))


@app.get("/slots")
def slots(free: bool = Query(False), size: str = Query(None)):
    if size and size not in SIZES:
        raise HTTPException(400, "size must be one of %s" % ", ".join(SIZES))
    sql = ("SELECT s.id, s.code, s.floor, s.size, t.plate AS occupied_by"
           " FROM slots s LEFT JOIN tickets t"
           " ON t.slot_id=s.id AND t.exited_at IS NULL")
    where = []
    params = []
    if free:
        where.append("t.id IS NULL")
    if size:
        where.append("s.size=%s")
        params.append(size)
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = db.query(sql + " ORDER BY s.floor, s.code", tuple(params))
    return {"count": len(rows), "free_only": free, "size": size, "slots": rows}
