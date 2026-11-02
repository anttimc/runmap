# Goals

Help a runner understand terrain before choosing a run across the Helsinki
capital area. The project is also a place to learn through replaceable,
understandable implementations.

## Intended outcomes

- **Find flatter options:** locate connected paths, recognize short rises and
  sustained climbs, and understand travel direction beyond a total-ascent number.
- **Find training hills:** distinguish short steep climbs, longer gentle climbs,
  and rolling terrain through inspectable measurements.
- **Explore interactively:** navigate an intended viewport of roughly 20 × 20 km
  without processing or transferring the entire source raster on each movement.
  Coverage may be larger; performance promises require representative measurements.
- **Understand running surroundings:** explore overlapping forest, waterside, and
  road-exposure attributes, then express supported route preferences. Preserve
  missing-data uncertainty and explain tradeoffs; the
  [environment experiment](environment-preferences.md) is planned work.
- **Adjust routes interactively:** move the start or turnaround, or change the
  centre-cost width, and see the revised route without a perceptible wait.
  Measure backend response and visible browser latency separately; a fast cached
  repeat does not establish responsiveness when endpoints or settings change.
- **Experiment independently:** change mathematical definitions without rewriting
  acquisition or the UI, run calculations outside the web application, and trace
  displayed measurements to their sources and settings.
- **Learn low-level implementation:** implement and measure small numerical and
  algorithmic kernels in Rust behind clear Python boundaries. Prefer an
  understandable scalar implementation first, preserving a readable reference
  and measuring the effect on complete interactions.

## Product direction

Help the runner understand available paths, assemble a run with guidance, and
eventually compare a few useful automatic suggestions. Use one attributed
pedestrian graph across these tools. Real junctions, access restrictions,
bridges, tunnels, and uncertainty matter more than partitioning the map into
cycles. Runs may retrace necessary access stems.

Keep preparation separate from ordinary interaction. Prefer explicit data and
focused functions, local files, and measured improvements over infrastructure
introduced in anticipation of future scale.

Keep interactive startup and active memory independent of total prepared path
coverage: query the relevant area from a local SQLite dataset and process a
bounded working set in the application. Retain compact numeric representations
for that working set. See the [migration roadmap](roadmap.md#next-sqlite-storage-and-bounded-network-loading)
for implementation stages and planner area-expansion limits.

## Scope

Terrain and path measurements inform route choice; they do not establish current
path conditions or predict heart rate, personal effort, or training outcomes.
Accounts, social features, live activity tracking, training prescriptions, and
turn-by-turn navigation are outside the current product scope.

See the [guide](guide.md) for delivered behavior and the [roadmap](roadmap.md)
for next steps and optional experiments.
