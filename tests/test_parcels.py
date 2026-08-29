"""Tests for UniUni's numeric state mapping and safe normalisation."""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.uniuni import parcels as parcels_module
from custom_components.uniuni.const import CAPABILITIES, DOMAIN, ParcelStatus
from custom_components.uniuni.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import active_sample, delivered_sample, event, pickup_sample


@pytest.mark.parametrize("state,status", [(190, ParcelStatus.REGISTERED), (199, ParcelStatus.IN_TRANSIT), (202, ParcelStatus.OUT_FOR_DELIVERY), (203, ParcelStatus.DELIVERED), (214, ParcelStatus.AT_PICKUP_POINT), (211, ParcelStatus.RETURNING), (213, ParcelStatus.PROBLEM)])
def test_numeric_states_map(state, status):
    assert map_parcel_status(state) is status
    assert map_event_status(state) is status


def test_unknown_state_warns_once(caplog):
    assert map_parcel_status(None) is ParcelStatus.UNKNOWN
    assert map_event_status(None) is None
    assert map_parcel_status(9999) is ParcelStatus.UNKNOWN
    assert map_parcel_status(9999) is ParcelStatus.UNKNOWN
    assert caplog.text.count("9999") == 1


def test_timestamp_helpers_and_dimensions_are_safe():
    assert parse_iso("2026-01-01T00:00:00Z").tzinfo
    assert parse_iso("garbage") is None
    assert to_iso_timestamp(1788206400000)
    assert to_iso_timestamp(10**20) is None
    assert format_dimensions(1, 2, 3)["text"] == "1 x 2 x 3 cm"
    assert format_dimensions(1, None, 3) is None
    assert tracking_url("UUS-X") is None


def test_history_sorts_trace_time_and_drops_unsettled_timestamp():
    history = build_history(delivered_sample()["spath_list"])
    assert [item["status"] for item in history] == [ParcelStatus.REGISTERED, ParcelStatus.IN_TRANSIT, ParcelStatus.DELIVERED]
    assert build_history([{"state": 199}, "bad"]) == []
    malformed = event(199, 1, None)
    malformed["dateTime"] = {"ts": 1}
    assert build_history([malformed]) == []


def test_timestamp_shape_warns_once(caplog):
    parcels_module._warned.discard("timestamp-shape")
    malformed = event(199, 1, None)
    malformed["dateTime"] = {"ts": 1}
    assert build_history([malformed]) == []
    assert build_history([malformed]) == []
    assert caplog.text.count("dateTime keys") == 1
    assert "issues/new" in caplog.text
    parcels_module._warned.discard("timestamp-shape")


def test_status_event_disagreement_warns_once(caplog):
    parcels_module._warned.discard("status-event-disagreement")
    raw = delivered_sample()
    raw["state"] = 202  # out_for_delivery, while the latest history event is 'delivered'
    normalize_parcel(raw, include_history=True)
    normalize_parcel(raw, include_history=True)
    assert caplog.text.count("disagrees with its latest history event") == 1
    assert "state=out_for_delivery event=delivered" in caplog.text
    assert "issues/new" in caplog.text
    parcels_module._warned.discard("status-event-disagreement")


def test_status_event_agreement_does_not_warn():
    parcels_module._warned.discard("status-event-disagreement")
    normalize_parcel(delivered_sample(), include_history=True)
    assert "status-event-disagreement" not in parcels_module._warned


def test_normalize_exact_contract_and_retains_source_record():
    raw = delivered_sample()
    parcel = normalize_parcel(raw, include_history=True)
    assert list(parcel) == ["carrier", "barcode", "sender", "receiver", "status", "raw_status", "delivered", "delivered_at", "planned_from", "planned_to", "pickup", "pickup_point", "url", "weight", "dimensions", "history", "raw"]
    assert parcel["barcode"] == raw["tno"] and parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["history"] and parcel["planned_from"] is None and parcel["delivered_at"] is None
    assert parcel["sender"] is parcel["receiver"] is parcel["pickup_point"] is None
    assert parcel["raw"] is raw


def test_active_pickup_and_pending_records_degrade_safely():
    assert normalize_parcel(active_sample())["status"] is ParcelStatus.OUT_FOR_DELIVERY
    pickup = normalize_parcel(pickup_sample())
    assert pickup["pickup"] is True and pickup["pickup_point"] is None
    pending = normalize_parcel({"tno": "UUS-PENDING"})
    assert pending["status"] is ParcelStatus.UNKNOWN and pending["history"] is None
    assert CAPABILITIES == frozenset({"history"})


def test_sort_and_delivered_filter():
    ordered = sort_parcels_by_ts([{"barcode": "b", "planned_from": None}, {"barcode": "a", "planned_from": "2026-01-01T00:00:00Z"}], "planned_from")
    assert [item["barcode"] for item in ordered] == ["a", "b"]
    now = datetime.now(timezone.utc)
    entry = MockConfigEntry(domain=DOMAIN, options={"delivered_filter_type": "days", "delivered_filter_amount": 7})
    assert len(apply_delivered_filter([{"delivered_at": (now - timedelta(days=1)).isoformat()}, {"delivered_at": (now - timedelta(days=30)).isoformat()}, {"delivered_at": "bad"}], entry)) == 2
