# Environment source inspection progress, 2026-11-02

The Leppävaara pilot now has an interactive source inspection page at
`/environment.html`, linked from the running map. The user reviewed the
visualization positively; geographic completeness and environment-score quality
have not been established. Current usage belongs in the
[guide](../../guide.md#environment-source-inspection), setup in
[development](../../development.md#inspect-environment-source-geometry), and
remaining tasks in the
[roadmap](../../roadmap.md#planned-environment-preferences-experiment).

## Delivered and checked

The page displays independently toggleable woodland, water polygons,
waterways/coastline, wetlands, parks/nature reserves, buildings, mapped paths,
and major roads. It shows the projected pilot core and enclosing query envelope.
Click inspection deduplicates rendered objects by OSM type/ID, lists overlapping
layer memberships and source tags, and links to OpenStreetMap. A map-center
button supports inspection after keyboard panning.

The API serves the existing frozen GeoJSON and manifest rather than fetching
live OSM data. The export checksum binds the feature URL to its source identity;
changed identities require reload. The source remains the
[first acquisition](2026-11-02-environment-acquisition.md), with OSM base timestamp
`2026-11-02T07:36:17Z` and export SHA-256
`bdffe305267a9d1cbb2940bec0b9ce19e0454680207b6ced07b3e0b49ea37143`.

Validation performed on the working tree:

- TypeScript checking, production multi-page build, and changed frontend-file
  formatting passed. The build retains the existing large shared-map chunk warning.
- Six Python acquisition/inspection checks passed, including polygon holes,
  projected-boundary fidelity, incomplete acquisitions, and stale identity rejection.
- Twelve existing frontend regression tests passed.
- Headless Chrome loaded the real pilot without runtime exceptions. A test click
  listed overlapping woodland and waterway objects; disabling layers removed
  those results. Map-center inspection worked. Desktop and 390 × 844 mobile
  screenshots were saved locally under `artifacts/environment-inspection/`;
  the mobile document had no horizontal overflow.
- Relative documentation targets and whitespace checks passed.

These are implementation and smoke checks, not quantitative delivery-latency
measurements, full accessibility acceptance, or representative geographic review.
The page shows source evidence only; no raster fields, learned scores, or routing
preferences were enabled.

## Road-class finding and next work

The user identified Leppävaarankatu as more pleasant than a motorway. In the
downloaded snapshot, matching Leppävaarankatu ways retain `highway=secondary`,
`maxspeed=40`, and mostly `lanes=2`, with some one- and three-lane sections.
This is a source-tag observation and user calibration example, not a measured
traffic or noise assessment.

The current major-road layer selects motorway, trunk, primary, secondary, and
tertiary classes plus their link roads, but draws all of them with the same red
line style. The data distinguishes classes even though the overview does not.
Source tags can already be expanded in click inspection.

The next inspection change is class-specific color/width styling with a legend
and prominent class, speed, and lane summaries. The later road-exposure baseline
should retain class distinctions and distance decay, then evaluate whether speed
and lane evidence improve calibration. Missing attributes remain explicit; road
class alone does not determine pleasantness. Keep Leppävaarankatu versus nearby
motorway surroundings as a local review case.
