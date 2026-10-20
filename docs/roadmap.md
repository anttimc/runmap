# Roadmap

This is the authoritative list of outstanding work. Existing behavior belongs
in the [guide](guide.md). Implementation and user acceptance are separate:
historical passing tests and smoke checks do not complete local runner review.
The priorities below consolidate the previous plans without marking their
remaining acceptance gates complete.

## Next: correctness and local acceptance

- Correct the Explore status label that still describes a round-trip outing.
  Acceptance: labels, help, inspection, and budget behavior consistently describe
  forward distance and make no return guarantee.
- Review familiar flat corridors, short/sustained hills, rolling paths,
  bridges/underpasses, stairs, restrictions, missing surfaces, and extract edges.
  Reuse the [source-derived fixtures](archive/history/30-routing-preferences-audit.json),
  recording accepted/rejected examples with endpoints, expected observations,
  dataset/model identities, and settings. Separate data defects from UI or
  numerical defects. Acceptance: measurements, connectivity, and uncertainty are
  understandable to a local runner.
- Check the current hill slider/help and combined planner interactions in desktop
  and mobile browsers, including keyboard use, cancellation, stale results,
  failure states, mode restoration, inspection, and Fit run. Historical browser
  reports include superseded hill models. Acceptance: current controls behave
  consistently and undesirable runs can be reproduced.
- Measure current preparation size/time, startup memory, cold/warm tile delivery,
  browser responsiveness, and start/policy/budget changes on a recorded machine.
  Existing tile benchmarks demonstrate specific payload/source-processing gains;
  full-application frame timing and broad policy/screenshot review remain open.
  Acceptance: measured interaction performance supports exploration and regeneration;
  adopt numerical targets from representative observations.

## Planned: routing improvements

Deliver these independently after their data and validation gates pass:

| Increment | Required work and acceptance |
| --- | --- |
| Traffic-light avoidance | Preserve crossing-way, modern signal, island, and railway evidence; define directed passage events and endpoint/repeated-passage semantics. Use search state when charging depends on arrival/exit. Validate multi-node, branching, avoidable, and unavoidable crossings before exposing a control. |
| Busy-road preference | Acquire complete buffered road context independently of runnable eligibility. Define a bounded distance-decaying exposure proxy and coverage mask; review parallel paths, crossings, and grade separation. |
| Green-surroundings preference | Acquire complete candidate ways and multipolygon geometry with coverage. Select documented inclusion rules and review parks, woods, edges, private land, and green paths beside busy roads. Missing context must stay unknown. |
| Combined calibration | Compare each supported preference and combinations against Off; record distance, observations, uncertainty, expanded states, and latency. Expose only supported controls and require understandable local tradeoffs. |
| Guided assembly | Add waypoints, retained sections, and explicit avoidance guidance. Validate complete directed walks, ordered constraints, repetition, and infeasible requests against saved familiar outings. |
| Automatic suggestions | Build a bounded set of useful, geographically different complete runs with user guidance. The existing CLI anchor baseline is experimental evidence. Acceptance requires stated constraints, meaningful alternatives, and local runner review. |

Feedback tools such as copied reproducible request records, advanced shape
settings, field/repetition overlays, and longer inspection stretches remain
follow-ups driven by the local review. Cross-junction flat/hill discovery and
absolute-elevation profiles need explicit scope before implementation.

## Planned: terrain and maintainability

- Repeat the whole-domain seam comparison at the longest 100 m background
  preset; the recorded 512 m halo validation covers the 40 m default.
- Move missing smoothing products out of synchronous request handling into bounded
  background work, or precompute useful presets. Then measure contour simplification,
  sampling density, and persistent/display-cache improvements in that order.
- Continue representative terrain checks for ringing, boundary effects, nodata,
  and noise/detail tradeoffs. A masked smoother or validated fill policy is
  required before processing genuinely incomplete DCT windows.
- Resolve exactly flat terrain samples: derived zero magnitude has undefined
  bearing, and the path sampler currently rejects that nodata bearing. Acceptance:
  a valid flat cell yields zero path grade without inventing a terrain direction,
  while genuine missing data stays missing.
- Complete precise annotations and focused configuration validation; move
  operations out of data containers and separate raster processing/persistence
  where useful. Follow [AGENTS.md](../AGENTS.md), preserve serialized contracts,
  and add static checking when domain types are coherent.
- Add preparation implementation identity to provenance. Measure before adding
  cross-request field caching or replacing inspectable JSON storage.
- Continue frontend separation by extracting inspection/chart rendering and
  feature map layers from the interaction controllers. Make remaining feature
  state explicit where DOM values or status text currently carry it.

## Optional / later

Public hosting needs a suitable basemap arrangement and attribution, repeatable
data builds, bounded misses/storage, and deployment-specific cache/concurrency
decisions. Current exploration snapshots are process-local. Object storage,
CDN adapters, multiple API workers, administrator acquisition jobs, and public
acquisition controls are not delivered infrastructure.

Wider public coverage or a private Jämsä region needs explicitly selected bounds
and preparation. Running-time models, target-duration/length tuning, automatic
turnaround selection, route import/export, and movable-centre/enclosure controls
remain optional increments. A bounded Odin or Go experiment is optional when
profiling or a learning objective justifies it.

Cycle-first topology, face search, and cycle catalogues are superseded; their
unfinished tasks in the [archive](archive/README.md) are not scheduled.
