# Environment tile implementation check — 2026-11-05

This is implementation evidence using the existing Leppävaara pilot, not
capital-area data acceptance. Main-map controls, XYZ delivery, and bounded
regional preparation are implemented. The 40 × 40 km source acquisition did not
complete: the default Overpass endpoint returned 502 capacity errors on the
initial run and retry; alternate endpoints timed out. A small diagnostic request
was also rejected (406). No full-area coverage or preparation measurements are
claimed. The retained acquisition request is not a published source bundle.

## Automated checks

- 22 Python environment tests passed, including acquisition identity/resume,
  baseline fields, regional block agreement against whole-grid scores,
  incremental TIFF publication, zero versus missing coverage, and invalid/stale
  tile requests.
- 17 frontend tests passed, including independent layer toggles and opacity and
  missing-bundle behavior. Production build and frontend formatting passed.
- Regional fixture tests use explicit woodland/water/wetland polygons and a
  motorway across block boundaries. They do not validate real OSM completeness.

## Real-pilot browser smoke check

The temporary API used `RUNMAP_ENVIRONMENT_TILE_DIRECTORY` set to
`data/environment/prepared/leppavaara-baseline-v3` on port 8002, independently of
the normal server and the capital-area default. Headless installed Chrome used
DevTools port 9233. Desktop 1400 × 950 and mobile 390 × 844 screenshots, request
statuses, and a reproduction script are retained in the local ignored directory
`artifacts/environment-preferences/2026-11-05-tiles/`.

All five controls were installed, overlapping fields rendered below roads/labels,
and there were no JavaScript exceptions or mobile horizontal overflow. This
smoke check is not exhaustive keyboard/touch, rapid-pan, stale-response, or route
overlap acceptance. The mobile view motivated moving explanatory detail into
expandable help so the controls appear earlier in the scrolling panel.

## Local API sample

[Raw observations](2026-11-05-environment-tiles.json) identify the pilot bundle,
platform, Python version, per-request sizes, and timings. Thirty tile pairs cover
all five layers at zooms 11–16 near 24.81° E, 60.22° N. Each URL was fetched twice
with Python `urllib.request` against localhost:8002. First-request timings are not
cleared OS-cache timings; cached repeats use the server's PNG cache. Existing
browser requests may have warmed some high-zoom entries.

First-request median/p95 were 40.26/70.41 ms; repeats were 0.66/0.75 ms. These
observations do not establish regional preparation cost, concurrent performance,
or browser interaction latency. Remaining data and acceptance tasks stay in the
[roadmap](../../roadmap.md#next-environment-exploration-and-costs).
