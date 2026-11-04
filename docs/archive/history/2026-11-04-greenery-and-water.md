# Weighted greenery and expanded water, 2026-11-04

Following user review, the displayed Forest surroundings score was broadened to
Greenery. Woodland contributes at weight 1 through its existing 100 m surroundings
mean. Parks and nature reserves contribute at weight 0.5 only through their cell's
polygon coverage. The maximum of the three weighted components prevents overlapping
designations from adding together. The separate Park coverage layer is retained.

Water proximity now includes every mapped water-polygon boundary and the coastline,
including ponds, basins, reservoirs, unclassified water and polygonal waterways.
Line streams/ditches remain excluded. Water interiors remain distinct from shoreline
proximity. Parks/reserves and non-lake water are accepted inputs, so review hatching
now shows limited road context rather than those source categories.

Current usage and formulas are maintained in the
[guide](../../guide.md#experimental-environment-rasters) and
[field contract](../../reference/prepared-data.md#greenery-and-water-version-3).
The [experiment](../../environment-preferences.md) and
[roadmap](../../roadmap.md#planned-environment-preferences-experiment) retain
outstanding calibration and validation. No routing preferences were enabled.

```sh
uv run --extra web --extra environment python -m runmap.environment.prepare \
  data/environment/raw/leppavaara-pilot
```

The default is now [`baseline-v3.json`](../../../config/environment/baseline-v3.json)
and `data/environment/prepared/leppavaara-baseline-v3/`. V1/v2 bundles remain
selectable and were not overwritten. Dataset identity is
`dec59449bad597c8e13c6a212f090d58678ff43fc3112e56dd7c2f6869ae258f`;
the implementation hash is
`38f21461b51061d43c7df78365ae7ef1271cd70c255b4a56b7e8ed85bf5dbbde`.
The TIFF checksum is
`31df035dedf38a1f6c30382c1a156f4f99dc0cd73100caa4d4dcb69a65f98626`.

The same frozen extract supplies 933 woodland, 189 park, 14 reserve, three tagged
lake and 68 other-water objects. Categories overlap; one additional polygonal
waterway is recognized compared with the previous narrower water interpretation.
Preparation took 2.93 seconds in one local run; the TIFF is 9,708,979 bytes.
These are dated observations, not performance guarantees.

Compared with the preserved v2 bundle, greenery is raised above woodland context
in 59,914 core cells and water proximity increases in 19,133 cells. A park-only
live API sample returned greenery 0.5. Raw woodland context, park coverage,
wetland presence, and road exposure were checked to be bit-identical to v2.
Diagnostic forest/lake channels remain available in the sample details.

Eighteen Python environment checks and fifteen frontend checks passed. Tests
cover designation footprint limits, holes, stronger woodland, overlap maximum,
all-water inclusion, line-water exclusion, deduplicated source evidence, version
identity, and older-bundle controls. TypeScript and production build passed with
the existing shared-map chunk warning. Source mapping completeness and selected
greenery weights still require independent/local review.
Headless Chrome rendered Greenery, expanded Water proximity, all score fields,
and updated context messages without runtime exceptions. The saved central test
click showed Greenery 0.5 from its park designation while the diagnostic woodland
context remained approximately 0.154. Local screenshots are saved under
`artifacts/environment-inspection/`.

Later on 2026-11-04, user review removed Park coverage as a standalone raster
control and score. Park/reserve fractions remain internal Greenery inputs and
raw inspection diagnostics; the underlying raster values were not changed.
