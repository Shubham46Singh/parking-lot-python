from datetime import datetime, timedelta, timezone

import pytest

from app.pricing import (
    DAILY_CAP_PAISE,
    FREE_MINUTES,
    PricingError,
    allocate,
    fee,
    fits,
    normalise_vehicle,
    occupancy,
)

UTC = timezone.utc


def test_parking_within_free_period_costs_nothing():
    entry = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    result = fee(entry, entry + timedelta(minutes=20))

    assert result["total_paise"] == 0
    assert result["billable_hours"] == 0


def test_exactly_thirty_minutes_is_free():
    entry = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    result = fee(entry, entry + timedelta(minutes=FREE_MINUTES))

    assert result["total_paise"] == 0


def test_thirty_one_minutes_charges_one_hour():
    entry = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    result = fee(entry, entry + timedelta(minutes=31))

    assert result["billable_hours"] == 1
    assert result["total_paise"] == 4000


def test_invalid_vehicle_is_rejected():
    with pytest.raises(PricingError):
        normalise_vehicle("spaceship")


def test_vehicle_cannot_use_a_smaller_slot():
    assert fits("compact", "large")
    assert not fits("large", "compact")


def test_smallest_suitable_slot_is_selected():
    free_slots = [
        {"id": 1, "floor": 0, "size": "large", "code": "G-L1"},
        {"id": 2, "floor": 1, "size": "compact", "code": "1-C01"},
        {"id": 3, "floor": 2, "size": "bike", "code": "2-B01"},
    ]

    selected = allocate("car", free_slots)

    assert selected["code"] == "1-C01"


def test_large_vehicle_cannot_use_compact_slot():
    free_slots = [
        {"id": 1, "floor": 1, "size": "compact", "code": "1-C01"},
    ]

    assert allocate("suv", free_slots) is None


def test_occupancy_counts_slots_correctly():
    slots = [
        {"id": 1, "floor": 0, "size": "large"},
        {"id": 2, "floor": 1, "size": "compact"},
        {"id": 3, "floor": 1, "size": "bike"},
    ]

    result = occupancy(slots, [1, 3])

    assert result["total"] == 3
    assert result["occupied"] == 2
    assert result["free"] == 1
    assert result["percent_full"] == pytest.approx(66.7)
    assert result["by_floor"]["1"]["occupied"] == 1


def test_stay_beyond_maximum_is_rejected():
    entry = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)

    with pytest.raises(PricingError):
        fee(entry, entry + timedelta(days=31))


def test_daily_cap_is_not_exceeded():
    entry = datetime(2026, 10, 15, 0, 30, tzinfo=UTC)
    result = fee(entry, entry + timedelta(hours=14))

    assert result["total_paise"] <= DAILY_CAP_PAISE
    assert result["capped"] is True
