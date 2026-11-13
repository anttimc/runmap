# Traffic-light avoidance

Traffic-light avoidance makes routes through mapped pedestrian lights more
expensive, so the planner can choose a reasonable detour. It does not ban those
crossings or use live light timings. The environment traffic preference is
separate: it models surrounding road noise.

Routing counts prepared crossing steps, not simply signal points on the map.
A gate records a crossing along a segment; a movement records a crossing when
turning from one segment to another. The [glossary](glossary.md) explains these
terms, including stages, passages, and unresolved evidence. [Usage](guide.md#traffic-light-avoidance),
[preparation](development.md#prepare-traffic-light-passages),
[storage](reference/prepared-data.md#traffic-light-sidecar), and
[routing mathematics](reference/routing-mathematics.md#traffic-light-passages)
cover the detailed rules. Remaining calibration, field review, and
performance checks are in the [roadmap](roadmap.md#planned-routing-improvements).

## Evidence and attribution

`paths/graph.py` retains crossing, signal, island, railway, button, and direction
metadata on nodes and ways. Packed node records retain optional nested `tags`
without changing the existing flat access fields. New metadata changes the graph
identity; old prepared files remain readable but do not gain discarded tags.

`paths/signals.py` can instead recover tags from the exact raw JSON used to build
an existing network, checking its SHA-256 against the network's recorded source.
This permits adding the sidecar without resampling terrain or changing network
and numeric generations. A newer OSM snapshot is not an interchangeable source.

The current attribution model, `directed-signal-passages-v2`, compiles the full
network before selecting runtime windows:

- Connected parts of one crossing-tagged original way produce a canonical
  interior charging gate. Node and way evidence on that crossing is counted once.
- Adjacent explicit signal nodes connected by a segment, both with physical
  degree two and outside a crossing-way stage, produce one candidate gate.
  This uses shared topology, not a distance clustering threshold.
- Other explicit crossing nodes produce incoming/outgoing segment movements.
  Where two pedestrian links exist alongside road links, the pedestrian pair
  defines the resolved crossing movement. Other connections at an explicit signal
  crossing remain uncertain candidates, including mixed road/path turns. They
  must not become free alternatives to the mapped crossing. Mixed connections
  are retained even when a crossing-way gate also covers that node.
- Generic vehicle lights alone do not create pedestrian charges on road-only
  junctions. Generic lights with pedestrian links, conflicting evidence, railway
  crossings, island uncertainty, or ambiguous branching are reported as unresolved
  candidates. They receive the same finite penalty as resolved candidates.
- Direction tags use original way orientation where attribution is unambiguous.
  Ambiguous association with several ways uses unresolved evidence. Separately
  mapped crossing ways separated by an island remain separate stages.

These are mapped-stage approximations, not surveyed signal phases. A connected
crossing way can hide multiple independently controlled stages; a crossing split
across original ways can produce more than one candidate. The model does not
infer synchronization or reconstruct every poorly mapped sidewalk movement.
The charged gate is a canonical interior point, not a measured stop line.
Review these cases locally before interpreting passage counts as physical waits.

Signals On prunes immediate retracing within a shortest-path leg, preventing
short spurs from resetting the recorded approach to a junction. Requested
turnarounds start a new leg and still permit reversal. The
[Leppävaara–Pajamäki regression](archive/history/2026-11-11-signal-route-regression.md)
records why the initial attribution/search combination produced false zeros.

## Map inspection

The main map's **Environment → Traffic lights** control displays source signal
nodes from the exact routing snapshot. Red identifies explicit pedestrian signal
evidence; amber identifies other or uncertain evidence, including generic vehicle
lights and railway/conflicting tags. Selecting or hovering opens a point info
popup with the OSM node ID and retained tags. Control explanations and caveats
are in contextual help popups; the visible status is limited to counts and actions.
These are source nodes, not independently timed waits or route-event counts.

The display is independent of the raster environment layers and route preference.
It loads only the visible bounds at zoom 13 or higher. Missing map evidence,
request failures, and oversized viewports have explicit status messages.
It includes source nodes absent from the runnable graph; it does not infer that
every displayed vehicle light stops a pedestrian, or that unmarked areas have no
signals. Source mapping gaps remain possible.

The OSM documentation distinguishes
[crossing signal evidence](https://wiki.openstreetmap.org/wiki/Key:crossing:signals)
from [generic traffic-signal nodes](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dtraffic_signals).
[Direction](https://wiki.openstreetmap.org/wiki/Key:traffic_signals:direction)
is relative to the associated original way, and
[island mapping](https://wiki.openstreetmap.org/wiki/Key:crossing:island)
can represent separate crossing stages.

## Dated local inventory

### Reported tram-signal coverage gap, 2026-11-11

The runner reports pedestrian signals on the crossing from Slöörinpolku toward
Albergan kartano at the south end of Alberganesplanadi. The routing snapshot and
the current OSM API checked on this date both contain crossing way
[317553233](https://www.openstreetmap.org/way/317553233), with nodes
[5716179418](https://www.openstreetmap.org/node/5716179418) and
[5716179385](https://www.openstreetmap.org/node/5716179385) tagged only
`railway=tram_crossing`. Neither the way nor those nodes identifies signals.
Consequently this crossing produces no signal marker or routing penalty;
refreshing the same source alone will not repair it. A bare tram crossing does
not establish signal control, so it must not automatically become a traffic light.

The runner observation is not yet applied as a local correction. Resolving this
gap requires corrected source mapping or a provenance-tracked local correction
shared by map inspection and passage preparation. Any correction must review
the two track nodes as one potential crossing stage rather than automatically
charging twice.

Read-only investigation on 2026-11-10 examined the default
`data/paths/prepared/helsinki-capital-area/network.sqlite`, dataset ID
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
Its source was the October 25 Overpass acquisition at
`data/paths/raw/helsinki-capital-area/osm-network-boundaries-none.json`, SHA-256
`d3e549ef9618efe06bb7a4ce96b5e511de3154fd946329735cbc857f27ca28f9`.

| Same-source evidence | Count |
| --- | ---: |
| Prepared nodes, all matched to raw IDs | 516,651 |
| Prepared nodes with exact `crossing=traffic_signals` | 3,991 |
| Raw explicit signal nodes represented in the graph | 4,036 |
| Those losing all explicit pedestrian signal evidence during old preparation | 43 |
| Represented raw nodes with `crossing:signals=yes` | 179 |
| Represented raw nodes with `crossing:island=yes` | 2,944 |
| Retained source ways with `crossing=traffic_signals` | 469 |
| Retained source ways with `footway=crossing` | 6,595 |

Explicit evidence here means `crossing:signals=yes` or a semicolon-separated
`traffic_signals` token in `crossing`. Counts overlap and are not independent
passages or real-world coverage percentages. Representative previously lost
nodes are `12629942836`, `13206246914`, and `13206246915`.

The November 5 local regional JSON and complete Finland PBF also contain signal
metadata. No download was needed for implementation. Their snapshot differs from
the default routing database; use a coordinated network/dependent-artifact rebuild
when migrating. The PBF manifest identifies provider file `finland-261103.osm.pbf`;
November 5 is its download date.

The prepared local sidecar and the archived paired-node route were checked in the
[dated smoke report](archive/history/2026-11-10-traffic-light-avoidance.md).
That check does not establish regional calibration, surveyed attribution, or
performance acceptance. Earlier smaller-area fixtures remain historical in the
[original preferences audit](archive/history/30-routing-preferences-audit.md#signal-evidence-and-event-semantics).
