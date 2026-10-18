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
    assert to_iso_timestamp(1788206400)
    assert to_iso_timestamp(10**20) is None
    assert format_dimensions(1, 2, 3)["text"] == "1 x 2 x 3 cm"
    assert format_dimensions(1, None, 3) is None
    assert tracking_url("UUS-X") is None


def test_history_sorts_trace_time_and_drops_unusable_timestamp():
    history = build_history(delivered_sample()["spath_list"])
    assert [item["status"] for item in history] == [ParcelStatus.REGISTERED, ParcelStatus.IN_TRANSIT, ParcelStatus.DELIVERED]
    assert build_history([{"state": 199}, "bad"]) == []
    malformed = event(199, 1, None)
    malformed["dateTime"] = {"ts": None}
    assert build_history([malformed]) == []


def test_missing_timezone_falls_back_to_ts():
    # The pre-network "Order received" event consistently carries a null
    # timezone/offsetByGMT on real parcels; ts alone must still anchor it.
    pre_network = event(190, 1, 1788033600)
    pre_network["dateTime"]["timezone"] = None
    pre_network["dateTime"]["offsetByGMT"] = None
    history = build_history([pre_network])
    assert history[0]["timestamp"] == "2026-08-29T20:00:00+00:00"


def test_timestamp_shape_warns_once(caplog):
    parcels_module._warned.discard("timestamp-shape")
    malformed = event(199, 1, None)
    malformed["dateTime"] = {"ts": None}
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
    assert parcel["history"] and parcel["planned_from"] is None
    assert parcel["delivered_at"] == parcel["history"][-1]["timestamp"]
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


def test_an_event_without_ts_is_kept_via_local_time_and_zone(caplog):
    """UniUni drops ts on the tail of some parcels and sends only wall time."""
    history = build_history(
        [
            {
                "state": 200,
                "description_en": "In transit",
                "dateTime": {"timezone": "America/Vancouver", "ts": 1784770251},
            },
            {
                "state": 203,
                "description_en": "Delivered",
                "dateTime": {
                    "timezone": "America/Los_Angeles",
                    "localTime": "2026-07-30 11:48:30",
                },
            },
        ]
    )
    assert len(history) == 2
    assert history[-1]["raw_status"] == "Delivered"
    assert history[-1]["timestamp"] == "2026-07-30T11:48:30-07:00"
    assert "no usable ts" not in caplog.text


def test_an_event_with_neither_ts_nor_a_usable_local_time_is_still_dropped(caplog):
    for metadata in (
        {"timezone": "America/Vancouver", "localTime": "not a time"},
        {"timezone": "Mars/Olympus", "localTime": "2026-07-30 11:48:30"},
        {"localTime": "2026-07-30 11:48:30"},
        {"timezone": "America/Vancouver"},
    ):
        assert build_history([{"state": 203, "dateTime": metadata}]) == []
    assert "no usable ts" in caplog.text


def test_zone_names_lists_every_event_zone():
    raw = {"spath_list": [{"dateTime": {"timezone": "Asia/Tokyo"}}, {"dateTime": None}, "x"]}
    assert parcels_module.zone_names(raw) == {"Asia/Tokyo"}


def test_local_time_uses_the_zones_the_coordinator_resolved():
    from zoneinfo import ZoneInfo

    metadata = {"localTime": "2026-01-02T10:00:00", "timezone": "Asia/Tokyo"}
    zones = {"Asia/Tokyo": ZoneInfo("Asia/Tokyo")}
    assert parcels_module._local_time_timestamp(metadata, zones) == "2026-01-02T10:00:00+09:00"
    # A zone the coordinator could not resolve yields no timestamp, never a guess.
    assert parcels_module._local_time_timestamp(metadata, {"Asia/Tokyo": None}) is None
