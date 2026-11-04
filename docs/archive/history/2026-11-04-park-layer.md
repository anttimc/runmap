# Separate park coverage, 2026-11-04

The inspection map now includes a Park coverage raster and separate Parks and
Nature reserves source toggles. Current usage belongs in the
[guide](../../guide.md#experimental-environment-rasters), the field contract in
[prepared data](../../reference/prepared-data.md#environment-raster-bundles), and
commands in [development](../../development.md#prepare-environment-rasters).

Only `leisure=park` polygon geometry contributes to the park field. Coverage is
the union fraction of each 10 m cell, estimated at 2 m sample centers, with holes
retained. Parks may overlap forest, wetlands, or other environment attributes.
The new layer describes park footprint; it does not apply the forest layer's
100 m surroundings average.

Nature reserves are classified separately. Their designation alone does not
raise park or forest coverage; explicit woodland inside them still contributes
to forest. Both parks and reserves remain candidates for reviewing missing
woodland evidence. The
[OSM nature-reserve definition](https://wiki.openstreetmap.org/wiki/Tag:leisure=nature_reserve)
describes protection for ecological or other special interest rather than a
specific land cover.

The same frozen Leppävaara extract supplies 189 park and 14 reserve polygons,
previously reported together as 203 designation polygons. The regenerated bundle
has dataset identity
`9b75fad6cfedc9dae57567f4d541b6fb8cf2ed06e95c7fc8c79443a9acb099e3`.
It contains five score previews plus candidate hatching. Park coverage is nonzero
in 85,048 core cells. All previously published bands were checked to be
bit-identical to the preceding bundle, including forest evidence gaps and road
attenuation. The previous bundle remains at
`data/environment/prepared/leppavaara-baseline-v2-2026-11-03-attenuated/`.

Preparation took 2.67 seconds in one local run; this is not a delivery-performance
guarantee. The manifest records source, implementation, dependencies, and artifact
checksums. The running API served `park.png` and returned park coverage 1 with
Heiniityn puisto (`way/425545750`) as evidence at a sampled full-coverage cell.

Sixteen Python environment checks and fifteen frontend checks passed. Coverage
includes park holes, woodland overlap, reserve-only areas, park sampling/evidence,
and older bundles without a park layer. TypeScript and production build passed,
retaining the existing shared-map chunk warning. Source evidence and geographic
completeness still require independent review; no routing preference is enabled.
Headless Chrome rendered the park overlay, all five score fields plus modeled
road dB, and separate park/reserve controls without runtime exceptions. A local
preview is saved at `artifacts/environment-inspection/raster-park.png`.
