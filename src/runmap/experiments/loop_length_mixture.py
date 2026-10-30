"""Fit finite polynomial experts with Gaussian priors and a softmax MAP gate."""

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.linalg import solve
from scipy.optimize import minimize
from scipy.special import logsumexp

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class MixtureConfig:
    components: int = 2
    restarts: int = 4
    max_iterations: int = 300
    tolerance: float = 1e-6
    seed: int = 0
    coefficient_sd_m: float = 3000.0
    intercept_sd_m: float = 10000.0
    gate_sd: float = 3.0
    variance_shape: float = 2.0
    variance_scale_m2: float = 270000.0
    gate: Literal["linear", "rbf"] = "linear"
    rbf_count: int = 16
    rbf_length_scale: float = 0.8


@dataclass(frozen=True)
class FeatureScaling:
    mean: FloatArray
    scale: FloatArray


@dataclass(frozen=True)
class MixtureFit:
    config: MixtureConfig
    expert_scaling: FeatureScaling
    gate_scaling: FeatureScaling
    coefficients: FloatArray
    gate_coefficients: FloatArray
    variances: FloatArray
    objective_history: tuple[float, ...]
    converged: bool
    effective_counts: FloatArray
    restart: int
    rbf_centers: FloatArray | None = None


@dataclass(frozen=True)
class MixturePrediction:
    weights: FloatArray
    component_means: FloatArray
    mean: FloatArray
    hard: FloatArray
    variance: FloatArray
    log_density: FloatArray | None


def validate_config(config: MixtureConfig) -> None:
    integers = (config.components, config.restarts, config.max_iterations, config.rbf_count)
    if any(value < 1 for value in integers) or config.seed < 0:
        raise ValueError("Counts must be positive and seed nonnegative")
    scales = (config.tolerance, config.coefficient_sd_m, config.intercept_sd_m,
              config.gate_sd, config.variance_shape, config.variance_scale_m2, config.rbf_length_scale)
    if not all(np.isfinite(value) and value > 0 for value in scales):
        raise ValueError("Prior scales, shape, and tolerance must be finite and positive")
    if config.gate not in ("linear", "rbf"):
        raise ValueError("Gate must be linear or rbf")


def validate_arrays(experts: FloatArray, gates: FloatArray, lengths: FloatArray | None = None) -> None:
    if experts.ndim != 2 or gates.ndim != 2 or len(experts) != len(gates) or not len(experts):
        raise ValueError("Feature matrices must have matching nonempty rows")
    if experts.shape[1] < 1 or gates.shape[1] < 1:
        raise ValueError("Feature matrices require an intercept")
    if not np.all(experts[:, 0] == 1) or not np.all(gates[:, 0] == 1):
        raise ValueError("First feature columns must be intercepts")
    if not np.isfinite(experts).all() or not np.isfinite(gates).all():
        raise ValueError("Features must be finite")
    if lengths is not None and (lengths.shape != (len(experts),) or not np.isfinite(lengths).all()):
        raise ValueError("Lengths must be a finite vector matching features")


def fit_scaling(values: FloatArray, binary_last: bool = False) -> FeatureScaling:
    mean, scale = values.mean(axis=0), values.std(axis=0)
    scale[scale == 0] = 1
    mean[0], scale[0] = 0, 1
    if binary_last:
        mean[-1], scale[-1] = 0, 1
    return FeatureScaling(mean, scale)


def transform(values: FloatArray, scaling: FeatureScaling) -> FloatArray:
    return (values - scaling.mean) / scaling.scale


def choose_rbf_centers(gates: FloatArray, count: int) -> FloatArray:
    """Cover training positions and widths without using length labels."""
    points = np.unique(gates[:, 1:4], axis=0)
    selected = [int(np.argmin(np.sum((points - points.mean(axis=0)) ** 2, axis=1)))]
    distances = np.sum((points - points[selected[0]]) ** 2, axis=1)
    for _ in range(min(count, len(points)) - 1):
        selected.append(int(np.argmax(distances)))
        distances = np.minimum(distances, np.sum((points - points[selected[-1]]) ** 2, axis=1))
    return points[selected].copy()


def expand_gate(gates: FloatArray, centers: FloatArray | None, length_scale: float) -> FloatArray:
    if centers is None:
        return gates
    distances = np.sum((gates[:, None, 1:4] - centers[None, :, :]) ** 2, axis=2)
    radial = np.exp(-distances / (2 * length_scale ** 2))
    # Retain linear features, with the unstandardized side indicator last.
    return np.column_stack((gates[:, :-1], radial, gates[:, -1]))


def gate_log_weights(gates: FloatArray, coefficients: FloatArray) -> FloatArray:
    logits = np.column_stack((gates @ coefficients, np.zeros(len(gates))))
    return logits - logsumexp(logits, axis=1, keepdims=True)


def gate_objective(flat: FloatArray, gates: FloatArray, responsibilities: FloatArray,
                   precision: float) -> tuple[float, FloatArray]:
    coefficients = flat.reshape(gates.shape[1], responsibilities.shape[1] - 1)
    log_weights = gate_log_weights(gates, coefficients)
    value = -np.sum(responsibilities * log_weights) + .5 * precision * np.sum(coefficients ** 2)
    gradient = gates.T @ (np.exp(log_weights[:, :-1]) - responsibilities[:, :-1]) + precision * coefficients
    return float(value), gradient.ravel()


def gate_hessian(flat: FloatArray, gates: FloatArray, responsibilities: FloatArray,
                 precision: float) -> FloatArray:
    columns, free = gates.shape[1], responsibilities.shape[1] - 1
    probabilities = np.exp(gate_log_weights(gates, flat.reshape(columns, free)))[:, :-1]
    hessian = np.zeros((columns, free, columns, free))
    for k in range(free):
        for j in range(k + 1):
            weight = probabilities[:, k] * ((1 if k == j else 0) - probabilities[:, j])
            block = gates.T @ (gates * weight[:, None])
            hessian[:, k, :, j] = block
            hessian[:, j, :, k] = block.T
    return hessian.reshape(columns * free, columns * free) + precision * np.eye(columns * free)


def update_gate(gates: FloatArray, responsibilities: FloatArray, coefficients: FloatArray,
                precision: float, use_newton: bool = False) -> FloatArray:
    if not coefficients.size:
        return coefficients.copy()
    if use_newton:
        result = minimize(gate_objective, coefficients.ravel(), args=(gates, responsibilities, precision),
                          method="trust-exact", jac=True, hess=gate_hessian,
                          options={"maxiter": 100, "gtol": 1e-5})
        if not result.success:
            # Near a mode, objective rounding can stall the trust-region ratio.
            result = minimize(gate_objective, result.x, args=(gates, responsibilities, precision),
                              method="L-BFGS-B", jac=True,
                              options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-7})
    else:
        result = minimize(gate_objective, coefficients.ravel(), args=(gates, responsibilities, precision),
                          method="L-BFGS-B", jac=True, options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-7})
    if not result.success:
        initial, _ = gate_objective(coefficients.ravel(), gates, responsibilities, precision)
        gradient_norm = float(np.max(np.abs(result.jac)))
        stationary = (use_newton and gradient_norm <= 1e-4
                      and result.fun <= initial + 1e-10 * max(1.0, abs(initial)))
        if not stationary:
            raise RuntimeError(f"Gate optimization failed: {result.message}; gradient norm {gradient_norm:g}")
    return result.x.reshape(coefficients.shape)


def expert_precision(columns: int, config: MixtureConfig) -> FloatArray:
    precision = np.full(columns, config.coefficient_sd_m ** -2)
    precision[0] = config.intercept_sd_m ** -2
    return precision


def update_experts(experts: FloatArray, lengths: FloatArray, responsibilities: FloatArray,
                   variances: FloatArray, precision: FloatArray) -> FloatArray:
    coefficients = np.empty((experts.shape[1], responsibilities.shape[1]))
    for k in range(responsibilities.shape[1]):
        weighted = experts * responsibilities[:, k, None]
        system = experts.T @ weighted + variances[k] * np.diag(precision)
        coefficients[:, k] = solve(system, weighted.T @ lengths, assume_a="pos")
    return coefficients


def log_components(experts: FloatArray, gates: FloatArray, lengths: FloatArray,
                   coefficients: FloatArray, gate: FloatArray, variances: FloatArray) -> FloatArray:
    residual = lengths[:, None] - experts @ coefficients
    return gate_log_weights(gates, gate) - .5 * (np.log(2 * np.pi * variances) + residual ** 2 / variances)


def objective(experts: FloatArray, gates: FloatArray, lengths: FloatArray, coefficients: FloatArray,
              gate: FloatArray, variances: FloatArray, config: MixtureConfig) -> float:
    likelihood = logsumexp(log_components(experts, gates, lengths, coefficients, gate, variances), axis=1).sum()
    penalty = .5 * np.sum(expert_precision(experts.shape[1], config)[:, None] * coefficients ** 2)
    penalty += .5 * np.sum(gate ** 2) / config.gate_sd ** 2
    penalty += np.sum((config.variance_shape + 1) * np.log(variances) + config.variance_scale_m2 / variances)
    return float(-likelihood + penalty)


def fit_restart(experts: FloatArray, gates: FloatArray, lengths: FloatArray, config: MixtureConfig,
                restart: int, expert_scaling: FeatureScaling, gate_scaling: FeatureScaling) -> MixtureFit:
    rng = np.random.default_rng(np.random.SeedSequence((config.seed, restart)))
    # Random projections provide distinct initial partitions of the gate space.
    scores = gates @ rng.normal(size=(gates.shape[1], config.components))
    labels = scores.argmax(axis=1)
    responsibilities = np.full((len(lengths), config.components), .1 / config.components)
    responsibilities[np.arange(len(lengths)), labels] += .9
    variances = np.full(config.components, max(float(lengths.var()), config.variance_scale_m2 / (config.variance_shape + 1)))
    gate = update_gate(gates, responsibilities, np.zeros((gates.shape[1], config.components - 1)),
                       config.gate_sd ** -2, use_newton=config.gate == "rbf")
    precision = expert_precision(experts.shape[1], config)
    coefficients = update_experts(experts, lengths, responsibilities, variances, precision)
    history = [objective(experts, gates, lengths, coefficients, gate, variances, config)]
    converged = False
    for _ in range(config.max_iterations):
        joint = log_components(experts, gates, lengths, coefficients, gate, variances)
        responsibilities = np.exp(joint - logsumexp(joint, axis=1, keepdims=True))
        coefficients = update_experts(experts, lengths, responsibilities, variances, precision)
        residual = lengths[:, None] - experts @ coefficients
        variances = (config.variance_scale_m2 + .5 * np.sum(responsibilities * residual ** 2, axis=0)) / (
            config.variance_shape + 1 + .5 * responsibilities.sum(axis=0))
        gate = update_gate(gates, responsibilities, gate, config.gate_sd ** -2, use_newton=config.gate == "rbf")
        value = objective(experts, gates, lengths, coefficients, gate, variances, config)
        improvement = history[-1] - value
        if improvement < -1e-8 * max(1.0, abs(history[-1])):
            raise RuntimeError("MAP objective increased")
        history.append(value)
        if abs(improvement) <= config.tolerance * max(1.0, abs(history[-2])):
            converged = True
            break
    joint = log_components(experts, gates, lengths, coefficients, gate, variances)
    counts = np.exp(joint - logsumexp(joint, axis=1, keepdims=True)).sum(axis=0)
    return MixtureFit(config, expert_scaling, gate_scaling, coefficients, gate, variances,
                      tuple(history), converged, counts, restart)


def fit_mixture(experts: FloatArray, gates: FloatArray, lengths: FloatArray,
                config: MixtureConfig = MixtureConfig()) -> MixtureFit:
    validate_config(config)
    validate_arrays(experts, gates, lengths)
    if config.gate == "rbf" and gates.shape[1] != 6:
        raise ValueError("RBF gates require [1, forward, lateral, q, detour, side]")
    if len(lengths) < config.components * experts.shape[1]:
        raise ValueError("Too few observations for the requested number of experts")
    expert_scaling, gate_scaling = fit_scaling(experts), fit_scaling(gates, binary_last=True)
    x, g = transform(experts, expert_scaling), transform(gates, gate_scaling)
    centers = choose_rbf_centers(g, config.rbf_count) if config.gate == "rbf" else None
    g = expand_gate(g, centers, config.rbf_length_scale)
    fits: list[MixtureFit] = []
    for restart in range(config.restarts):
        fits.append(fit_restart(x, g, lengths, config, restart, expert_scaling, gate_scaling))
    converged = [fit for fit in fits if fit.converged]
    best = min(converged or fits, key=lambda fit: fit.objective_history[-1])
    return replace(best, rbf_centers=centers)


def predict_mixture(fit: MixtureFit, experts: FloatArray, gates: FloatArray,
                    lengths: FloatArray | None = None) -> MixturePrediction:
    validate_arrays(experts, gates, lengths)
    x, g = transform(experts, fit.expert_scaling), transform(gates, fit.gate_scaling)
    g = expand_gate(g, fit.rbf_centers, fit.config.rbf_length_scale)
    means = x @ fit.coefficients
    weights = np.exp(gate_log_weights(g, fit.gate_coefficients))
    mean = np.sum(weights * means, axis=1)
    variance = np.sum(weights * (fit.variances + (means - mean[:, None]) ** 2), axis=1)
    hard = means[np.arange(len(means)), weights.argmax(axis=1)]
    density = None if lengths is None else logsumexp(
        log_components(x, g, lengths, fit.coefficients, fit.gate_coefficients, fit.variances), axis=1)
    return MixturePrediction(weights, means, mean, hard, variance, density)
