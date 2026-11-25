"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific :data:`_STATUS_MAP` and :func:`normalize_parcel` are kept
here. Everything else — the
timestamp parsing, the history builder, the sort contract, the delivered
filter, the one-shot warning for unmapped statuses — is suite-wide machinery
and should be left alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-uniuni/issues/new"
    "?template=unrecognised_status.yml"
)

# The numeric source states map onto the canonical enum. The values must come from the
# canonical enum — never invent a new one. Prefer mapping too little over
# mapping wrongly: an unmapped value surfaces as ``unknown`` plus a one-shot
# warning that asks the user to report it, which is how the map grows.
_STATUS_MAP: dict[str, ParcelStatus] = {
    **{str(code): ParcelStatus.REGISTERED for code in (190, 223)},
    **{
        str(code): ParcelStatus.IN_TRANSIT
        for code in (199, 200, 218, 221, 225, 229, 4010, 1910, 195, 204, 217, 255)
    },
    **{str(code): ParcelStatus.OUT_FOR_DELIVERY for code in (202, 220)},
    **{str(code): ParcelStatus.DELIVERED for code in (203, 216, 228)},
    **{str(code): ParcelStatus.AT_PICKUP_POINT for code in (214, 226)},
    **{str(code): ParcelStatus.RETURNING for code in (211, 215, 230, 233, 234)},
    **{
        str(code): ParcelStatus.PROBLEM
        for code in (
            192,
            198,
            1870,
            206,
            207,
            209,
            212,
            213,
            219,
            222,
            224,
            231,
            232,
            235,
        )
    },
}

# Keys already warned about, so each unconfirmed shape is logged only once
# per HA session instead of on every poll.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    _warn_once(
        f"status:{code}",
        "Unrecognised UniUni status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _warn_timestamp_shape(metadata: Any) -> None:
    """Warn once when a history event's dateTime has no usable ``ts`` at all.

    Structure only — the ``dateTime`` dict's keys, never its values (a
    ``localTime`` string could conceivably carry a recipient-adjacent detail).
    """
    _warn_once(
        "timestamp-shape",
        "A UniUni history event's dateTime has no usable ts — the event is "
        "dropped rather than risk an unanchored timestamp. Open an issue and "
        "paste this line: %s\n  dateTime keys=%s",
        NEW_ISSUE_URL,
        sorted(metadata) if isinstance(metadata, dict) else type(metadata).__name__,
    )


def _warn_status_event_disagreement(
    status: ParcelStatus, event_status: ParcelStatus
) -> None:
    """Warn once when the top-level state disagrees with the latest history event."""
    _warn_once(
        "status-event-disagreement",
        "UniUni's top-level state disagrees with its latest history event — "
        "help us confirm which one is authoritative. Open an issue and paste "
        "this line: %s\n  state=%s event=%s",
        NEW_ISSUE_URL,
        status,
        event_status,
    )


def map_parcel_status(code: str | int | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if code is None or code == "":
        return ParcelStatus.UNKNOWN
    code = str(code)
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | int | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if code is None or code == "":
        return None
    code = str(code)
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for a ``dateTime.ts`` value.

    UniUni's ``ts`` is confirmed **epoch seconds** — verified by comparing a
    real event's ``ts`` against its own ``localTime``/``offsetByGMT`` pair
    (``localTime == ts + offsetByGMT``). Strings pass through untouched;
    their consumers are guarded by :func:`parse_iso`.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


def _resolve_zone(
    name: str, zones: Mapping[str, tzinfo | None] | None
) -> tzinfo | None:
    """Return the zone for ``name``, or ``None`` when it is unknown.

    The coordinator resolves zones off the event loop and passes them in;
    ``zones=None`` (direct callers, tests) falls back to ``ZoneInfo``.
    """
    if zones is not None:
        return zones.get(name)
    try:
        # suite: allow zoneinfo (only when the caller passes no zones; the coordinator does)
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def zone_names(raw: dict) -> set[str]:
    """Return the IANA zone names a parcel's events carry, for the coordinator to resolve."""
    names: set[str] = set()
    for event in raw.get("spath_list") or []:
        metadata = event.get("dateTime") if isinstance(event, dict) else None
        if isinstance(metadata, dict) and isinstance(metadata.get("timezone"), str):
            names.add(metadata["timezone"])
    return names


def _local_time_timestamp(
    metadata: Any, zones: Mapping[str, tzinfo | None] | None = None
) -> str | None:
    """Return an ISO timestamp from ``localTime`` + ``timezone``, or ``None``.

    UniUni drops ``ts`` on the tail end of some parcels and sends only the
    carrier's own wall-clock time and zone name. Those two are an exact
    anchor, and without this the newest events — the delivery among them —
    were discarded, which both truncated the history and made the top-level
    state look like it disagreed with it.
    """
    if not isinstance(metadata, dict):
        return None
    local_time = metadata.get("localTime")
    zone_name = metadata.get("timezone")
    if not isinstance(local_time, str) or not isinstance(zone_name, str):
        return None
    try:
        parsed = datetime.fromisoformat(local_time)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    zone = _resolve_zone(zone_name, zones)
    return parsed.replace(tzinfo=zone).isoformat() if zone is not None else None


def build_history(
    events: list | None,
    *,
    max_events: int = HISTORY_MAX_EVENTS,
    zones: Mapping[str, tzinfo | None] | None = None,
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    UniUni's settled event fields are ``state``, ``description_en`` and
    ``dateTime.ts``. The pre-network "Order received" event consistently
    carries a null ``timezone``/``offsetByGMT`` — confirmed on every real
    parcel seen so far — but ``ts`` alone is still a valid UTC anchor, so it
    is used directly rather than dropping the event.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        metadata = event.get("dateTime")
        timestamp = (
            to_iso_timestamp(metadata.get("ts")) if isinstance(metadata, dict) else None
        )
        if not timestamp:
            timestamp = _local_time_timestamp(metadata, zones)
        if not timestamp:
            _warn_timestamp_shape(metadata)
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event.get("state")),
            "raw_status": event.get("description_en") or str(event.get("state") or ""),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code or TRACKING_URL is None:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(
    raw: dict,
    *,
    include_history: bool = False,
    zones: Mapping[str, tzinfo | None] | None = None,
) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are
    the contract**: every carrier in the suite returns exactly these, in this
    order, and the aggregator and cross-carrier dashboards depend on it. Set a
    key to ``None`` when the carrier does not expose it — never omit it.

    Rules worth keeping when you rewrite the body:

    * ``status`` is canonical, ``raw_status`` is the carrier's own text.
    * A delivered parcel has ``delivered_at`` set and ``planned_from`` /
      ``planned_to`` cleared — the ETA is meaningless once it has arrived.
    * ``planned_from`` / ``planned_to`` come from :func:`planned_window` and are
      emptied by :func:`planned_for_status`.
    * ``pickup`` comes from :func:`is_pickup` and stays ``True`` after collection.
    * ``weight`` is kilograms, ``dimensions`` centimetres (see
      :func:`format_dimensions`).
    * ``history`` is ``None`` when the option is off — the key still exists.
    """
    tracking_code = raw.get("tno")
    status_code = raw.get("state")
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED

    history_entries = build_history(raw.get("spath_list"), zones=zones)
    # The carrier exposes no separate delivery timestamp; its delivered event's
    # ``dateTime.ts`` is the only anchor, confirmed as epoch seconds.
    delivered_at = None
    if delivered:
        delivered_at = next(
            (
                entry["timestamp"]
                for entry in reversed(history_entries)
                if entry["status"] is ParcelStatus.DELIVERED
            ),
            None,
        )
    if history_entries:
        latest_event_status = history_entries[-1]["status"]
        if latest_event_status is not None and latest_event_status is not status:
            _warn_status_event_disagreement(status, latest_event_status)

    planned_from, planned_to = planned_for_status(
        status, *planned_window(None, None, zone=dt_util.get_default_time_zone())
    )
    pickup = is_pickup(
        None,
        status,
        (
            map_event_status(event.get("state"))
            for event in raw.get("spath_list") or []
            if isinstance(event, dict)
        ),
    )

    return {
        "carrier": "UniUni",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "contents": None,
        "status": status,
        "raw_status": str(status_code) if status_code is not None else None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": history_entries if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
