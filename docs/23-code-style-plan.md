# Python code style plan

Preserve runtime behavior and serialized formats while applying AGENTS.md.
Use responsibility boundaries to split functions; do not impose a line limit.

## Change list and sequence

1. Define precise shared data types, then annotate functions. Use data-only
   dataclasses for internal records, TypedDict for structured dictionaries,
   parameterized collections, and NumPy array types. Avoid Any.
2. Split graph construction into boundary collection, way filtering, junction
   detection, segment construction, and summary helpers.
3. Split terrain enrichment into sampling, quality flags, measurements, totals,
   and arc updates. Replace positional sample tuples with named data records.
4. Separate route candidate generation, assessment, ranking, and selection.
5. Separate CLI parser construction, input loading, execution, and reporting.
6. Separate configuration loading from focused section validation.
7. Move substantial operations out of data containers, including region tile
   enumeration, bounds validation/serialization, and cost validation.
8. Extract independent raster processing and persistence helpers from service
   classes. Keep behavioral classes where reusable state or lifecycle warrants it.
9. Refine acquisition, measurement inspection, contour rendering, experiments,
   and test helpers; complete their annotations.

Keep docstrings short and focused on purpose and rationale throughout.

## Item 1 implementation

- Start with shared routing dictionary schemas and the graph index/search API.
- Continue with routing profiles and exploration data, configuration records,
  measurements, and API data containers.
- Add NumPy dtype aliases and annotate terrain operations.
- Extend annotations through callers, acquisition, services, CLI, and tests.
- Validate each slice with existing relevant tests; preserve JSON field names
  and mutable collection behavior. Check annotation resolution and imports.
- Add static checking after the shared schemas and their callers are coherent;
  do not substitute broad dictionaries or casts for accurate domain types.

## Progress

- Item 1 in progress: shared routing schemas and search annotations are the
  first implementation slice and are implemented. Remaining modules are pending.
  The seven existing routing tests pass under Python 3.14, and search annotation
  resolution passes. Exploration tests are blocked by missing FastAPI in that
  interpreter; the existing virtual environment uses unsupported Python 3.9.
- Item 2 complete: graph construction delegates boundary collection, way
  filtering, junction detection, section splitting, segment/arc construction,
  and summary generation to typed helpers. Internal graph parts use a data-only
  dataclass; serialized graph summaries have a precise TypedDict schema.
  Seven routing tests pass. Six existing topology tests pass in isolation,
  excluding unrelated raster imports; 300 deterministic comparisons against
  the committed implementation produce identical graphs. The full graph test
  module remains unverified because Python 3.14 lacks Rasterio.
- Item 3 complete: terrain samples use a data-only dataclass with named fields.
  Sampling, quality flags, measurement construction, totals, segment/arc updates,
  and metadata have focused typed helpers. Measurement tests use the named records.
  All 82 tests pass in the rebuilt project .venv on Python 3.14.6, resolving the
  earlier dependency limitations. A further 100 deterministic terrain comparisons
  against the committed implementation produce identical outputs.
- Item 4 complete: candidate generation, assessment, pure ranking, and alternative
  selection use focused typed functions. Internal ranking records are separate
  from published candidate dictionaries. Constraint precision, generation order,
  deduplication, anchor diversity, and fallback selection are preserved.
  All 85 tests pass using uv in the project environment, including three new
  ranking/selection regression tests. Another 150 deterministic comparisons
  against the committed baseline implementation produce identical results.
- Item 5 complete: CLI parser builders are separate from execution. Path options
  use a data-only dataclass; source loading, cache verification, graph preparation,
  terrain attachment, route graph validation, and reporting use typed helpers.
  Region preparation and legacy crop configuration loading also have focused
  helpers. Command flags, defaults, output messages, and error boundaries remain
  unchanged. All 90 tests pass using uv, including five new CLI regression tests.
- Items 6–9 pending.
