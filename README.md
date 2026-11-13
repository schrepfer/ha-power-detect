# Power Detect (`power_detect`)

[![Marketplace](https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg)](https://www.home-assistant.io/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.11%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](custom_components/power_detect/manifest.json)
[![Validate](https://github.com/schrepfer/ha-power-detect/actions/workflows/validate.yml/badge.svg)](https://github.com/schrepfer/ha-power-detect/actions/workflows/validate.yml)

A lightweight Home Assistant custom integration that polls a [**`power-detect`**](https://github.com/schrepfer/power-detect) HTTP server (the Home Assistant equivalent of [`power-detect-monitor.sh`](https://github.com/schrepfer/power-detect/blob/main/TrueNAS/power-detect-monitor.sh)) to expose utility power state, fine-grained outage status, and automation events.

For each configured `power-detect` server, `power_detect` automatically provides:
* **`binary_sensor.<slug>`** (`device_class: power`) — **`on`** while mains power is present (`powered`), **`off`** during an outage (`battery` or `shutdown`), and **`unknown`** when the endpoint cannot be reached.
* **`sensor.<slug>`** (`device_class: enum`) — Reports the exact fine-grained status: **`powered`**, **`battery`**, **`shutdown`**, or **`unknown`**.
* **`power_detect` Bus Event** — Fired whenever status changes, carrying `status`, `previous_status`, `was_outage`, `outage_duration_seconds`, and server metadata in `event_data` (which Home Assistant's `trigger: event` can match directly).

---

## Features

* **Zero External Dependencies** — Uses Home Assistant's built-in async `aiohttp` session with a fast `3s` timeout (matching `curl --max-time 3` in `power-detect-monitor.sh`) and a quick single retry to absorb transient LAN packet drops.
* **Network-Blip Safe** — If the `power-detect` host reboots or briefly drops off the LAN, `sensor.<slug>` and `binary_sensor.<slug>` become `unknown` (rather than falsely flipping `off` as a power outage), and recovery sets `was_outage: false`.
* **Outage Duration Tracking** — Tracks when an outage (`battery` / `shutdown`) started in your Home Assistant server's local timezone and includes `outage_started` and `outage_duration_seconds` in `power_detect` events.
* **Clean Logging** — Logs connectivity loss once on failure and once on recovery without spamming Home Assistant logs during extended outages.
* **Single or Multi-Server** — Configure one `url:` directly under `power_detect:` or monitor multiple endpoints via `servers:`.

---

## Installation

You can install **Power Detect** on Home Assistant 2026.11+ using the built-in **Marketplace** or by copying the integration directory via `rsync` / `scp`.

### Method 1: Home Assistant Marketplace (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=schrepfer&repository=ha-power-detect&category=integration)

Or add it directly in Home Assistant:
1. Open **Settings → Marketplace** in Home Assistant.
2. Click the top-right menu (`⋮`) → **Add from link** (or **Custom repositories**) *(if prompted, link your GitHub account)*.
3. Paste `https://github.com/schrepfer/ha-power-detect`, select **Integration**, and click **Add**.
4. Open **Power Detect** in the Marketplace, click **Download** / **Install**, and **restart Home Assistant**.

---

### Method 2: Copy from Another Machine (`rsync` or `scp`)

> **Important:** Inside `<config>/custom_components/`, the directory **must** be named `power_detect` (matching `"domain": "power_detect"` in [`manifest.json`](custom_components/power_detect/manifest.json)).

```bash
git clone https://github.com/schrepfer/ha-power-detect.git
rsync -av --exclude='__pycache__' --exclude='*.pyc' \
  ha-power-detect/custom_components/power_detect \
  root@homeassistant.local:/config/custom_components/
```

Or with `scp`:
```bash
scp -r ha-power-detect/custom_components/power_detect root@homeassistant.local:/config/custom_components/
```

Then restart Home Assistant (**Developer Tools → YAML → Restart**).

---

## Configuration

Add the `power_detect:` block to your Home Assistant `configuration.yaml` file (YAML changes require a Home Assistant restart).

### Minimal Single-Server Example

```yaml
# configuration.yaml
power_detect:
  url: http://power.x.wabb.it:1999
```

This creates:
* **`binary_sensor.power_detect`** (`on` when powered, `off` when on `battery` or `shutdown`)
* **`sensor.power_detect`** (`powered`, `battery`, `shutdown`, or `unknown`)

### Multi-Server & Custom Options Example

```yaml
# configuration.yaml
power_detect:
  scan_interval: 10
  timeout: 3
  servers:
    - name: House Power
      url: http://power.x.wabb.it:1999          # -> binary_sensor.house_power & sensor.house_power

    - name: Workshop Power
      host: workshop-power.x.wabb.it
      port: 1999                                # -> binary_sensor.workshop_power & sensor.workshop_power
```

### Configuration Variables

You may configure either a **single server directly** under `power_detect:` or a **`servers:` list**:

| Key | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `url` | string | **Yes** *(or `host`)* | — | Full HTTP URL of the `power-detect` server (e.g., `http://power.x.wabb.it:1999`). |
| `host` | string | **Yes** *(or `url`)* | — | Hostname or IP address of the `power-detect` server (used with `port`). |
| `port` | integer | No | `1999` | HTTP port when using `host`. |
| `name` | string | No | `"Power Detect"` | Friendly entity display name. |
| `slug` | string | No | `"power_detect"` | Entity ID slug (`binary_sensor.<slug>` and `sensor.<slug>`). |
| `scan_interval` | integer / time | No | `10` (`00:00:10`) | Polling interval in seconds (or `HH:MM:SS`). |
| `timeout` | float | No | `3.0` | HTTP request timeout in seconds (`0.5` – `60.0`). |
| `servers` | list | No | — | List of server objects accepting the keys above when monitoring multiple endpoints. |

---

## Entities & State Attributes

| Entity | Device Class | States | Description |
| :--- | :--- | :--- | :--- |
| **`binary_sensor.<slug>`** | `power` | `on`, `off`, `unknown` | `on` when mains power is present (`powered`); `off` during an outage (`battery` or `shutdown`); `unknown` if unreachable. |
| **`sensor.<slug>`** | `enum` | `powered`, `battery`, `shutdown`, `unknown` | Exact fine-grained `power-detect` status. |

Both entities expose the following state attributes:

| Attribute | Example Value | Description |
| :--- | :--- | :--- |
| `name` | `"Power Detect"` | Configured server display name. |
| `url` | `"http://power.x.wabb.it:1999"` | Normalized `power-detect` endpoint URL. |
| `status` | `"battery"` | Current fine-grained status (`powered`, `battery`, `shutdown`, `unknown`). |
| `previous_status` | `"powered"` | Prior status before the most recent transition (`null` before first change). |
| `last_known_status` | `"battery"` | Most recent non-`unknown` status reported by the server. |
| `reachable` | `true` | `true` if the latest HTTP poll succeeded; `false` on timeout/connection error. |
| `last_checked` | `"2026-11-13T12:15:10-08:00"` | Local ISO-8601 timestamp of the latest poll. |
| `last_changed` | `"2026-11-13T12:10:00-08:00"` | Local ISO-8601 timestamp when `status` last changed. |
| `outage_started` | `"2026-11-13T12:10:00-08:00"` | Local ISO-8601 timestamp when the active outage began (`null` while `powered`). |
| `last_error` | `null` | Error message from the latest failed poll (`null` when reachable). |

---

## `power_detect` Event

Whenever a monitored server's status changes after initial startup, `power_detect` fires a **`power_detect`** event on the Home Assistant event bus.

Home Assistant's `trigger: event` natively filters on any subset of `event_data` (such as `status: battery`, `status: shutdown`, or `was_outage: true`), or you can omit `event_data` to handle all transitions in a single automation.

### Event Data Payload (`trigger.event.data`)

```yaml
name: "Power Detect"
slug: "power_detect"
entity_id: "sensor.power_detect"
binary_sensor_id: "binary_sensor.power_detect"
url: "http://power.x.wabb.it:1999"
status: "shutdown"
previous_status: "battery"
last_known_status: "battery"
power_present: false
reachable: true
was_outage: true
changed_at: "2026-11-13T12:15:00-08:00"
outage_started: "2026-11-13T12:10:00-08:00"
outage_duration_seconds: 300.0
```

---

## Usage Examples

### 1. Alert Immediately When House Switches to Battery Power

```yaml
automation:
  - alias: "Power Outage - Switched to Battery"
    description: Announces and pushes an alert as soon as mains power is lost
    triggers:
      - trigger: event
        event_type: power_detect
        event_data:
          status: battery
    actions:
      - action: notify.notify
        data:
          title: "⚡ Power Outage"
          message: "Utility power lost ({{ trigger.event.data.name }} is on battery)."
```

### 2. Critical Action When Grace Period Expires (`shutdown`)

```yaml
automation:
  - alias: "Power Outage - Shutdown Threshold Reached"
    description: Alerts when power-detect enters shutdown state after the grace period
    triggers:
      - trigger: event
        event_type: power_detect
        event_data:
          status: shutdown
    actions:
      - action: notify.notify
        data:
          title: "🛑 Power Critical (Shutdown)"
          message: >-
            Power has been out for {{ (trigger.event.data.outage_duration_seconds | int) // 60 }}m;
            servers are shutting down.
```

### 3. Notify When Utility Power Is Restored After an Outage

```yaml
automation:
  - alias: "Power Restored After Outage"
    description: Sends a recovery notification with total outage duration
    triggers:
      - trigger: event
        event_type: power_detect
        event_data:
          status: powered
          was_outage: true
    actions:
      - action: notify.notify
        data:
          title: "✅ Power Restored"
          message: >-
            Utility power restored after {{ (trigger.event.data.outage_duration_seconds | float(0)) | round(0) }}s.
```

### 4. Checking Power Status in Conditions & Templates

```jinja2
{# True when utility power is present: #}
{{ is_state('binary_sensor.power_detect', 'on') }}

{# True during any power outage (either 'battery' or 'shutdown'): #}
{{ is_state('binary_sensor.power_detect', 'off') }}

{# Fine-grained status ('powered', 'battery', 'shutdown', 'unknown'): #}
{{ states('sensor.power_detect') }}
```

---

## Troubleshooting & Local Testing

### Enable Debug Logging in Home Assistant

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.power_detect: debug
```

### Test Endpoints & Run Unit Tests Locally (No Home Assistant Required)

```bash
chmod +x scripts/preview_power_detect.py
python3 scripts/preview_power_detect.py http://power.x.wabb.it:1999
python3 -m unittest discover -s tests -v
```
