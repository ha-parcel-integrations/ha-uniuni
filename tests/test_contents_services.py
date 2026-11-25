"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.uniuni.const import CONF_PARCELS, CONF_TRACKING_CODE, DOMAIN
from custom_components.uniuni.contents import CONTENTS_STORE_MAX_CODES
from custom_components.uniuni.coordinator import UniUniCoordinator

from .payloads import ACTIVE_CODE, DELIVERED_CODE, active_sample

CLIENT = "custom_components.uniuni.api.UniUniApiClient"
COORD = f"{UniUniCoordinator.__module__}.UniUniCoordinator"
UNTRACKED = "EXAMPLE555555"
NEW_CODE = "EXAMPLE999999"
STORE_KEY = f"{DOMAIN}.contents.%s"


async def _setup(hass, codes: list[str] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes or []]},
    )
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "update_contents", {**track, "contents": " Running  shoes "})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "update_contents", {**track, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "update_contents", {**track, "contents": "  "})
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}
    await _call(hass, "update_contents", {**track, "contents": "Running shoes"})

    await _call(hass, "update_contents", track)

    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass, ["EXAMPLE-777"])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: " example-777 ", "contents": "Running shoes"},
    )
    assert _contents(entry, "EXAMPLE-777") == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_a_contents_change_never_fetches_or_refreshes(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with (
        patch(f"{COORD}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        assert _contents(entry, ACTIVE_CODE) == "Running shoes"

        # unchanged value: nothing written, nobody told
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        assert listeners.call_count == 1

        # replace and clear through track_parcel on the tracked code
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {**data, "contents": "Winter socks"},
            blocking=True,
        )
        assert listeners.call_count == 2
        assert _contents(entry, ACTIVE_CODE) == "Winter socks"
        await hass.services.async_call(
            DOMAIN, "track_parcel", {**data, "contents": ""}, blocking=True
        )
        assert listeners.call_count == 3
        assert _contents(entry, ACTIVE_CODE) is None

        # a code that is not tracked changes the store, not the parcels
        await hass.services.async_call(
            DOMAIN,
            "update_contents",
            {CONF_TRACKING_CODE: UNTRACKED, "contents": "Socks"},
            blocking=True,
        )
        await hass.async_block_till_done()
        refresh.assert_not_called()
        fetch.assert_not_called()


async def test_reapply_reaches_delivered_parcels_and_response_codes(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    coordinator.delivered = [
        {"barcode": DELIVERED_CODE, "contents": "stale"},
        {"barcode": None, "contents": "stale"},
    ]
    coordinator._code_by_barcode[DELIVERED_CODE] = "EXAMPLE000321"
    coordinator.contents_store.async_set("EXAMPLE000321", "Running shoes")

    coordinator.async_reapply_contents()

    assert coordinator.delivered[0]["contents"] == "Running shoes"
    assert coordinator.delivered[1]["contents"] is None


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: NEW_CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: NEW_CODE}]
    assert _contents(entry, NEW_CODE) == "Running shoes"


async def test_a_newly_tracked_code_still_refreshes_through_the_options_listener(
    hass,
):
    await _setup(hass)
    with patch(f"{COORD}.async_request_refresh", new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: NEW_CODE, "contents": "Running shoes"},
            blocking=True,
        )
        await hass.async_block_till_done()
    assert refresh.await_count == 1


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: NEW_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(NEW_CODE) is None
    assert _contents(entry, NEW_CODE) is None


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_the_cap_drops_the_oldest_value_at_201(hass):
    entry = await _setup(hass)
    store = entry.runtime_data.coordinator.contents_store
    for index in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"CODE{index}", "Shoes")

    assert store.get("CODE0") is None
    assert store.get("CODE1") == "Shoes"
    assert store.get(f"CODE{CONTENTS_STORE_MAX_CODES}") == "Shoes"


async def test_contents_text_is_trimmed_and_capped_through_the_action(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "  " + "x" * 300 + "  "},
    )
    assert _contents(entry, ACTIVE_CODE) == "x" * 120


async def test_an_entry_without_a_store_file_sets_up_with_no_contents(
    hass, hass_storage
):
    assert STORE_KEY % "any" not in hass_storage
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass, [ACTIVE_CODE])
    events = []
    for name in ("parcel_status_changed", "parcel_registered", "parcel_delivered"):
        hass.bus.async_listen(f"{DOMAIN}_{name}", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert events == []


async def test_untrack_works_when_the_entry_is_not_loaded(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with patch.object(hass.config_entries, "async_update_entry") as update:
        await hass.services.async_call(
            DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
    assert update.call_count == 1
    entry.mock_state(hass, ConfigEntryState.LOADED)
