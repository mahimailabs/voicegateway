"""Tests for the pure-Python percentile helper.

The monitoring dashboards read these numbers directly, so linear-
interpolation correctness matters. Cross-checks selected cases against
``statistics.quantiles(method='inclusive')`` (which uses the same
definition).
"""

from __future__ import annotations

import statistics

import pytest

from voicegateway.storage._percentiles import (
    compute_percentiles,
    quantile_label,
)


def test_empty_input_returns_none_for_each_key():
    out = compute_percentiles([], [50.0, 95.0, 99.0])
    assert out == {"p50": None, "p95": None, "p99": None}


def test_single_value_repeats_for_each_percentile():
    out = compute_percentiles([42.0], [50.0, 95.0, 99.0])
    assert out == {"p50": 42.0, "p95": 42.0, "p99": 42.0}


def test_two_values_midpoint_for_p50():
    out = compute_percentiles([10.0, 20.0], [50.0])
    # Linear interpolation between the two samples at k=0.5.
    assert out["p50"] == pytest.approx(15.0)


def test_p0_and_p100_are_min_and_max():
    out = compute_percentiles([1.0, 2.0, 3.0, 4.0, 5.0], [0.0, 100.0])
    assert out["p0"] == 1.0
    assert out["p100"] == 5.0


def test_matches_numpy_linear_interpretation_for_sequence():
    """For [1..100], p50 == 50.5, p95 == 95.05, p99 == 99.01."""
    values = list(range(1, 101))
    out = compute_percentiles([float(v) for v in values], [50.0, 95.0, 99.0])
    assert out["p50"] == pytest.approx(50.5)
    assert out["p95"] == pytest.approx(95.05)
    assert out["p99"] == pytest.approx(99.01)


def test_matches_stdlib_inclusive_quantile():
    """statistics.quantiles uses the same linear-interpolation method."""
    values = [3.0, 7.0, 11.0, 13.0, 17.0, 19.0, 23.0]
    out = compute_percentiles(values, [25.0, 50.0, 75.0])
    # statistics.quantiles(n=4, method='inclusive') returns the 3 inner
    # quartiles — at p=25, 50, 75.
    q = statistics.quantiles(values, n=4, method="inclusive")
    assert out["p25"] == pytest.approx(q[0])
    assert out["p50"] == pytest.approx(q[1])
    assert out["p75"] == pytest.approx(q[2])


def test_monotonicity_within_bucket():
    """Higher percentiles must be >= lower percentiles for the same sample."""
    values = [5.0, 1.0, 9.0, 3.0, 7.0, 2.0, 8.0, 4.0, 6.0]
    out = compute_percentiles(values, [50.0, 75.0, 95.0, 99.0])
    assert out["p50"] <= out["p75"] <= out["p95"] <= out["p99"]


def test_out_of_range_percentiles_clamp():
    out = compute_percentiles([1.0, 2.0, 3.0], [-10.0, 150.0])
    assert out["p-10"] == 1.0
    assert out["p150"] == 3.0


def test_fractional_percentile_preserves_decimal_in_key():
    """99.9 is a distinct percentile from 99 and must map to a distinct key."""
    out = compute_percentiles([1.0, 2.0, 3.0, 4.0], [99.0, 99.9])
    assert "p99" in out
    assert "p99_9" in out
    assert out["p99"] is not None
    assert out["p99_9"] is not None


def test_integer_and_float_integer_collide():
    """99 and 99.0 both normalize to p99 — deliberately."""
    with pytest.raises(ValueError, match="Duplicate"):
        compute_percentiles([1.0, 2.0], [99, 99.0])


def test_quantile_label_format():
    assert quantile_label(50) == "0.5"
    assert quantile_label(95) == "0.95"
    assert quantile_label(99) == "0.99"
    assert quantile_label(99.9) == "0.999"
