"""Test fixtures for weather_plus."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make HA load custom_components/weather_plus during tests."""
    yield
