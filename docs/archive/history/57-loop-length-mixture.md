# MAP polynomial mixtures — 2026-10-29

The first mixture implementation was checked on the 5,475 completed Leppävaara
observations from the [baseline experiment](55-loop-length-baselines.md).
Current commands belong in [development](../../development.md#loop-length-map-mixtures),
and model definitions and proposed adaptive sampling extensions belong in the
[mathematical reference](../../reference/loop-length-mixture.md).
The [measurement record](57-loop-length-mixture.json) preserves priors, input
hash, source identity, full-data parameters, and fold diagnostics and metrics.

## Configuration and results

Each model uses the nine-term `network_affine` basis and a six-term gate with
coordinates, width, detour, and side. Proper Gaussian coefficient priors and an
inverse-gamma variance prior use the initial implementation defaults. The run
used three restarts, seed 0, at most 300 iterations, and relative objective
tolerance 1e-6. Validation retains sixteen spatial blocks with a one-cell buffer;
all widths and sides at a position stay together. Scalers are training-only.

| Experts | Soft MAE | Hard MAE | Soft RMSE | Mean negative log density | Converged folds |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 410.51 m | 410.51 m | 527.01 m | 7.7144 | 16/16 |
| 2 | 389.60 m | 405.83 m | 510.66 m | 7.7713 | 16/16 |
| 3 | 360.41 m | 371.76 m | 483.45 m | 7.5300 | 16/16 |
| 4 | 335.23 m | 341.33 m | 450.21 m | 7.3824 | 16/16 |

The refitted ordinary least-squares baseline has MAE 410.51 m and RMSE 527.02 m.
Four experts reduce soft MAE by about 18.3%. The two-expert model improves point
errors while worsening density prediction; the three- and four-expert models
improve both. Negative log density uses lengths in metres and is comparable
only with the same response units.

The largest soft prediction error is 2,764.51 m for four experts, versus
2,347.45 m for the ordinary baseline. Better average accuracy does not eliminate
large local failures. No predictive interval calibration or adaptive sampling
benefit was tested. Component-count selection on these same holdouts remains
exploratory; these results are not an untouched final test or evidence of
transfer to other starts and preferences.

## Reproduction

```sh
uv run --extra web --extra experiments python -m runmap.experiments.fit_loop_length_mixture \
  artifacts/loop-length-fit/leppavaara-north-2026-10-28-final \
  artifacts/loop-length-mixture/leppavaara-north-v1 \
  --components 1 2 --restarts 3

uv run --extra web --extra experiments python -m runmap.experiments.fit_loop_length_mixture \
  artifacts/loop-length-fit/leppavaara-north-2026-10-28-final \
  artifacts/loop-length-mixture/leppavaara-north-v1-k3-k4 \
  --components 3 4 --restarts 3
```

Use fresh output paths when repeating. Local output directories retain complete
fold-specific fits and per-observation held-out predictions. No new planner
evaluations were made. The full 394-test suite passed, including four focused
mixture tests covering MAP normal equations and variance updates, gate gradients,
synthetic regime recovery, predictive moments, determinism, and scaling behavior.
