# Working in this repository

Home Assistant custom integration for **UniUni** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. See ha-dpd's or ha-gls's `const.py` for a live example |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

UniUni is code-based with no user credentials. The fixed public web keys are
transport constants only: never surface them in configuration, attributes or
diagnostics. A rejected key raises a carrier error, so the coordinator retains
its cached parcel data, and emits one redacted warning per key surface.

The EDD request follows only a resolved tracking result. Current EDD values are
null and the primary estimate is date-only, so all ETA fields deliberately stay
`None`. A first populated EDD logs only its type and top-level field names until
its timestamp semantics are captured. History is opt-in via the ``history``
key; `dateTime.ts` is confirmed **epoch seconds** (verified against a real
event's own `localTime`/`offsetByGMT`) and anchors a history entry on its own —
`timezone`/`offsetByGMT` being null is the normal, confirmed shape of the
pre-network "Order received" event on every real parcel seen so far, not a
reason to drop it. The complete carrier record is preserved in `raw`;
diagnostics redact its sensitive fields before export. Pickup status is
mapped, but no pickup location is exposed. API mechanics live in
`carrier-research/uniuni/api/`.

**Pre-1.0 WARNING obligations** (`parcels.py`'s `_warn_once`/`_warned`; `api.py`
keeps its own key/EDD/multiplicity flags): an unmapped status
(`_warn_unmapped_status`), a rejected public web key (`_warn_key_rejected`,
redacted, one per key surface), a first populated EDD field
(type/keys only, value withheld), a history event whose `dateTime.ts` isn't a
usable number at all (`_warn_timestamp_shape` — the event is dropped, only
its `dateTime` keys are logged; a null `timezone`/`offsetByGMT` alone no
longer triggers this), the top-level `state`
disagreeing with the latest history event's mapped status
(`_warn_status_event_disagreement` — status codes only, always checked
against `spath_list` even when the `history` option is off), and a tracking
id resolving to more than one record (`_warn_multiple_records` in `api.py` —
only the first is kept, the rest discarded). All fire once per HA session and
carry a copy-paste `issues/new?template=unrecognised_status.yml` link.

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Dynamic polling* — both the tracking **and** the EDD request can trigger the
429 backoff. UniUni has never actually been observed to 429; if it starts to
and the backoff isn't enough, record that here rather than adding a generator
flag.

## Running tests

```
python -m pytest tests/ --cov=custom_components.uniuni
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in this carrier's own directory in the private
`carrier-research/<slug>/api/`, never in this repo.
