"""Tests for the sensors."""

from __future__ import annotations

from custom_components.gestiohome.sensor import as_given


def test_values_keep_the_decimals_given() -> None:
    """Whole kWh show without decimals; m³ keep theirs."""
    assert as_given(12049.0) == 12049
    assert isinstance(as_given(12049.0), int)
    assert as_given(100.069) == 100.069
    assert as_given(None) is None
