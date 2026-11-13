# Glossary

These terms describe Runmap's data and code. Start with the example below, then
use the tables when reading the [architecture](architecture.md), code, or format
references. File paths beginning with `routing/` or `paths/` are relative to
`src/runmap/`; Rust paths begin with `rust/src/`.

## From a path on the map to a route

Imagine a path between junctions A and B. Runmap stores that physical stretch as
one **segment**, with a line describing its shape. A and B are **nodes**. If travel
is allowed both ways, the segment has two **arcs**: A to B and B to A.

Now place the start halfway along the path. For this request, the search adds a
temporary connection from the start to the appropriate end of the segment. This
is an **overlay edge**. It allows a partial traversal without changing the shared
network. The finished route records which segments it used, in which direction,
and how much of each segment it followed.

## Network and search

| Term | Meaning in Runmap |
| --- | --- |
| Network / graph | The connected pedestrian paths available to routing. A graph describes these paths as nodes and directed arcs. The prepared network also stores source tags, geometry, and measurements. |
| Node | A connection point in the routing graph, such as a junction or path end. A shape point along a line need not be a graph node. An OSM source node and a node retained in the routing graph are not interchangeable. |
| Physical segment / segment | A stretch of path with a stored direction and geometry. Opposite directions of travel share this physical record. See `paths/` and `rust/src/chunk_graph.rs` (`SegmentRef`). |
| Arc | A directed graph connection over a segment. Forward and reverse arcs refer to the segment's stored orientation, not compass direction or the runner's outbound/return leg. Access rules determine which arcs a request may use. |
| Edge | A connection considered by a search. In the Rust search, an `Edge` describes either a complete base arc or a temporary partial-segment connection. It includes direction, length, and the portion of the segment used. See `rust/src/endpoint_overlay.rs` and `chunk_search.rs`. Some older/reference code uses “edge” more generally for a directed connection; check the local type. |
| Geometry | The coordinates describing a segment's line. Several geometry points can lie between two graph nodes. |
| Topology / adjacency | Which nodes and arcs connect, and the indexes used to find those connections. Geometry describes where a path goes; topology describes how it connects. |
| Traversal | One use of a segment in a particular direction, possibly using only part of it. Route responses record a segment ID and start/end offsets. Repeated uses remain separate traversals. |
| Chainage / offset | Distance along a segment, measured from its stored start. Graph offsets use the routing segment's length; geometry chainage uses the projected line's length. These lengths can differ, so code sometimes carries both. Decreasing route offsets mean reverse travel. |
| Snapping / endpoint | Snapping finds an eligible position on the prepared network near a map location. The resulting endpoint can be a node or a position along a segment. `NumericEndpoint` describes that location. |
| Endpoint overlay | Temporary nodes and edges that let a request start or end inside a segment. The overlay belongs to the request and leaves shared graph data unchanged. |
| Leg / candidate | A leg is an ordered part of a run, such as home to turnaround. A candidate is a complete proposed run, with one leg for a one-way run or outbound and return legs for a round trip. |
| Policy / profile / eligibility | Rules that decide which paths a request may use, including access, surface, and stair choices. An eligibility mask stores these allowed/disallowed choices compactly. Here, “profile” can mean a routing policy; a route elevation profile is instead a series of heights along a route. |
| Cost / weight / penalty | Cost is the number the search minimizes. Length and enabled preferences contribute to it. A weight sets a preference's strength; a penalty increases cost for an undesirable stretch or movement. Cost is not a prediction of running time or personal effort. See [routing mathematics](reference/routing-mathematics.md). |
| Shape field / reuse cost | A shape field adds location-dependent costs to guide a run's shape. Reuse costs discourage following already-used stretches. Both depend on the current request, unlike prepared terrain observations. |
| Search state / label | A record of the best known cost and predecessor for a position during search. With signals enabled, position alone is insufficient: the search also tracks how it arrived, because that affects the next crossing cost. |
| Working area / window / bounds | The rectangular region selected for a search attempt. The service may retry with a larger window. This is separate from the full prepared coverage. |
| Frontier / unloaded connection | A place where a connection continues beyond the selected graph. It can trigger a larger search area. It is different from a real dead end or the edge of the source dataset. |
| Heuristic / lower bound | A heuristic estimates the remaining cost to guide search. A lower bound never exceeds that cost. The current Rust search uses a scaled straight-line bound; landmark-based bounds are proposed work. |
| Landmark / ALT | A proposed acceleration method using precomputed directed distances to and from selected nodes. These distances give lower bounds for A* search. It is not yet implemented in Runmap. See the [experiment](routing-scalability.md#landmark-experiment). |

## Preference data and traffic lights

| Term | Meaning in Runmap |
| --- | --- |
| Prepared routing input / channel | Observations computed before a request and used by routing: hills, environment, or signals. A channel is one of these groups. Request weights turn observations into costs. See `routing/prepared_inputs.py`. |
| Hill total / hill detail | Totals summarize a complete segment in each direction. Detail records measurements along smaller intervals, needed when a route uses only part of a segment. Terrain-quality values accompany the measurements. |
| Environment field / interval / support | A field is a value such as greenery, water, traffic, or wetland. Routing stores values over intervals along a segment. Support flags describe whether the model has enough surrounding data under its assumptions; they do not prove that real-world mapping is complete. |
| Signal | Traffic-light crossing information derived from OSM. In routing code, “signals” means prepared crossing records, not live red/green phases or real-time waiting estimates. Map signal points show source evidence separately from route passage counts. |
| Signal stage / passage | A stage is a prepared approximation of one separately charged crossing step. A passage is a route's counted use of such a step. Counts depend on the mapped evidence and preparation rules, not simply the number of signal points. |
| Gate / movement / turn | A gate charges a passage at a position along a segment, for specified travel directions. A movement record charges a particular incoming-to-outgoing segment transition at a node. Remembering the incoming segment avoids charging unrelated turns. See `routing/signals.py` and [traffic-light avoidance](traffic-light-avoidance.md). |
| Unresolved signal | Crossing evidence whose stage interpretation is uncertain. This is preparation uncertainty, not the traffic light's current state. |
| Observation / diagnostic | Information describing a route, such as environment values, unknown lengths, or hill coverage. It may be reported even when its preference has zero weight. Such reporting need not load the whole area's data before search. |
| Raster | A geographic grid of values, such as elevation or greenery. A rendered map tile is an image derived from data; routing uses prepared measurements rather than reading colors from that image. |

## Storage, runtime, and measurements

| Term | Meaning in Runmap |
| --- | --- |
| Native | The compiled Rust library loaded into the Python server through PyO3, exposed as `_routing_native`. It runs in the same process, not a separate routing service. See `rust/src/` and `routing/numeric_search.py`. |
| Numeric | The routing representation using integer IDs and typed number arrays instead of decoding Python/JSON records for every graph element. “Numeric” describes data; “native” describes the compiled implementation that uses it. |
| Reference backend | The Python search and cost implementation in `routing_reference/`. It supports comparisons with Rust and compatibility when a numeric artifact is absent. It shares prepared data and API contracts with the native backend. |
| Artifact / sidecar | An artifact is a prepared file or set of files. A sidecar is a companion file tied to another dataset, such as `signals.sqlite` beside `network.sqlite`. Files used together must have matching identities. |
| Identity / generation / manifest | Identity metadata identifies the source dataset, preparation models, and relevant content. A generation is a particular prepared version. A manifest records the files, sources, settings, and other metadata for a prepared dataset. Validation prevents mixing incompatible files. |
| Chunk / payload / buffer | A chunk is an independently loadable portion of prepared data. A payload is its serialized bytes; a buffer is the memory holding bytes or arrays during use. A chunk limits one loading unit, not the total memory a request can use. |
| Packed / compressed | Packed data stores records in a defined binary layout to avoid rebuilding many Python objects. Compression reduces stored bytes and requires decompression when read. These are separate properties: packed data need not be compressed. |
| Shared store / cache / LRU | The stores in `routing/numeric_store.py` and `routing/input_store.py` retain reusable graph and preference data within byte budgets. LRU means least recently used: older entries are removed first when space is needed. Exact-area caches also reuse an assembled graph for the same bounds. |
| Owner / reference count / eviction | An owner keeps a memory buffer alive. Active native graphs retain references to shared data, so removing it from a cache (eviction) does not free it while a search still uses it. A cache budget therefore does not by itself cap total process memory. |
| Lease / lock / admission | A lease reserves access to a resource, such as a database connection. A lock prevents conflicting work from running together. Admission limits restrict how much work starts at once. These control different parts of request handling. |
| Snapshot / worker | An exploration snapshot stores reached-node distances for later map requests. It belongs to one API worker process and cannot be retrieved from another worker or after restart. It retains result buffers independently of the graph cache. |
| Warm / cold | Warm requests reuse some cached data; cold requests must load it. Reports must say which cache: application data, operating-system file cache, or a newly started server process. A first request is not necessarily a cold-disk request. |
| Latency / serialization / RSS | Latency is elapsed time within stated measurement boundaries. Serialization converts results to response bytes, such as JSON. RSS is resident process memory reported by the operating system; it includes more than accounted cache bytes. |
| p50 / p95 / p99 | Latency percentiles: for example, p95 is the time at or below which 95% of measured requests completed. A median is p50. State sample count and workload when reporting them. |

Exact layouts, limits, and formulas belong in the linked technical references.
This glossary explains the terms without replacing those contracts.
