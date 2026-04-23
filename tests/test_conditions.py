"""Pure unit tests for the condition evaluators."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.weather_plus.conditions import (
    CONDITION_SPECS,
    ForecastPoint,
    evaluate,
)

_NOW = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
_SPECS_BY_KEY = {spec.key: spec for spec in CONDITION_SPECS}


def _point(
    offset_hours: float,
    *,
    condition: str | None = None,
    temp: float | None = None,
) -> ForecastPoint:
    return ForecastPoint(
        when=_NOW + timedelta(hours=offset_hours),
        temperature=temp,
        condition=condition,
    )


def test_today_rain_triggers_when_any_point_in_24h_window_rains():
    points = [
        _point(5, condition="cloudy"),
        _point(10, condition="pouring"),
        _point(48, condition="rainy"),
    ]
    assert evaluate(_SPECS_BY_KEY["today_rain"], points, _NOW, 65, 80) is True


def test_today_rain_off_when_only_outside_window():
    # rainy at +30h is outside the 24h window
    points = [_point(0.5, condition="cloudy"), _point(30, condition="pouring")]
    assert evaluate(_SPECS_BY_KEY["today_rain"], points, _NOW, 65, 80) is False


def test_today_severe_matches_lightning_and_hail():
    points = [_point(2, condition="hail")]
    assert evaluate(_SPECS_BY_KEY["today_severe"], points, _NOW, 65, 80) is True
    points = [_point(2, condition="rainy")]
    assert evaluate(_SPECS_BY_KEY["today_severe"], points, _NOW, 65, 80) is False


def test_today_cold_uses_threshold():
    points = [_point(6, temp=66), _point(12, temp=64)]
    assert evaluate(_SPECS_BY_KEY["today_cold"], points, _NOW, 65, 80) is True
    # raise threshold so 64 no longer counts as below
    assert evaluate(_SPECS_BY_KEY["today_cold"], points, _NOW, 60, 80) is False


def test_today_hot_uses_threshold():
    points = [_point(6, temp=79), _point(12, temp=85)]
    assert evaluate(_SPECS_BY_KEY["today_hot"], points, _NOW, 65, 80) is True
    assert evaluate(_SPECS_BY_KEY["today_hot"], points, _NOW, 65, 90) is False


def test_hour_window_excludes_points_beyond_90_minutes():
    # exactly at the 1.5h boundary is excluded (half-open)
    points = [_point(1.5, condition="lightning")]
    assert evaluate(_SPECS_BY_KEY["hour_lightning"], points, _NOW, 65, 80) is False
    points = [_point(1.0, condition="lightning")]
    assert evaluate(_SPECS_BY_KEY["hour_lightning"], points, _NOW, 65, 80) is True


def test_past_points_are_ignored():
    points = [_point(-1, condition="pouring")]
    assert evaluate(_SPECS_BY_KEY["today_rain"], points, _NOW, 65, 80) is False


def test_missing_condition_does_not_match():
    points = [_point(2, condition=None, temp=70)]
    assert evaluate(_SPECS_BY_KEY["hour_rainy"], points, _NOW, 65, 80) is False


def test_missing_temperature_does_not_match_temperature_specs():
    points = [_point(2, condition="cloudy", temp=None)]
    assert evaluate(_SPECS_BY_KEY["today_cold"], points, _NOW, 65, 80) is False
    assert evaluate(_SPECS_BY_KEY["today_hot"], points, _NOW, 65, 80) is False


def test_all_specs_have_unique_keys_and_device_class():
    keys = [spec.key for spec in CONDITION_SPECS]
    assert len(keys) == len(set(keys))
    for spec in CONDITION_SPECS:
        assert spec.device_class is not None
