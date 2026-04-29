# Weather Plus

Home Assistant custom integration that wraps a source `weather` entity's hourly forecast and exposes
aggregate sensors for the current cycle (morningtime → next morningtime). Window aggregates
are scoped to the part of the day where the extreme is meaningful — e.g. `daytime_high` is
the afternoon peak, distinct from `todays_high` which can land in the morning if a cold front
passes through:

- `sensor.<name>_todays_high` / `_todays_low` — across the full cycle
- `sensor.<name>_morningtime_low` — pre-dawn cold (morningtime → daytime window)
- `sensor.<name>_daytime_high` — afternoon peak (daytime → nighttime window)
- `sensor.<name>_nighttime_low` — overnight cold (nighttime → next morningtime window)
- `sensor.<name>_morningtime` / `_daytime` / `_nighttime` — timestamps anchoring each window

## Install via HACS

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/cwdot/weather-plus` as type **Integration**
3. Install **Weather Plus**, restart Home Assistant
4. Settings → Devices & Services → Add Integration → **Weather Plus**

## Configuration

| Field | Default | Notes |
|-------|---------|-------|
| Source weather entity | — | Any `weather.*` entity that supports the `get_forecasts` service |
| Time anchors | Fixed hours | `Fixed hours` uses the values below; `Dawn / noon / dusk` reads `next_dawn` / `next_noon` / `next_dusk` from the sun entity |
| Morningtime hour | `6` | Local-time hour; must be `< daytime hour` |
| Daytime hour | `12` | Local-time hour; must be `< nighttime hour` |
| Nighttime hour | `20` | Local-time hour |
| Update interval (min) | `30` | How often to re-fetch the forecast |

## How it works

On each refresh, the coordinator calls `weather.get_forecasts` (`type: hourly`) on the source
entity, classifies each forecast point into the morningtime / daytime / nighttime window of the
current cycle, and computes min/max temperatures for each. The cycle starts at the most
recent passed morningtime — so the nighttime window naturally spans midnight into the next
calendar day. Sensors inherit the source entity's `temperature_unit`.
