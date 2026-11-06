"""Check environment cost integration and unknown-context behavior."""

import pytest

from runmap.paths.environment_join import EnvironmentInterval
from runmap.routing.environment_costs import (
    EnvironmentWeights,
    evaluate_environment_cost,
    sum_environment_costs,
    total_environment_penalty,
)


def _intervals() -> tuple[EnvironmentInterval, ...]:
    return (
        EnvironmentInterval("s", 0, 10, 9, (0.2, 0.4, 0.6, 0.1), (1, 1)),
        EnvironmentInterval("s", 10, 20, 9, (0.8, None, 0.0, 0.7), (1, 0)),
    )


def test_full_and_partial_costs_use_graph_chainage_and_support() -> None:
    weights = EnvironmentWeights(1, 1, 1, 1)
    full = evaluate_environment_cost(_intervals(), 0, 20, weights)
    assert (full.greenery_m, full.water_m, full.traffic_m, full.wetland_m) == pytest.approx((10, 16, 16, 8))
    assert full.unknown_graph_m == (0, 10, 10, 0)
    assert total_environment_penalty(full) == pytest.approx(50)
    partial = evaluate_environment_cost(_intervals(), 5, 15, weights)
    assert (partial.greenery_m, partial.water_m, partial.traffic_m, partial.wetland_m) == pytest.approx((5, 8, 8, 4))
    assert partial.unknown_graph_m == (0, 5, 5, 0)


def test_off_and_repeated_traversals() -> None:
    off = evaluate_environment_cost(_intervals(), 0, 20, EnvironmentWeights())
    assert (off.greenery_m, off.water_m, off.traffic_m, off.wetland_m) == (0, 0, 0, 0)
    assert total_environment_penalty(off) == 0
    assert off.unknown_graph_m == (0, 10, 10, 0)
    once = evaluate_environment_cost(_intervals(), 0, 20, EnvironmentWeights(traffic=0.5))
    twice = sum_environment_costs((once, once))
    assert twice.traffic_m == pytest.approx(16)
    assert twice.unknown_graph_m == (0, 20, 20, 0)


def test_known_zero_and_unsupported_zero_have_different_costs() -> None:
    values = (1.0, 1.0, 0.0, 0.0)
    known = (EnvironmentInterval("s", 0, 10, 9, values, (1, 1)),)
    unsupported = (EnvironmentInterval("s", 0, 10, 9, values, (0, 1)),)
    weights = EnvironmentWeights(1, 1, 1, 1)
    assert total_environment_penalty(evaluate_environment_cost(known, 0, 10, weights)) == 0
    result = evaluate_environment_cost(unsupported, 0, 10, weights)
    assert total_environment_penalty(result) == 40
    assert result.unknown_graph_m == (10, 10, 10, 10)


def test_rejects_invalid_weights_span_and_join_data() -> None:
    with pytest.raises(ValueError, match="weights"):
        evaluate_environment_cost(_intervals(), 0, 20, EnvironmentWeights(traffic=-1))
    with pytest.raises(ValueError, match="exceeds"):
        evaluate_environment_cost(_intervals(), 0, 21, EnvironmentWeights(traffic=1))
    gap = (_intervals()[0], EnvironmentInterval("s", 11, 20, 9, (0.5, 0.5, 0.5, 0.5), (1, 1)))
    with pytest.raises(ValueError, match="contiguous"):
        evaluate_environment_cost(gap, 0, 20, EnvironmentWeights())
    bad_value = (EnvironmentInterval("s", 0, 10, 9, (1.2, 0.5, 0.5, 0.5), (1, 1)),)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        evaluate_environment_cost(bad_value, 0, 10, EnvironmentWeights())
