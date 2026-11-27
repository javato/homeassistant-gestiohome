# GestioHome for Home Assistant

A Home Assistant integration that reads community-metered supplies from **GestioHome** (Usanca Metering Solutions), such as domestic hot water, heating or cooling, and brings them into the **Energy dashboard on their real dates**.

It follows the same approach as [e-data](https://github.com/uvejota/homeassistant-edata) does for electricity: instead of a sensor that Home Assistant records when it reads it, it imports **external statistics** dated with each reading.

## Why not a plain sensor

GestioHome publishes readings late (about one a day, at varying times). With a plain sensor, Home Assistant would book each consumption at the time the website was read rather than when it happened, and the website's month-to-date figure resets every month. This integration:

- Reads the **absolute meter reading** with its exact date and time, which never resets.
- Writes each reading **in the hour it was taken**, with what the meter counted since the previous reading and a running total that carries on across months. That is what Home Assistant does with a meter that reports once a day.
- Adds up days and months **exactly as the website does**.
- Imports **the whole history** GestioHome holds the first time.

GestioHome gives about one reading a day, not hourly consumption like Datadis: in the hourly view of the Energy dashboard, each reading shows in its hour with everything used since the previous one. Use the day, week or month views for consumption over time.

## Installation

From the Home Assistant **Marketplace** (formerly HACS):

1. Settings → Marketplace → ⋮ menu → *Custom repositories*.
2. Add `https://github.com/javato/homeassistant-gestiohome` as an *Integration*.
3. Search for **GestioHome**, install it and restart Home Assistant.

## Configuration

Settings → Devices & services → Add integration → **GestioHome**:

1. **GestioHome address** (your building's address, e.g. `https://mybuilding.gestiohome.com`; pasting the login page address works too), **username** and **password** of the GestioHome website.
2. **Meters**: the integration lists the meters GestioHome shows on its tabs (Calor, Frío, ACS or whatever your building has). Choose the ones to add.
3. **Names**: give each meter a name; the tab's name is suggested.

If GestioHome stops accepting the password, Home Assistant asks for the new one.

### Meters and statistics

A meter is recognised **only by its GestioHome number**. Each one gets a statistic `gestiohome:<id>_<name>_<number>`, e.g. `gestiohome:abcd_calor_23021`, created when the meter is added and never changed afterwards, even if you rename the meter or GestioHome renames its tab. `<id>` is the last characters of your dwelling's app code.

To add, remove or rename meters later: Settings → Devices & services → GestioHome → **Configure**. Removing a meter removes its sensors; its statistic is kept.

Once a day the integration looks at the tabs again and tells you in **Settings → Repairs** when a meter you added is no longer listed, or a new one appears. Nothing changes on its own. A meter with a new number is a new meter: if a meter is replaced, add the new one. Keep the old statistic in the Energy dashboard if the new meter starts from zero, or remove it if the new one carries the whole history.

### Energy dashboard

Settings → Dashboards → Energy: add each statistic where it belongs, for example hot water under **Water** and heating or cooling (kWh) under **Gas**. This keeps them separate from electricity. The *Supply information* sensor lists the statistics.

The unit of each meter is the one GestioHome gives for it (kWh, m³, litres…).

## Entities

| Entity | Shows |
|---|---|
| Supply information | Time of the latest reading, dwelling and statistics |
| Meter reading *name* | Latest absolute meter reading |
| Last recorded consumption *name* | Consumption of the last day with a reading |
| Consumption this month / last month *name* | The same totals the website shows |
| Repair Home Assistant statistics (button) | Rebuilds each meter's statistic from its whole history. A statistic is only cleared once GestioHome has given the readings to build it again |
| Force sync (button) | Asks GestioHome again right away, and looks at its tabs again |

The sensors deliberately have no *state class*: they are for display only and do not create statistics stamped with the time they were read. For charts, use the `gestiohome:...` statistics.

## How it works

- It logs in to the website like a browser, finds the meters in the tabs of its menu and reads them through the website's own data endpoint (`getStatisticData.php`, one request per meter).
- It checks hourly, but leaves the website alone for 20 hours after a new reading (readings are usually 24 hours apart).
- Every sync rewrites the last 31 days: a reading that reaches GestioHome late, even for a past day, lands in its real hour.
- If a meter reading drops by more than 10 % (meter replaced or reset), nothing is subtracted and the new value becomes the baseline. A smaller drop is a correction of the meter and is subtracted, as the website does.

### Upgrading from 2026.11.1 or earlier

Those versions had fixed supplies. After updating, remove the integration and add it again to choose its meters: it imports the whole history again into the new statistics (`gestiohome:<id>_<name>_<number>`). Switch the Energy dashboard to them; the old ones (`gestiohome:<id>_heating`…) are kept when the integration is removed and can be deleted in Developer tools → Statistics.

## License

MIT
