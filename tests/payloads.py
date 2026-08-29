"""Synthetic, privacy-safe UniUni payloads used by tests."""
from __future__ import annotations

ACTIVE_CODE = "UUS-SYNTHETIC-ACTIVE"
DELIVERED_CODE = "UUS-SYNTHETIC-DELIVERED"


def event(state: int, seq: int, timestamp: int | None, text: str = "Moved") -> dict:
    """Return a minimal source event with settled timestamp metadata."""
    return {
        "state": state,
        "traceSeq": seq,
        "description_en": text,
        "pathInfo": "synthetic location",
        "dateTime": {
            "timezone": "America/Toronto",
            "offsetByGMT": "-04:00",
            "localTime": "synthetic",
            "ts": timestamp,
        },
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """Return a resolved delivered record with deliberately scrambled history."""
    return {
        "tno": code,
        "state": 203,
        "country": "CA",
        "estimate_time": "2026-08-30",
        "partner_id": "redacted",
        "spath_list": [
            event(203, 3, 1788206400, "Delivered"),
            event(199, 2, 1788120000, "In transit"),
            event(190, 1, 1788033600, "Registered"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """Return an out-for-delivery record."""
    sample = delivered_sample(code)
    sample.update({"state": 202, "spath_list": sample["spath_list"][:-1]})
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """Return a pickup-status record without an exposed location."""
    sample = active_sample(code)
    sample["state"] = 214
    return sample
