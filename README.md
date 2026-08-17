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

Requires **Home Assistant 2026.8.0 or newer**

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

## Activities (best time to go outside)

Each activity is a subentry (Settings → Weather Plus → Add activity) and emits
`sensor.<activity>_best_time` and `sensor.<activity>_best_temperature`. Morning and evening
walks are separate activities with independent settings.

The search runs as staged passes, each of which **rolls back** rather than returning nothing —
a walk with imperfect light beats no walk at all:

1. **Time** (always) — bounds the search to the daily `[start hour, end hour)` window, at or
   after now, on the earliest day that still has forecast coverage. The hourly forecast is
   linearly interpolated onto a 10-minute grid, so answers land on times like `7:30`.
2. **Temperature** (optional) — keeps only moments whose temperature stays inside
   `[min, max]`. Rolled back if nothing qualifies.
3. **Elevation** (optional) — keeps only moments whose sun elevation stays at or below the cap
   (default `15°`), computed from your Home Assistant latitude/longitude via `astral`. Rolled
   back to the temperature pass's survivors if nothing qualifies.

The winner is the surviving moment closest to the configured **ideal temperature**, ties going
to the earliest. Every pass tests a **20-minute buffer** (`t`, `t+10`, `t+20`), not just the
starting instant — a walk that begins under the caps should not run into a hotter or higher sun
partway through.

`sensor.<activity>_best_time` carries `sun_elevation` and `rolled_back` attributes, so a
compromised answer is distinguishable from one that satisfied every constraint.

### Holding today's pick

The search only looks forward, so a chosen moment would otherwise decay into "best *remaining*
time" as its window elapsed — `07:20 @ 70°F` becoming `08:50 @ 79°F` by mid-morning, with a
restart making the jump visible all at once. Once a moment has been chosen for the current local
day it is held for the rest of that day; while it is still upcoming it keeps tracking forecast
updates, and it is released when the day rolls over.

`sensor.<activity>_best_time` restores its pick across restarts using Home Assistant's
restore-state (not the recorder, which can be disabled or purged), so a redeploy after the walk
has happened still reports the moment that was actually best. The `is_past` attribute
distinguishes a held moment from an upcoming one.

### Retuning thresholds at runtime

The range that suits a walk in January is not the one that suits July, so each activity also
emits `number` entities. Changing one takes effect immediately, survives restarts, and does not
require editing the subentry — so an automation can retune the season:

- `number.<activity>_minimum_temperature`
- `number.<activity>_maximum_temperature`
- `number.<activity>_maximum_sun_elevation`

```yaml
automation:
  - alias: Summer walk range
    triggers:
      - trigger: calendar  # or any seasonal trigger you prefer
    actions:
      - action: number.set_value
        target:
          entity_id: number.morning_walk_maximum_temperature
        data:
          value: 68
```

The subentry values are the starting point; a number set here overrides it. Because the
temperature numbers carry `device_class: temperature`, Home Assistant renders and accepts them
in your configured unit system, converting to the weather entity's unit internally.

| Activity field | Default | Notes |
|----------------|---------|-------|
| Start hour | `6` | Local-time hour, inclusive |
| End hour | `9` | Local-time hour, exclusive |
| Focus on temperature | `true` | Enables pass 2 |
| Minimum / maximum temperature | `60` / `75` | In the source entity's unit |
| Focus on sun elevation | `true` | Enables pass 3 |
| Maximum sun elevation | `15` | Degrees above the horizon; lower means softer light |
