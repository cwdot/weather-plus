"""Condition evaluators ported from palantir's weather plugin.

Each condition scans forecast points within a time window and resolves to
on/off based on whether any point in the window matches a predicate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

_RAINS = frozenset({"rainy", "lightning", "lightning-rainy", "pouring"})
_SEVERE = frozenset(
    {
        "hail",
        "lightning",
        "lightning-rainy",
        "snowy",
        "snow-rainy",
        "windy",
        "windy-variant",
        "exceptional",
    }
)
_CLEAR_NIGHT = frozenset({"clear-night"})
_CLOUDY = frozenset({"cloudy"})
_FOG = frozenset({"fog"})
_HAIL = frozenset({"hail"})
_LIGHTNING = frozenset({"lightning", "lightning-rainy"})
_PARTLY_CLOUDY = frozenset({"partlycloudy"})
_RAINY = frozenset({"rainy", "lightning", "lightning-rainy", "pouring"})
_SNOWY = frozenset({"snowy", "snow-rainy"})
_SUNNY = frozenset({"sunny"})
_WINDY = frozenset({"windy", "windy-variant"})
_EXCEPTIONAL = frozenset({"exceptional"})

_DAY_WINDOW = timedelta(hours=24)
_HOUR_WINDOW = timedelta(hours=1, minutes=30)


@dataclass(frozen=True)
class ForecastPoint:
    when: datetime
    temperature: float | None
    condition: str | None
    precipitation_probability: float | None = None


_PredicateFactory = Callable[[float, float], Callable[[ForecastPoint], bool]]


@dataclass(frozen=True)
class ConditionSpec:
    key: str
    name: str
    description: str
    device_class: BinarySensorDeviceClass | None
    window: timedelta
    predicate_factory: _PredicateFactory


def _condition_match(values: frozenset[str]) -> _PredicateFactory:
    def factory(_cold: float, _hot: float) -> Callable[[ForecastPoint], bool]:
        def predicate(point: ForecastPoint) -> bool:
            return point.condition is not None and point.condition in values

        return predicate

    return factory


def _temp_below(_cold: float, _hot: float) -> Callable[[ForecastPoint], bool]:
    def predicate(point: ForecastPoint) -> bool:
        return point.temperature is not None and point.temperature < _cold

    return predicate


def _temp_above(_cold: float, _hot: float) -> Callable[[ForecastPoint], bool]:
    def predicate(point: ForecastPoint) -> bool:
        return point.temperature is not None and point.temperature > _hot

    return predicate


def _temp_factory(below: bool) -> _PredicateFactory:
    return _temp_below if below else _temp_above


CONDITION_SPECS: tuple[ConditionSpec, ...] = (
    ConditionSpec(
        key="today_rain",
        name="Rain Today",
        description="It will rain in the next 24 hours",
        device_class=BinarySensorDeviceClass.MOISTURE,
        window=_DAY_WINDOW,
        predicate_factory=_condition_match(_RAINS),
    ),
    ConditionSpec(
        key="today_severe",
        name="Severe Weather Today",
        description="There will be severe weather in the next 24 hours",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_DAY_WINDOW,
        predicate_factory=_condition_match(_SEVERE),
    ),
    ConditionSpec(
        key="today_cold",
        name="Cold Today",
        description="It will be cold in the next 24 hours",
        device_class=BinarySensorDeviceClass.COLD,
        window=_DAY_WINDOW,
        predicate_factory=_temp_factory(below=True),
    ),
    ConditionSpec(
        key="today_hot",
        name="Hot Today",
        description="It will be hot in the next 24 hours",
        device_class=BinarySensorDeviceClass.HEAT,
        window=_DAY_WINDOW,
        predicate_factory=_temp_factory(below=False),
    ),
    ConditionSpec(
        key="hour_clear_night",
        name="Clear Night",
        description="The sky is clear during the night",
        device_class=BinarySensorDeviceClass.LIGHT,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_CLEAR_NIGHT),
    ),
    ConditionSpec(
        key="hour_cloudy",
        name="Cloudy",
        description="There are many clouds in the sky",
        device_class=BinarySensorDeviceClass.LIGHT,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_CLOUDY),
    ),
    ConditionSpec(
        key="hour_fog",
        name="Fog",
        description="There is a thick mist or fog reducing visibility",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_FOG),
    ),
    ConditionSpec(
        key="hour_hail",
        name="Hail",
        description="Hailstones are falling",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_HAIL),
    ),
    ConditionSpec(
        key="hour_lightning",
        name="Lightning",
        description="Lightning or thunderstorms are occurring",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_LIGHTNING),
    ),
    ConditionSpec(
        key="hour_partlycloudy",
        name="Partly Cloudy",
        description="The sky is partially covered with clouds",
        device_class=BinarySensorDeviceClass.LIGHT,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_PARTLY_CLOUDY),
    ),
    ConditionSpec(
        key="hour_rainy",
        name="Rainy",
        description="It is raining",
        device_class=BinarySensorDeviceClass.MOISTURE,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_RAINY),
    ),
    ConditionSpec(
        key="hour_snowy",
        name="Snowing",
        description="It is snowing",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_SNOWY),
    ),
    ConditionSpec(
        key="hour_sunny",
        name="Sunny",
        description="The sky is clear and the sun is shining",
        device_class=BinarySensorDeviceClass.LIGHT,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_SUNNY),
    ),
    ConditionSpec(
        key="hour_windy",
        name="Windy",
        description="It is windy",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_WINDY),
    ),
    ConditionSpec(
        key="hour_exceptional",
        name="Exceptional",
        description="Exceptional weather conditions are occurring",
        device_class=BinarySensorDeviceClass.SAFETY,
        window=_HOUR_WINDOW,
        predicate_factory=_condition_match(_EXCEPTIONAL),
    ),
)


def evaluate(
    spec: ConditionSpec,
    points: list[ForecastPoint],
    now: datetime,
    cold_threshold: float,
    hot_threshold: float,
) -> bool:
    """Return True if any point in [now, now + spec.window) matches the spec."""
    end = now + spec.window
    predicate = spec.predicate_factory(cold_threshold, hot_threshold)
    for point in points:
        if point.when < now or point.when >= end:
            continue
        if predicate(point):
            return True
    return False
