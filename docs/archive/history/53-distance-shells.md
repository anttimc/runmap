# Distance shell generation prototypes

Measured on 2026-10-25 on macOS ARM64, Python 3.14.6, using the same prepared
Helsinki dataset and Leppävaara start `(24.795, 60.216)` as the distance-tile
report. [Raw samples and configuration](53-distance-shells.json) retain three
repeats per budget/resolution. These are local diagnostics with uncontrolled OS
caches, not production percentiles. No production endpoint or frontend changed.

## Grid prototype

The prototype reads numeric geometry buffers directly, without loading display
interval SQL/JSON. It resolves directed segment endpoint distances, includes
partial segment starts, samples geometry at half the grid spacing, and assigns
each grid position its nearest sample's network distance within 150 m.
ContourPy extracts filled 500 m bands. Holes and disconnected components survive;
zero-area rings at exact thresholds are removed. No independent polygon
simplification is applied, preserving shared contour boundaries.

Median time from numeric geometry reads through WGS84 GeoJSON serialization,
excluding Dijkstra, gzip, HTTP, and browser rendering:

| Maximum distance | 50 m grid | 25 m grid | 50 m gzip payload |
| --- | ---: | ---: | ---: |
| 2 km | 24 ms | 31 ms | 18 kB |
| 5 km | 153 ms | 207 ms | 147 kB |
| 10 km | 510 ms | 706 ms | 600 kB |
| 20 km | 1,277 ms | 1,910 ms | 2,051 kB |

The totals cover **all** bands and the entire reached area, rather than one tile.
They cannot be used as a controlled speedup ratio against the historical tile
measurements. Gzip itself adds approximately 1, 10, 40, and 140 ms respectively
for the 50 m outputs. The 20 km/25 m output has about 288,000 vertices and is
11.2 MB before gzip, so polygon display/transfer still requires measurement.

At 20 km/50 m, median stages were approximately 442 ms reading numeric chunks,
264 ms sampling, 390 ms building the grid, 36 ms extracting contours, 61 ms
projecting coordinates, and 65 ms encoding JSON. Stage medians need not sum to
the median total. All 40 bands in the saved 20 km/25 m GeoJSON were valid under
Shapely validation.

## Delaunay comparison and boundary-focused reduction

SciPy/Qhull provides Delaunay triangulation; Matplotlib's public `tricontourf`
extracts filled contours directly from the triangular mesh. Matplotlib is already
an experiments extra. ContourPy handles structured grids, not these triangles.

Both methods use the same reached edge samples within each comparison. Uniform
mesh reduction retains one minimum-distance representative per occupied 50 m
cell. Boundary-focused reduction uses that density within 50 m of each 500 m
threshold, including the final requested boundary. Elsewhere it keeps minimum
and maximum distance representatives per 200 m cell as coarse interior/exterior
anchors. This avoids discarding all points away from the boundaries.

Triangles with any side longer than 300 m are masked. This limits large spatial
bridges but does not enforce water or other network barriers.

Median **surface construction and contour extraction only**, excluding shared
geometry reads/sampling, projection, JSON, gzip, Dijkstra, and delivery:

| Maximum distance | Grid, paired with filtered mesh | Uniform fresh mesh | Boundary-focused fresh mesh |
| --- | ---: | ---: | ---: |
| 2 km | 7 ms | 10 ms | 7 ms |
| 5 km | 42 ms | 49 ms | 33 ms |
| 10 km | 162 ms | 200 ms | 118 ms |
| 20 km | 436 ms | 595 ms | 328 ms |

Mesh times include point reduction, triangulation, masking, and contour
extraction. The grid column comes from the boundary-focused experiment; uniform
mesh measurements were run separately. Each experiment retains its own paired
grid timings in the raw report. No raster image drawing was timed; the public
Matplotlib API does include contour artist/axes setup.

At 20 km, filtering reduced mesh sites from 146,629 to 70,740. In the filtered
experiment, full geometry-read-through-contour time was 1,298 ms for the grid
and 1,190 ms for the mesh, an approximately 8% reduction. Site reduction itself
took 101 ms, triangulation 142 ms, and extraction 73 ms. Repeating extraction
with the same prepared mesh/distances took 71 ms, an optimistic lower bound for
mesh reuse that excludes updating vertex distances and masks.

The grid and meshes interpolate differently and do **not** produce equivalent
boundaries. Point reduction is approximate. Mesh GeoJSON assembly and validity
were not benchmarked: these results stop at compound contour paths. Grid
GeoJSON timings above include polygon assembly and serialization.

## Interpretation and remaining work

The grid prototype makes small explorations inexpensive without path vector
tiles. Boundary-focused triangulation improves the surface stage at larger
budgets, but geometry reads and sampling remain substantial shared costs.
Reusing prepared geometry/sites/connectivity is a useful next experiment.

Both prototypes visualize an approximate geographic envelope of network
distance. Neither adds an off-network travel cost or has water/coverage masks
yet. Reached-only samples also approximate the outermost boundary; a production
mesh should include unreached exterior anchors and respect barriers. Visual
quality and error around bridges, lakes, one-way paths, sparse networks, and
the dataset boundary need comparison before selecting the renderer. A mesh
cannot safely infer these constraints from threshold crossing points alone.

Eleven new tests cover directed/partial edge distances, bent geometry,
unreachable/excluded edges, grid bounds, shell holes and validity, exact
threshold extrema, and mesh sample reduction. All seven existing numeric
exploration tests also pass.

## Reproduction

```sh
uv run python -m runmap.experiments.benchmark_distance_shells \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --report artifacts/distance-shells/benchmark.json \
  --geojson artifacts/distance-shells/shells-20km-25m.geojson

uv run python -m runmap.experiments.benchmark_distance_mesh \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --report artifacts/distance-shells/mesh-50m.json

uv run python -m runmap.experiments.benchmark_distance_mesh \
  data/paths/prepared/helsinki-capital-area/network.sqlite \
  --boundary-window 50 \
  --report artifacts/distance-shells/mesh-boundary-50m.json

uv run python -m unittest discover -s tests -p 'test_distance_*.py'
```

The grid benchmark regenerates a snapshot search each repeat, allowing the
existing graph cache to operate. The mesh benchmark prepares one search per
budget and regenerates each surface from that snapshot. Neither caches sample,
grid, or polygon results. A separate mesh extraction timing deliberately reuses
the already built mesh. `--exact-sites` is available for a mesh that only merges
duplicate coordinates; it was not used in the recorded comparisons.
