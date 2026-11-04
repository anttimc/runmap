# First environment raster baseline, 2026-11-02

The Leppävaara pilot now has four independent 10 m raster fields and a
review-candidate overlay in the inspection map. This is an uncalibrated
mapped-context baseline, with no learned model or routing integration.
Current [usage](../../guide.md#experimental-environment-rasters),
[preparation commands](../../development.md#prepare-environment-rasters), and
[artifact contracts](../../reference/prepared-data.md#environment-raster-bundles)
are maintained separately. Outstanding validation belongs in the
[roadmap](../../roadmap.md#planned-environment-preferences-experiment).

## Source interpretation and reproduction

Pilot review changed the original three-field proposal: woodland inside parks
and reserves needs cover evidence; wetlands need an independent field; water
should prioritize lakes and sea shoreline rather than streams/ditches; road
exposure should describe relative traffic-related noise/discomfort and distinguish
less-busy streets from major roads. These requirements are captured in the
[experiment](../../environment-preferences.md#source-interpretation-after-pilot-review).

```sh
uv run --extra web --extra environment python -m runmap.environment.prepare \
  data/environment/raw/leppavaara-pilot
```

The default output is `data/environment/prepared/leppavaara-baseline-v1/`.
Use a fresh `--output` path when repeating. The final bundle was generated from
working-tree code based on `d9685cc`; its four preparation modules are identified
by SHA-256 `e95f6eecd267e26bb43c1fd1223d214a2837abfe8823181d3e12927bc361cd97`.
Two earlier development drafts were retained in separate local directories;
the default directory selects the final bundle.

| Identity | Value |
| --- | --- |
| Dataset | `1914d23b44d917c8d37474fc62320e3c63d6ed68fe897bbbc91110813caf6474` |
| Source GeoJSON SHA-256 | `bdffe305267a9d1cbb2940bec0b9ce19e0454680207b6ced07b3e0b49ea37143` |
| Source OSM base | `2026-11-02T07:36:17Z` |
| TIFF SHA-256 | `81da3cefc49ffc23b9054593d15ff12b70c9d3a023f1532f538cffb0b2e12c16` |

The dataset binds the full acquisition manifest as well as export identity,
configuration, implementation contents, and dependency versions. The manifest
records Python 3.14.6 on macOS ARM64, NumPy 2.5.4, SciPy 1.18.1, Rasterio 1.5.2,
Shapely 2.2.0, and Pillow 12.3.0. Configuration is
[`baseline-v1.json`](../../../config/environment/baseline-v1.json).

## Observations

The 6 × 6 km core contains 600 × 600 cells. Processing uses the retained 300 m
halo before cropping. Coverage uses 2 m center samples; shoreline/road distances
use occupied 10 m cells. All core cells passed the computational-support test;
this says nothing about real-world mapping completeness.

Selected context contained 933 woodland, 79 wetland, 203 park/reserve, three
explicitly tagged lake, seven coastline, and 67 other-water objects, plus 11,711
eligible motor-road objects. Categories overlap; counts include the halo and are
not routing-edge counts. No selected geometry was skipped for invalidity.
The three-lake rule is deliberately strict: other water types remain candidates
or excluded evidence, rather than silently becoming lake positives.

The final preparation took 2.53 seconds in one local run. The compressed TIFF is
6,101,817 bytes, evidence JSON 15,526,095 bytes, and the five previews total
433,049 bytes. These are one-run observations, not throughput, latency, or memory
guarantees. Cold/warm delivery timing and peak memory have not been benchmarked.

The four score channels stayed within [0, 1]. At a stream-side browser test click
near `60.211198, 24.778872`, forest surroundings were approximately 0.154,
lake/sea proximity 0, road exposure 0.161, and wetland presence 0. The source
inspector still listed the mapped stream. These illustrate the chosen rules;
they are not ground-truth ratings.

## Validation and limits

Eleven Python acquisition/inspection/raster checks passed, covering woodland
holes, overlapping wetlands, parks as candidates, stream exclusion, lake-boundary
and coastline distance, road weighting/decay and duplicate handling, access
exclusions, halo support, publication, cell sampling, and stale source/dataset
identity rejection. Fourteen frontend checks passed, including absent-raster
fallback and suppression of late responses after rapid clicks. TypeScript,
production build, and changed frontend-file formatting passed; the existing
shared-map chunk warning remains.

Headless Chrome rendered all five overlays, all four sample scores, and source
layers without runtime exceptions. Desktop/mobile source inspection and map-center
inspection passed. Local screenshots under `artifacts/environment-inspection/`
include each raster and the score panel. Visual review confirmed the forest,
lake/coastal, and road overlays align with the basemap; the basemap is independent
of the frozen extract and does not establish source completeness.

Missing forest inside parks is flagged rather than inferred. Wetland presence
does not establish path wetness. Waterways do not contribute to default water
proximity; coastline lines do not determine the sea side. Traffic uses class and
distance only, with no speed/lane refinement, flow measurements, acoustic
propagation, or shielding. Known-candidate hatching is not a full missing-data
map. Calibrated runner review, independent cover evidence, broader water-type
review, quantitative delivery measurements, and model comparisons remain open.
