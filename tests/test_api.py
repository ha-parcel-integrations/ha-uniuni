"""Tests for UniUni's public tracking and guarded EDD requests."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.uniuni import api as api_module
from custom_components.uniuni.api import UniUniApiClient, UniUniApiError

from .payloads import active_sample

CODE = "UUS-SYNTHETIC-TEST"


def _context(status: int, body: object) -> MagicMock:
    response = AsyncMock(status=status)
    response.json = AsyncMock(
        side_effect=json.JSONDecodeError("x", str(body), 0) if isinstance(body, str) else None,
        return_value=None if isinstance(body, str) else body,
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _session(get_body: object, edd_body: object = None, *, get_status=200, edd_status=200):
    session = MagicMock()
    session.get.return_value = _context(get_status, get_body)
    session.post.return_value = _context(edd_status, edd_body or {"data": []})
    return session


async def test_get_resolves_record_then_calls_edd_only_for_it():
    session = _session({"data": {"valid_tno": [active_sample(CODE)]}}, {"data": [{"tno": CODE, "delivery_estimate": None}]})
    assert (await UniUniApiClient(session).async_get_parcel(CODE))["tno"] == CODE
    assert session.get.call_args.kwargs["params"]["id"] == CODE
    assert session.post.call_args.kwargs["json"]["tnos"] == [CODE]


async def test_unknown_has_no_edd_request():
    session = _session({"data": {"valid_tno": []}})
    assert await UniUniApiClient(session).async_get_parcel(CODE) is None
    session.post.assert_not_called()


async def test_multiple_records_warns_once_and_uses_the_first(caplog):
    api_module._multiplicity_warning_logged = False
    session = _session(
        {"data": {"valid_tno": [active_sample(CODE), active_sample("UUS-SYNTHETIC-EXTRA")]}},
        {"data": [{"tno": CODE, "delivery_estimate": None}]},
    )
    record = await UniUniApiClient(session).async_get_parcel(CODE)
    assert record["tno"] == CODE
    session2 = _session(
        {"data": {"valid_tno": [active_sample(CODE), active_sample("UUS-SYNTHETIC-EXTRA")]}},
        {"data": [{"tno": CODE, "delivery_estimate": None}]},
    )
    await UniUniApiClient(session2).async_get_parcel(CODE)
    assert caplog.text.count("record_count=2") == 1
    assert "issues/new" in caplog.text
    api_module._multiplicity_warning_logged = False


async def test_single_record_does_not_warn_multiplicity():
    api_module._multiplicity_warning_logged = False
    session = _session({"data": {"valid_tno": [active_sample(CODE)]}}, {"data": [{"tno": CODE, "delivery_estimate": None}]})
    await UniUniApiClient(session).async_get_parcel(CODE)
    assert api_module._multiplicity_warning_logged is False


@pytest.mark.parametrize("body", [[], {"data": {}}, {"data": {"valid_tno": {}}}])
async def test_unexpected_tracking_envelope_raises(body):
    with pytest.raises(UniUniApiError):
        await UniUniApiClient(_session(body)).async_get_parcel(CODE)


async def test_key_errors_warn_without_key(caplog):
    with pytest.raises(UniUniApiError):
        await UniUniApiClient(_session({"error": "invalid key"}, get_status=400)).async_get_parcel(CODE)
    assert "tracking key" in caplog.text
    assert "SMq" not in caplog.text


async def test_edd_key_and_envelope_errors_raise():
    client = UniUniApiClient(_session({"data": {"valid_tno": [active_sample(CODE)]}}, {"error": "invalid key"}, edd_status=400))
    with pytest.raises(UniUniApiError):
        await client.async_get_parcel(CODE)
    client = UniUniApiClient(_session({"data": {"valid_tno": [active_sample(CODE)]}}, {"data": {}}))
    with pytest.raises(UniUniApiError):
        await client.async_get_parcel(CODE)


async def test_populated_edd_warns_shape_not_value(caplog):
    session = _session({"data": {"valid_tno": [active_sample(CODE)]}}, {"data": [{"delivery_estimate": "private date"}]})
    await UniUniApiClient(session).async_get_parcel(CODE)
    assert "type=str" in caplog.text
    assert "private date" not in caplog.text


async def test_tracking_429_carries_status_and_retry_after():
    session = _session({}, get_status=429)
    session.get.return_value.__aenter__.return_value.headers = {"Retry-After": "30"}
    with pytest.raises(UniUniApiError) as excinfo:
        await UniUniApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 30


async def test_edd_429_carries_status_and_retry_after():
    session = _session({"data": {"valid_tno": [active_sample(CODE)]}}, edd_status=429)
    session.post.return_value.__aenter__.return_value.headers = {}
    with pytest.raises(UniUniApiError) as excinfo:
        await UniUniApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None


async def test_retry_after_falls_back_to_none_on_http_date():
    session = _session({}, get_status=429)
    session.get.return_value.__aenter__.return_value.headers = {
        "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"
    }
    with pytest.raises(UniUniApiError) as excinfo:
        await UniUniApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.retry_after is None


async def test_http_json_and_network_failures_raise_or_propagate():
    for status, body in ((500, {}), (200, "not json")):
        with pytest.raises(UniUniApiError):
            await UniUniApiClient(_session(body, get_status=status)).async_get_parcel(CODE)
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientError("boom")
    with pytest.raises(aiohttp.ClientError):
        await UniUniApiClient(session).async_get_parcel(CODE)
