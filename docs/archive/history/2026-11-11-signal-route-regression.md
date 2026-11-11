# Leppävaara–Pajamäki signal regression — 2026-11-11

The runner reported zero lights from Leppävaarankatu 19 to Pajamäki despite
several real crossings. The investigation reproduced false-zero observations
with a representative one-way request. Source evidence already existed in the
original routing snapshot; a new signal download was not needed.

## Reproduction and identity

The [saved request, results, traversed segment IDs, and artifact identity](2026-11-11-signal-route-regression.json)
pin the inputs. The start is the building centre of Leppävaarankatu 19, located
offline from building way `28816404` in the already downloaded Finland PBF. The
destination is a point on Pajamäentie in Pajamäki. These coordinates reproduce
the reported corridor, not an unknown exact browser request from the runner.

The routing network remains the October 25 acquisition, dataset
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
The newer PBF was used only to locate the address; signal attribution uses the
exact original routing JSON, SHA-256
`d3e549ef9618efe06bb7a4ce96b5e511de3154fd946329735cbc857f27ca28f9`.
The request uses one-way mode, zero loop width, Signals Low/High, and other
preferences Off. It retains the default surface/stair policy.

## Defects and corrections

The original model charged only selected incoming/outgoing pairs. Search could
leave a crossing on a short road spur, reverse, and return with another recorded
arrival, then cross without the original charge. The false-zero route included
immediate retracing at Numersinkatu, Alberganesplanadi, Komentajankatu, and
Perkkaankuja. The first implementation's synthetic branching example did not
include a sufficiently cheap spur and therefore missed this defect.

Signals On now prunes immediate retracing within a shortest-path leg. A requested
turnaround still starts a new leg and can reverse the prior leg's arrival.
Python and Rust implement the same constraint; the regression fixture includes
a two-metre road spur that previously made the incorrect route cheaper.

Removing spurs alone exposed a second attribution gap: mixed road/path movements
at explicit signal crossings were missing, including connections at nodes already
covered by a crossing-way gate. They became free detours through the same
junction. Model `directed-signal-passages-v2` retains them as uncertain candidates
with the documented finite fallback. Generic vehicle-only junctions still do not
automatically become pedestrian signals.

## Observed results

| Model | Preference | Distance | Resolved / unresolved passages | Immediate spur reversals |
| --- | --- | ---: | --- | --- |
| v1 | Low | 3,340.48 m | 0 / 0 | 5 |
| v1 | High | 3,340.48 m | 0 / 0 | 5 |
| v2 | Low | 3,330.36 m | 3 / 0 | 0 |
| v2 | High | 3,532.47 m | 0 / 0 | 0 |

The corrected High route follows a different corridor through Linnanrakentajainkuja,
Postipuunkäytävä, and Hatsinanpuisto. Inspection found no mapped signal nodes on
its traversed junctions. This is evidence of a different mapped route, not proof
that every real signal is mapped or that this detour has been accepted by a runner.

The active sidecar uses SQL schema 2 and passage model v2. It is 11,608,064 bytes,
with 4,141 explicit pedestrian source points and 1,119 other/uncertain source
points. The illustrative corridor bounds 24.80–24.865 E, 60.21–60.22 N contain
79 pedestrian and 34 other/uncertain points; these are not the route's wait count.
The original sidecar remains as `signals-before-movement-v2.sqlite` locally.

## Source inspection and validation

**Environment → Traffic lights** now displays spatially queried source points,
independent of raster availability and routing costs. Red identifies explicit
pedestrian evidence and amber other/uncertain evidence. OSM IDs and tags are
available in point info popups; control explanations and caveats use contextual
help popups rather than permanent paragraphs. This allows runner review to
distinguish absent source evidence from passage-attribution limitations.

The full Python suite passed 488 tests; the frontend suite passed 26 tests and
the TypeScript/Vite build passed. Rust's 13 tests and native-binding clippy checks
passed. Coverage includes cheap spurs, mixed connections, source-map bounds,
missing/stale artifacts, response cancellation, popup interaction, and the
existing endpoint/repetition/leg-seam semantics. These checks do not replace
regional attribution review, weight calibration, or performance acceptance.

The live local API reproduced the saved v2 Low/High results. A Chrome 157 browser
check rendered 139 source points at zoom 15 with both display classifications,
verified the source-info popup, keyboard help, Environment visibility, and touch
help at 390×844. It used the real source API without mocked signal responses;
the main bundle was instrumented only to expose the map for assertions.
No runtime exceptions were recorded. See the
[browser observations](2026-11-11-signal-map-browser.json),
[desktop view](../../../artifacts/traffic-light-regression-2026-11-11/traffic-lights.png),
[touch help](../../../artifacts/traffic-light-regression-2026-11-11/traffic-lights-mobile-help.png),
and [browser reproduction script](../../../artifacts/traffic-light-regression-2026-11-11/check-browser.mjs).
