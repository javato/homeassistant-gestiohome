# GestioHome for Home Assistant

A Home Assistant integration that reads community-metered supplies from **GestioHome** (Usanca Metering Solutions) and brings them into the **Energy dashboard on their real dates**:

| Supply | Unit | Energy dashboard |
|---|---|---|
| Domestic hot water | m³ | Water |
| Heating (e.g. underfloor heating) | kWh | Gas (thermal energy) |
| Cooling | kWh | Gas (thermal energy) |

It follows the same approach as [e-data](https://github.com/uvejota/homeassistant-edata) does for electricity: instead of a sensor that Home Assistant records when it reads it, it imports **external statistics** dated with each consumption.

## Why not a plain sensor

GestioHome publishes readings late (about one a day, at varying times). With a plain sensor, Home Assistant would book each consumption at the time the website was read rather than when it happened, and the website's month-to-date figure resets every month. This integration:

- Reads the **absolute meter reading** with its exact date and time, which never resets.
- Writes **one row per day** (at local midnight) to `gestiohome:<id>_dhw`, `gestiohome:<id>_heating` and `gestiohome:<id>_cooling`, with a running total that carries on across months.
- Gives each day what the meter counted since the previous reading, **exactly as the website does**, so daily and monthly totals match GestioHome.
- Imports **the whole history** GestioHome holds the first time.

Unlike Datadis (electricity), GestioHome does not provide hourly consumption, only about one reading a day. That is why the statistics are daily: the hourly view of the Energy dashboard shows each day's consumption at midnight; use the day, week or month views.

## Installation

From the Home Assistant **Marketplace** (formerly HACS):

1. Settings → Marketplace → ⋮ menu → *Custom repositories*.
2. Add `https://github.com/javato/homeassistant-gestiohome` as an *Integration*.
3. Search for **GestioHome**, install it and restart Home Assistant.

## Configuration

Settings → Devices & services → Add integration → **GestioHome**:

- **GestioHome address**: your building's address, e.g. `https://mybuilding.gestiohome.com` (pasting the login page address works too).
- **Username** and **password** of the GestioHome website.

The integration finds the dwelling and its meters on its own. If GestioHome stops accepting the password, Home Assistant asks for the new one.

### Energy dashboard

Settings → Dashboards → Energy:

- **Water**: add `gestiohome:<id>_dhw`.
- **Gas**: add `gestiohome:<id>_heating` (and `gestiohome:<id>_cooling` if you use it).

This keeps them separate from electricity. `<id>` is the last characters of your dwelling's app code; the *Supply information* sensor lists the statistics created.

## Entities

| Entity | Shows |
|---|---|
| Supply information | Time of the latest reading, dwelling and statistics created |
| Meter reading (hot water, heating, cooling) | Latest absolute meter reading |
| Last recorded consumption | Consumption of the last day with a reading |
| Consumption this month / last month | The same totals the website shows |
| Repair Home Assistant statistics (button) | Clears the integration's statistics and imports them again in full |
| Force sync (button) | Asks GestioHome again right away, meters included |

The sensors deliberately have no *state class*: they are for display only and do not create statistics stamped with the time they were read. For charts, use the `gestiohome:...` statistics.

## How it works

- It logs in to the website like a browser and uses the website's own data endpoint (`getStatisticData.php`).
- It checks hourly, but skips the website while it holds a reading less than 6 hours old.
- Every sync rewrites the last 31 days: a reading that reaches GestioHome late, even for a past day, lands on its real day.
- If a meter reading goes down (meter replaced or reset), nothing is subtracted and the new value becomes the baseline.

## License

MIT
