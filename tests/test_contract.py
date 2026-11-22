"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime

import pytest

from custom_components.uniuni.const import ParcelStatus
from custom_components.uniuni.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _with_state(code: int) -> dict:
    sample = pickup_sample()
    sample["state"] = code
    return sample


def _collected() -> dict:
    sample = pickup_sample()
    sample["state"] = 203
    sample["spath_list"] = [
        event(203, 3, 1788206400, "Collected"),
        event(214, 2, 1788120000, "Ready for collection"),
        event(190, 1, 1788033600, "Registered"),
    ]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": lambda: _with_state(211),
    "problem": lambda: _with_state(192),
    "bare": lambda: {"tno": "UUS-SYNTHETIC-BARE"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_delivered_at_is_the_delivery_event():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["delivered_at"] == datetime.fromtimestamp(1788206400, UTC).isoformat()


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_planned_to_never_without_planned_from(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False
    assert normalize_parcel(active_sample())["pickup"] is False


def test_is_pickup_prefers_the_route_flag_and_survives_collection():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(None, ParcelStatus.DELIVERED, [None]) is False


def test_planned_for_status_empties_the_window():
    window = ("2099-01-01T00:00:00+00:00", "2099-01-01T23:59:59+00:00")
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, *window) == (None, None)


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=UTC)
    end = datetime.fromisoformat(end)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(start).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=UTC)
    start = datetime.fromisoformat(start)
    end = datetime.fromisoformat(end)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
