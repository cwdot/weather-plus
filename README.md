# Weather Plus

Home Assistant custom integration that wraps a source `weather` entity's hourly forecast and exposes
six aggregate sensors for the current calendar day:

- `sensor.<name>_day_high` / `_day_low` — across the full day
- `sensor.<name>_daytime_high` / `_daytime_low` — within configured daytime hours
- `sensor.<name>_night_high` / `_night_low` — outside daytime hours

## Install via HACS

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/cwdot/weather-plus` as type **Integration**
3. Install **Weather Plus**, restart Home Assistant
4. Settings → Devices & Services → Add Integration → **Weather Plus**

## Configuration

| Field | Default | Notes |
|-------|---------|-------|
| Source weather entity | — | Any `weather.*` entity that supports the `get_forecasts` service |
| Daytime start hour | `6` | Local-time hour, inclusive |
| Daytime end hour | `20` | Local-time hour, exclusive |
| Update interval (min) | `30` | How often to re-fetch the forecast |

## How it works

On each refresh, the coordinator calls `weather.get_forecasts` (`type: hourly`) on the source
entity, filters the returned points to today's calendar date in the local timezone, and computes
min/max temperatures for each window. Sensors inherit the source entity's `temperature_unit`.
