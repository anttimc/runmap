"""Verify MAP updates, identifiable synthetic regimes, and training-only scaling."""

from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import OptimizeResult, check_grad

from runmap.experiments.loop_length_mixture import (
    MixtureConfig, choose_rbf_centers, expert_precision, fit_mixture, gate_hessian, gate_objective,
    predict_mixture, update_gate,
)


class MixtureTests(unittest.TestCase):
    def test_stalled_gate_solver_requires_stationarity_and_no_objective_increase(self) -> None:
        rng = np.random.default_rng(27)
        gates = np.column_stack((np.ones(30), rng.normal(size=(30, 3))))
        responsibility = rng.dirichlet(np.ones(3), size=30)
        initial = np.zeros((4, 2))
        optimum = update_gate(gates, responsibility, initial, .2)
        value, gradient = gate_objective(optimum.ravel(), gates, responsibility, .2)
        stalled = OptimizeResult(x=optimum.ravel(), fun=value, jac=gradient, success=False, message="rounding")
        with patch("runmap.experiments.loop_length_mixture.minimize", return_value=stalled):
            recovered = update_gate(gates, responsibility, initial, .2, use_newton=True)
        np.testing.assert_allclose(recovered, optimum)
        for invalid in (OptimizeResult(stalled, jac=np.ones(8)), OptimizeResult(stalled, fun=value + 100)):
            with patch("runmap.experiments.loop_length_mixture.minimize", return_value=invalid):
                with self.assertRaises(RuntimeError):
                    update_gate(gates, responsibility, initial, .2, use_newton=True)

    def test_rbf_gate_recovers_a_curved_region_on_unseen_inputs(self) -> None:
        rng = np.random.default_rng(41)
        coordinates = rng.uniform(-2, 2, (500, 2))
        experts = np.column_stack((np.ones(500), coordinates))
        gates = np.column_stack((np.ones(500), coordinates, np.zeros((500, 3))))
        lengths = 2000 + 120 * coordinates[:, 0] + 1200 * (np.sum(coordinates ** 2, axis=1) < 1.4)
        lengths += rng.normal(0, 20, 500)
        config = MixtureConfig(components=2, restarts=4, gate="rbf", rbf_count=20)
        fit = fit_mixture(experts[:400], gates[:400], lengths[:400], config)
        nonlinear = predict_mixture(fit, experts[400:], gates[400:])
        linear = fit_mixture(experts[:400], gates[:400], lengths[:400], replace(config, gate="linear"))
        baseline = predict_mixture(linear, experts[400:], gates[400:])
        self.assertLess(np.abs(nonlinear.mean - lengths[400:]).mean(),
                        np.abs(baseline.mean - lengths[400:]).mean() * .7)
        self.assertTrue(np.all(np.diff(fit.objective_history) <= 1e-7))
        self.assertIsNotNone(fit.rbf_centers)
        assert fit.rbf_centers is not None
        scaled = (gates[:400] - fit.gate_scaling.mean) / fit.gate_scaling.scale
        self.assertTrue(all(np.any(np.all(scaled[:, 1:4] == center, axis=1)) for center in fit.rbf_centers))
        batch = predict_mixture(fit, experts[400:], gates[400:])
        one = predict_mixture(fit, experts[-1:], gates[-1:])
        np.testing.assert_allclose(batch.weights[-1:], one.weights)

    def test_rbf_centers_ignore_duplicate_sides_and_stop_at_unique_inputs(self) -> None:
        gates = np.array([[1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 1], [1, 2, 3, 1, 0, 0]], dtype=float)
        centers = choose_rbf_centers(gates, 16)
        self.assertEqual(centers.shape, (2, 3))

    def test_single_expert_satisfies_map_normal_equations(self) -> None:
        rng = np.random.default_rng(8)
        coordinate = np.linspace(-2, 2, 150)
        experts = np.column_stack((np.ones(150), coordinate, coordinate ** 2))
        gates = np.column_stack((np.ones(150), coordinate, coordinate > 0)).astype(float)
        lengths = 1800 + 500 * coordinate + 90 * coordinate ** 2 + rng.normal(0, 30, 150)
        config = MixtureConfig(components=1, restarts=1, tolerance=1e-10)
        fit = fit_mixture(experts, gates, lengths, config)
        scaled = (experts - fit.expert_scaling.mean) / fit.expert_scaling.scale
        normal = scaled.T @ (scaled @ fit.coefficients[:, 0] - lengths)
        normal += fit.variances[0] * expert_precision(3, config) * fit.coefficients[:, 0]
        np.testing.assert_allclose(normal, 0, atol=1e-5)
        residual = lengths - scaled @ fit.coefficients[:, 0]
        expected = (config.variance_scale_m2 + .5 * residual @ residual) / (config.variance_shape + 1 + 75)
        self.assertAlmostEqual(fit.variances[0], expected, places=6)
        self.assertTrue(fit.converged)
        self.assertTrue(np.all(np.diff(fit.objective_history) <= 1e-7))

    def test_gate_gradient_matches_finite_differences(self) -> None:
        rng = np.random.default_rng(9)
        gates = rng.normal(size=(25, 4))
        responsibility = rng.dirichlet(np.ones(3), size=25)
        coefficients = rng.normal(size=8)
        error = check_grad(lambda value: gate_objective(value, gates, responsibility, .2)[0],
                           lambda value: gate_objective(value, gates, responsibility, .2)[1], coefficients)
        self.assertLess(error, 1e-5)
        step = 1e-5
        numeric = np.column_stack([
            (gate_objective(coefficients + direction, gates, responsibility, .2)[1]
             - gate_objective(coefficients - direction, gates, responsibility, .2)[1]) / (2 * step)
            for direction in np.eye(len(coefficients)) * step])
        np.testing.assert_allclose(gate_hessian(coefficients, gates, responsibility, .2), numeric, atol=1e-8)

    def test_recovers_input_selected_regimes_and_predictive_moments(self) -> None:
        rng = np.random.default_rng(10)
        coordinate = np.linspace(-2, 2, 250)
        experts = np.column_stack((np.ones(250), coordinate))
        gates = np.column_stack((np.ones(250), coordinate, coordinate > 0)).astype(float)
        lengths = 2000 + 250 * coordinate + 1400 * (coordinate > 0) + rng.normal(0, 25, 250)
        config = MixtureConfig(components=2, restarts=4, tolerance=1e-8)
        fit = fit_mixture(experts, gates, lengths, config)
        prediction = predict_mixture(fit, experts, gates, lengths)
        single = fit_mixture(experts, gates, lengths, replace(config, components=1, restarts=1))
        baseline = predict_mixture(single, experts, gates)
        self.assertLess(np.abs(prediction.hard - lengths).mean(), 60)
        self.assertLess(np.abs(prediction.mean - lengths).mean(), np.abs(baseline.mean - lengths).mean() / 2)
        np.testing.assert_allclose(prediction.weights.sum(axis=1), 1)
        expected = (prediction.weights * (fit.variances + prediction.component_means ** 2)).sum(axis=1) - prediction.mean ** 2
        np.testing.assert_allclose(prediction.variance, expected, atol=1e-7)
        self.assertTrue(np.isfinite(prediction.log_density).all())
        again = fit_mixture(experts, gates, lengths, config)
        np.testing.assert_array_equal(fit.coefficients, again.coefficients)
        self.assertTrue(np.all(np.diff(fit.objective_history) <= 1e-7))

    def test_prediction_does_not_refit_scaling_and_constant_features_are_safe(self) -> None:
        coordinate = np.linspace(0, 1, 40)
        experts = np.column_stack((np.ones(40), coordinate, np.full(40, 7)))
        gates = np.column_stack((np.ones(40), coordinate, np.zeros(40)))
        fit = fit_mixture(experts, gates, 1000 + coordinate * 100, MixtureConfig(components=1, restarts=1))
        batch = predict_mixture(fit, experts, gates)
        one = predict_mixture(fit, experts[-1:], gates[-1:])
        np.testing.assert_allclose(batch.mean[-1:], one.mean)
        self.assertEqual(fit.expert_scaling.scale[-1], 1)
        self.assertEqual(fit.gate_scaling.scale[-1], 1)
        with self.assertRaises(ValueError):
            fit_mixture(experts, gates, np.full(40, np.nan))
        with self.assertRaises(ValueError):
            fit_mixture(experts, gates, coordinate, MixtureConfig(variance_shape=-1))


if __name__ == "__main__":
    unittest.main()
