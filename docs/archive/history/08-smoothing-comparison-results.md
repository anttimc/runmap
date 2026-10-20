# 8. First smoothing comparison results

> **Archived 2026-10-20.** This is an implementation/planning record,
> not current documentation. Status, defaults, commands, and measurements
> below are historical and may describe superseded behavior. Read the
> [current guide](../../guide.md) and [roadmap](../../roadmap.md).

Initial run: 2026-09-05. Thin-plate comparison added: 2026-10-01. This is an exploratory result, not the final smoother selection.

## Inputs and implementation

The comparison uses a central 2 × 2 km crop (`E 376000–378000`, `N 6678000–6680000`, EPSG:3067), containing 1000 × 1000 elevation cells. The reusable implementation separates smoothing, metric-aware differentiation, and experiment rendering:

- `terrain/smoothing.py`: normalized Gaussian smoothing, DCT-based thin-plate smoothing, and regularized nonlinear diffusion.
- `terrain/derivatives.py`: east/north gradients, magnitude, and uphill grid bearing.
- `experiments/compare_smoothing.py`: repeatable real and synthetic comparisons.

The nonlinear preset uses `kappa=8%`, a 2 m Gaussian guide, 20 iterations, and time step 0.2, for total diffusion time 4 pixel². Its flat-region diffusion scale is roughly comparable to a Gaussian sigma of 5.7 m; nonlinear conductance changes that scale around gradients.

The thin-plate trials use half-amplitude wavelengths of 20 m and 40 m. These are close to the approximately 21.3 m and 42.7 m half-amplitude wavelengths of Gaussian σ=4 m and σ=8 m, making the pairs useful initial scale comparisons. The implementation removes and restores a fitted plane, then applies the exact discrete Neumann-Laplacian response with orthonormal DCTs.

## Real Leppävaara crop

| Method | Runtime | Elevation RMS change | Median slope | 95th-percentile slope | 99.9th-percentile slope |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw | — | 0 m | 6.84% | 30.09% | 89.04% |
| Gaussian σ=2 m | 0.018 s | 0.074 m | 6.48% | 26.91% | 79.77% |
| Gaussian σ=4 m | 0.022 s | 0.166 m | 6.23% | 23.98% | 70.47% |
| Gaussian σ=8 m | 0.019 s | 0.321 m | 6.00% | 21.08% | 59.19% |
| Thin plate, 20 m half-amplitude | 0.012 s | 0.140 m | 6.35% | 25.01% | 74.87% |
| Thin plate, 40 m half-amplitude | 0.013 s | 0.280 m | 6.13% | 22.06% | 63.88% |
| Nonlinear κ=8%, t=4 px² | 0.402 s | 0.097 m | 5.44% | 27.98% | 88.17% |

Times are single observed runs on the current machine and exclude raster reading, gradient calculation, and figure rendering. They are useful for scale only. Both thin-plate trials took about 12–13 ms for one million cells, comparable to the Gaussian passes and roughly thirty times faster than the nonlinear preset in this run.

A separate full-source check on the 3000 × 3000 raster took 0.086 s to read the GeoTIFF and 0.162 s for a 40 m thin-plate pass. This supports interactive parameter exploration when elevation is retained in memory and updates are debounced; rendering and derivative calculation still need an end-to-end UI benchmark.

The nonlinear preset reduces typical slopes while retaining nearly all of the extreme upper tail. This may preserve sharp terrain, but it may also preserve high-gradient artifacts. Real-data percentiles cannot decide which interpretation is correct.

## Synthetic validation

The analytic surface combines a tilted plane, broad rounded hill, and narrower ridge-like hill. Independent Gaussian elevation noise with σ=0.20 m was added using a fixed seed. Metrics exclude a 40 m boundary. Direction errors are evaluated where the true slope is at least 1%.

| Method | Elevation RMSE | Gradient-vector RMSE | Median direction error | 95th direction error | Peak slope / true peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw | 0.200 m | 10.00% | 44.0° | 157.4° | 2.04 |
| Gaussian σ=2 m | 0.056 m | 2.22% | 12.0° | 85.8° | 1.23 |
| Gaussian σ=4 m | 0.031 m | 0.66% | 3.6° | 23.6° | 1.01 |
| Gaussian σ=8 m | 0.053 m | 0.30% | 1.1° | 6.8° | 0.94 |
| Thin plate, 20 m half-amplitude | 0.032 m | 0.77% | 4.2° | 27.7° | 1.03 |
| Thin plate, 40 m half-amplitude | 0.018 m | 0.20% | 1.1° | 6.8° | 0.98 |
| Nonlinear κ=8%, t=4 px² | 0.034 m | 0.92% | 3.6° | 13.3° | 1.41 |

For this smooth synthetic terrain and noise model, the 40 m thin-plate trial now gives the best elevation reconstruction and gradient-vector accuracy. It matches Gaussian σ=8 m's direction accuracy while retaining the true peak more closely: about 2% attenuation rather than 6%. The 20 m thin-plate and Gaussian σ=4 m trials are broadly similar, with Gaussian slightly ahead on elevation and gradient error and thin plate slightly closer on elevation-change magnitude in the real crop. The nonlinear preset improves direction strongly but retains a spurious peak about 41% above truth, so it is not yet preferable.

This synthetic example favors smooth surfaces by construction and is particularly compatible with a curvature penalty. The next validation should add discontinuities/artifacts representative of the elevation product, sharper terrain transitions, and familiar Leppävaara cross-sections. It must check thin-plate ringing, overshoot, boundary effects, and new extrema. Parameter comparisons should also include nonlinear settings with stronger conduction and/or longer diffusion rather than evaluating one preset.

## Reproduce

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[experiments]'
MPLBACKEND=Agg MPLCONFIGDIR=/tmp/runmap-matplotlib \
  .venv/bin/python -m runmap.experiments.compare_smoothing
.venv/bin/python -m unittest discover -s tests
```

Outputs:

- `artifacts/leppavaara-smoothing/smoothing-comparison.png`
- `artifacts/leppavaara-smoothing/derivative-cross-section-north.png` (west–east at N 6679599 m)
- `artifacts/leppavaara-smoothing/derivative-cross-section-central.png` (west–east at N 6678999 m)
- `artifacts/leppavaara-smoothing/derivative-cross-section-south.png` (west–east at N 6678399 m)
- `artifacts/leppavaara-smoothing/synthetic-derivative-ridge.png` (through the narrow synthetic ridge at y=70 m)
- `artifacts/leppavaara-smoothing/synthetic-derivative-hill-centre.png` (through the broad hill centre at y=0 m)
- `artifacts/leppavaara-smoothing/synthetic-derivative-south.png` (off-centre at y=-100 m)
- `artifacts/leppavaara-smoothing/synthetic-derivative-sharp-feature.png` (through a 6 m high, 24 m wide triangular ridge)
- `artifacts/leppavaara-smoothing/comparison.json`

Each cross-section image has two aligned panels: elevation profiles above and signed eastward grade below. It overlays raw elevation, Gaussian σ=4 m, Gaussian σ=8 m, and thin plate at a 40 m half-amplitude wavelength. Positive derivative values rise toward the east and negative values fall. The plots intentionally omit the weaker Gaussian, thin-plate, and nonlinear cases to keep the visual comparison legible; their numerical results remain in JSON. The report also records the quantity, sign convention, orientation, raster row, exact northing, and output path. It is the authoritative full-precision record. The map comparison uses a common 0–40% colour scale and equal-length uphill arrows at fixed 80 m spacing, hidden below 1% slope.

The synthetic derivative plots add the known analytic truth as a dashed black reference. They use the same selected methods as the real-data profiles and pass through the narrow ridge, broad hill centre, and an off-centre section.

The additional sharp-feature case uses a continuous triangular ridge with abrupt slope changes at its foot and crest. It is intentionally less favourable to curvature smoothing than the rounded Gaussian features and helps expose peak attenuation, transition spreading, and ringing.
