# UniUni Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-uniuni.svg)](https://github.com/ha-parcel-integrations/ha-uniuni/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [UniUni](https://www.uniuni.com/tracking/) packages and parcels in Canada and the United States. No account is needed: enter the tracking code yourself.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of UniUni parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status and UniUni's numeric source status
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `uniuni.track_parcel` / `uniuni.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- An UniUni parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-uniuni` as an **Integration**.
3. Install **UniUni** and restart Home Assistant.

### Manual

Copy `custom_components/uniuni` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → UniUni**. There is nothing to fill in: the hub is created immediately (UniUni tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`uniuni.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery, stopped entirely once nothing is left to track) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → UniUni → ⋮ → Delete**. Nothing is stored on UniUni's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.uniuni_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.uniuni_awaiting_pickup` | Number of parcels waiting to be collected (`at_pickup_point`), full list under the `parcels` attribute |
| `sensor.uniuni_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.uniuni_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.uniuni_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.uniuni_last_successful_update` | Diagnostic: when UniUni was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

A **Deliveries** calendar entity is also created, showing expected delivery windows for active parcels — read-only, no extra API calls.

A **Refresh** button entity forces an immediate poll, without waiting for the next scheduled interval.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by UniUni |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a pickup location |
| `delivered` | Delivered |
| `returning` | Going back to the sender |
| `problem` | UniUni reports an exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the UniUni device):

| Event | When |
|---|---|
| `uniuni_parcel_registered` | A new parcel appears in the active list |
| `uniuni_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `uniuni_parcel_delivered` | A parcel is delivered |
| `uniuni_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `uniuni.track_parcel` | `tracking_code` | Start tracking a parcel |
| `uniuni.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.uniuni: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — UniUni has not scanned it yet, the code is wrong, or UniUni has introduced a source state that is not mapped yet.
- **A status logs "Unrecognised UniUni status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-uniuni/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the UniUni consumer website. It is not affiliated with, endorsed by, or supported by UniUni.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
