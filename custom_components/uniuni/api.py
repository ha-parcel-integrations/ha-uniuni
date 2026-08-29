"""UniUni public tracking client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import EDD_API_URL, EDD_WEB_KEY, TRACKING_API_URL, TRACKING_WEB_KEY

_LOGGER = logging.getLogger(__name__)
_key_warning_logged: set[str] = set()
_edd_shape_warning_logged = False
_multiplicity_warning_logged = False

# Where users report a broken assumption about this endpoint's contract.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-uniuni/issues/new"
    "?template=unrecognised_status.yml"
)


class UniUniApiError(Exception):
    """Raised for a carrier response which is neither data nor not-found."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store a safe failure summary, the status code and ``Retry-After``, if any."""
        super().__init__(f"UniUni API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def _warn_key_rejected(surface: str) -> None:
    """Warn once without leaking a transport key or tracking code."""
    if surface not in _key_warning_logged:
        _key_warning_logged.add(surface)
        _LOGGER.warning("UniUni rejected its public %s key; retaining prior parcel data", surface)


def _warn_multiple_records(count: int) -> None:
    """Warn once when a single tracking id resolves to more than one record."""
    global _multiplicity_warning_logged
    if _multiplicity_warning_logged:
        return
    _multiplicity_warning_logged = True
    _LOGGER.warning(
        "UniUni returned %d records for a single tracking id; only the "
        "first is used and the rest are discarded. Open an issue and paste "
        "this line: %s\n  record_count=%d",
        count,
        NEW_ISSUE_URL,
        count,
    )


class UniUniApiClient:
    """Fetch one consumer-tracked parcel and guarded EDD metadata."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with Home Assistant's shared session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Return the resolved record, or ``None`` for an unrecognised code."""
        params = {"id": tracking_code, "key": TRACKING_WEB_KEY, "source": "web"}
        async with self._session.get(TRACKING_API_URL, params=params) as response:
            if response.status == 429:
                raise UniUniApiError(
                    "HTTP 429",
                    status_code=429,
                    retry_after=self._retry_after(response),
                )
            payload = await self._json(response)
            if response.status == 400 and self._is_key_error(payload):
                _warn_key_rejected("tracking")
                raise UniUniApiError("tracking key rejected")
            if response.status != 200:
                raise UniUniApiError(f"HTTP {response.status}", status_code=response.status)

        records = self._records(payload)
        if records is None:
            raise UniUniApiError("unexpected tracking envelope")
        dict_records = [item for item in records if isinstance(item, dict)]
        if len(dict_records) > 1:
            _warn_multiple_records(len(dict_records))
        record = dict_records[0] if dict_records else None
        if record is None:
            return None
        await self._async_check_edd(str(record.get("tno") or tracking_code))
        return record

    async def _async_check_edd(self, tno: str) -> None:
        """Probe EDD only for a resolved record; do not publish unknown semantics."""
        global _edd_shape_warning_logged
        payload_data = {"key": EDD_WEB_KEY, "tnos": [tno]}
        async with self._session.post(EDD_API_URL, json=payload_data) as response:
            if response.status == 429:
                raise UniUniApiError(
                    "EDD HTTP 429",
                    status_code=429,
                    retry_after=self._retry_after(response),
                )
            payload = await self._json(response)
            if response.status == 400 and self._is_key_error(payload):
                _warn_key_rejected("EDD")
                raise UniUniApiError("EDD key rejected")
            if response.status != 200:
                raise UniUniApiError(
                    f"EDD HTTP {response.status}", status_code=response.status
                )
        if not isinstance(payload, dict):
            raise UniUniApiError("unexpected EDD envelope")
        data = payload.get("data")
        records = data if isinstance(data, list) else data.get("data") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise UniUniApiError("unexpected EDD envelope")
        for record in records:
            if not isinstance(record, dict) or record.get("delivery_estimate") is None:
                continue
            if not _edd_shape_warning_logged:
                _edd_shape_warning_logged = True
                _LOGGER.warning(
                    "UniUni returned a populated EDD field; ETA is withheld until "
                    "its shape is verified (type=%s, keys=%s). Open an issue and "
                    "paste this line: %s",
                    type(record["delivery_estimate"]).__name__, sorted(record), NEW_ISSUE_URL,
                )
            break

    @staticmethod
    async def _json(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except ValueError as err:
            raise UniUniApiError("unparseable body") from err

    @staticmethod
    def _records(payload: Any) -> list[Any] | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        records = data.get("valid_tno")
        return records if isinstance(records, list) else None

    @staticmethod
    def _is_key_error(payload: Any) -> bool:
        return isinstance(payload, dict) and "key" in str(payload).lower()

    @staticmethod
    def _retry_after(response: aiohttp.ClientResponse) -> float | None:
        """Parse ``Retry-After`` as seconds, or ``None`` for an HTTP-date/missing header."""
        header = response.headers.get("Retry-After")
        try:
            return float(header) if header else None
        except ValueError:
            return None
