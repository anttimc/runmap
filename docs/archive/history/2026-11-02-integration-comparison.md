# Native integration sampling comparison — 2026-11-02

This dated experiment compares integration speed and numerical error. Production
sampling defaults remain unchanged. It follows the
[native routing profile](2026-11-02-native-routing-profile.md).

## Results

- [Standalone visual report](../../../artifacts/integration-comparison-2026-11-02/index.html)
- [PNG](../../../artifacts/integration-comparison-2026-11-02/overview.png) and
  [SVG](../../../artifacts/integration-comparison-2026-11-02/overview.svg)
- [CSV measurements](../../../artifacts/integration-comparison-2026-11-02/results.csv)
  and [metadata](../../../artifacts/integration-comparison-2026-11-02/metadata.json)

A seeded uniform sample of 3,000 physical segment geometries contained 6,248
straight pieces. Median piece length was 9.07 m; p90 was 28.15 m and maximum
342.64 m. At current sampling, 81.4% of pieces were at the four-sample minimum
in seven of nine scenarios; the two narrowest fields reduced that to 23.1% and
72.1%. Raising the spacing cap alone therefore leaves much of the work intact.

For the scenario with 1,500 m home-to-turnaround separation, 225 m radial scale,
and 75 m transition width:

| Cap / minimum samples per piece | Integration median for 3,000 geometries | Speedup | p99 error | Worst error |
| --- | ---: | ---: | ---: | ---: |
| 5 m / 4 (current) | 0.2905 ms | 1.00× | 0.00133% | 0.00577% |
| 20 m / 4 | 0.2605 ms | 1.12× | 0.00258% | 0.02924% |
| 5 m / 1 | 0.2231 ms | 1.30× | 0.00252% | 0.00577% |
| 10 m / 2 | 0.1880 ms | 1.55× | 0.00515% | 0.01870% |
| 20 m / 2 | 0.1741 ms | 1.67× | 0.00729% | 0.05203% |
| 20 m / 1 | 0.1848 ms | 1.57× | 0.01782% | 0.06256% |

Errors are absolute differences from a fine reference, normalized by each
polyline's length, not percentages of its integral. For example, 0.05% on a
100 m polyline means 0.05 m of integrated unit-field error. Both field integrals
are bounded by the polyline length. For each geometry, the larger radial/side
error is used. Relative errors in an almost-zero integral could be much larger;
these numbers are not weighted total-route cost errors.

Across all nine scenarios, worst observed errors were 0.05776% for current
sampling, 0.07135% for 10 m / 2 or 20 m / 2, and 0.07149% for 20 m / 1.
The maximum p99 error across scenarios for 10 m / 2 was 0.00774%.
The 0.1 m versus 0.05 m reference convergence check differed by at most
0.000342% of geometry length. This is empirical convergence, not a rigorous
error bound.

Reducing the minimum and raising the cap together warrant a route-level
experiment. A 10 m cap with two minimum samples is a conservative candidate;
20 m with two samples is another. Measured timings fluctuate, and fewer samples
did not always produce a lower median. These results do not establish an exact
ranking of close configurations. No default has been selected or changed.

## Method and limits

The optional Cargo `integration-experiment` feature exposes a Rust-only
entry point to the same midpoint integration kernel used by routing. Normal
routing still passes a minimum of four. No Python extension rebuild is needed.

Caps 5/10/20/40 m were crossed with minimum counts 4/2/1. All candidates retained
`spacing = min(cap, radius/4, transition_width/4)`. Nine field frames crossed
separations 500/1,500/3,000 m with width ratios 0.01/0.05/0.15; radius ratio
was 0.15. Frames were aligned east-west around the sampled geometry's median
position. These are synthetic request frames on real geometry, rather than
nine actual routing requests.

Geometry came from dataset
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`
in the same fixed Helsinki bounds as the earlier profile. Segments were
sorted by physical ID, deduplicated, filtered to have a vertex inside the bounds,
and sampled with seed 20261102 from 44,917 eligible segments. Input digest,
frame coordinates, platform, and revision are in metadata. The capture used
revision `d9685cc4e869266e06416f444eca8cf76466fb6d` plus working-tree
instrumentation. Measurements ran on macOS arm64 using a release Rust build.

Each configuration was warmed up. There were 21 timing samples, each averaging
ten complete batches, with configuration order rotated between samples. Timings
include input validation, frame preparation, integration, and result-vector
allocation/destruction. Geometry was already decoded into Rust vectors. The
profiling feature was disabled. CPU scheduling, frequency, and caches were not
controlled; sub-millisecond differences require caution.

The reference cap was 0.05 m, checked against 0.1 m. No integral memoization was
used. These uniformly sampled full segments are not weighted by search visits;
clipped endpoint edges and alternative field orientations were not studied.
No route searches were recomputed, so route identity, accumulated weighted costs,
and complete-request speedups remain unvalidated. Broad or saturated fields
can be cheap; narrow transitions still force fine spacing regardless of cap.

Validation: Rust tests passed with and without `integration-experiment` (six and
five tests), including default-entry equivalence and fine-reference convergence.
Seven Python field tests passed. Clippy passed for the experiment and library.
The normal Python extension was not rebuilt or replaced.

## Reproduction

With prepared Helsinki numeric data and the experiments dependencies installed:

```sh
source "$HOME/.cargo/env"
cargo build --manifest-path rust/Cargo.toml --release \
  --example compare_integration --features integration-experiment
uv run --no-sync python -m runmap.experiments.compare_integration \
  data/paths/prepared/helsinki-capital-area/numeric-routing-v2.sqlite \
  --count 3000 --repeats 21 \
  --output artifacts/integration-comparison-2026-11-02
```

The ignored artifact directory includes the standalone HTML, PNG/SVG, CSV,
metadata, and exact text input consumed by the Rust example.
