"""Tests for the sensors."""

from __future__ import annotations

from datetime import datetime

from custom_components.gestiohome.api import Reading
from custom_components.gestiohome.coordinator import reading_decimals
from custom_components.gestiohome.sensor import as_given


def test_values_keep_the_decimals_given() -> None:
    """Whole kWh show without decimals; m³ keep theirs."""
    assert as_given(12049.0) == 12049
    assert isinstance(as_given(12049.0), int)
    assert as_given(100.069) == 100.069
    assert as_given(None) is None


def test_decimals_come_from_the_readings() -> None:
    """Whole kWh give 0 decimals; m³ readings give 3; never more than 3."""
    when = datetime(2026, 11, 1)
    assert reading_decimals([Reading(when, 12049.0), Reading(when, 12074.0)]) == 0
    assert reading_decimals([Reading(when, 100.069), Reading(when, 100.1)]) == 3
    assert reading_decimals([Reading(when, 1.23456)]) == 3
