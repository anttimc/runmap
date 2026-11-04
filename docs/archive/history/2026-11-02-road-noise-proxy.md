# Reference-car road-noise proxy, 2026-11-02

The road raster now uses ideal geometric sound-intensity spreading, logarithmic
levels, and a configurable discomfort scale. It replaces the initial linear
300 m exposure curve as the default. The previous bundle and configuration are
preserved. Forest, water, and wetland arrays are bit-identical to that version.
Current usage is in the [guide](../../guide.md#experimental-environment-rasters),
equations in the [field contract](../../reference/prepared-data.md#road-noise-proxy-version-2),
and commands in [development](../../development.md#prepare-environment-rasters).

## Model and assumptions

The user's proposed direction was to use car intensity at a reference distance
and decibels as a discomfort proxy. The source-type powers were corrected before
implementation: ideal line-source intensity falls as `1/r`, point-source
intensity as `1/r²`. They lose approximately 3 and 6 dB per distance doubling,
respectively. These are intensity laws, not pressure-amplitude laws.
[FHWA's measurement handbook](https://www.fhwa.dot.gov/Environment/noise/measurement/handbook.cfm)
supports the ideal spreading relationships.

Defaults in [`baseline-v2.json`](../../../config/environment/baseline-v2.json):
reference distance 10 m, assumed car intensity `10⁻⁵ W/m²`, intensity reference
`10⁻¹² W/m²`, hence nominal reference level 70 dB. Class factors scale intensity;
they are the original class priors interpreted as relative activity/source
strength rather than linear discomfort amplitudes. Motorway/trunk use ideal
line spreading; other classes use a sparse passing-car point-spreading envelope
at the nearest road point. Actual traffic steadiness is not known from class.

Discomfort maps 45–75 modeled dB to [0, 1]. These thresholds, the reference-car
level, and all class factors are configurable experimental assumptions, not
measured emission levels, accepted annoyance thresholds, or dBA predictions.

| Distance | Motorway dB / score | Secondary dB / score | Local motor road dB / score |
| --- | --- | --- | --- |
| 10 m | 70.00 / 0.833 | 67.78 / 0.759 | 63.01 / 0.600 |
| 50 m | 63.01 / 0.600 | 53.80 / 0.293 | 49.03 / 0.134 |
| 100 m | 60.00 / 0.500 | 47.78 / 0.093 | 43.01 / 0 |
| 300 m | 55.23 / 0.341 | 38.24 / 0 | 33.47 / 0 |

These are analytical curve examples, not observations at those distances.
Distance is clamped to 10 m in the default near field. The model keeps the
dominant road class, rather than summing OSM way pieces or all acoustic sources.
This preserves invariance to repeated geometry and road segmentation.

Configuration update, 2026-11-03: residential intensity factor was changed to
zero and tertiary to 0.1 following user review. The table above describes the
original 2026-11-02 configuration. Its bundle was preserved at
`data/environment/prepared/leppavaara-baseline-v2-2026-11-02/`; the manifest
contains the original configuration. The default v2 directory now uses the
updated factors, so repeating the current default command uses the revised model.

Further review on 2026-11-03 found that Kilonrinne itself was excluded as
residential, but nearby service-tagged driveways dominated the remaining score.
The user then set service and unclassified factors to zero. The intermediate
bundle is preserved at `data/environment/prepared/leppavaara-baseline-v2-2026-11-03-residential-zero/`;
the current v2 default uses the additional exclusions. Larger roads can still
contribute exposure over those excluded streets.

Nuotiotie review on 2026-11-03 identified `living_street` as another remaining
local source class. Its factor was subsequently set to zero. The preceding
bundle is retained at `data/environment/prepared/leppavaara-baseline-v2-2026-11-03-service-zero/`.
Nuotiotie's own contribution is excluded, while the modeled Turunväylä/ramp
influence at roughly 250 m remains a separate calibration case.

Later on 2026-11-03 the motorway/trunk exponent was increased from 1 to 1.5
following user feedback: intermediate attenuation, approximately 4.5 dB loss per
doubling, rather than an ideal line source. Configuration validation now accepts
finite exponents throughout [1, 2]. The preceding ideal-line bundle remains at
`data/environment/prepared/leppavaara-baseline-v2-2026-11-03-local-zero/`.
The original curve table and measurements below remain historical; current
motorway/trunk influence reaches the 45 dB display floor at about 464 m with
the unchanged assumed 70 dB at 10 m. Actual ground and barrier effects remain
unmodeled; exponent 1.5 is an effective calibration assumption.
The regenerated API sample at Nuotiotie's recorded living-street midpoint
dropped from approximately 0.373 to 0.143 (modeled level 49.30 dB). The new
dataset identity is `62e3ef82e88626334509a22a016561a0779e8f20130a0a4c56d7656bf13383c0`.
Fourteen Python and fourteen frontend checks passed, including fractional
attenuation, rejection of invalid exponents, and correct source-type metadata.

There is no artificial 300 m zero cutoff. Under these defaults a local motor
road reaches the display floor at about 79 m, secondary at 138 m, and an ideal
motorway line source at 3.16 km. The latter can make a broad faint motorway
background persist. Finite-road, terrain, barrier, ground, atmospheric, and
traffic-flow effects are not modeled. The new curve does not imply that all
motorway influence should become geographically smaller.

## Artifact and checks

```sh
uv run --extra web --extra environment python -m runmap.environment.prepare \
  data/environment/raw/leppavaara-pilot
```

Output: `data/environment/prepared/leppavaara-baseline-v2/`. Default API selection
now points there; use `RUNMAP_ENVIRONMENT_RASTER_DIRECTORY` to inspect an older
bundle. Existing source data retains the acquisition identity recorded in the
[first raster report](2026-11-02-environment-rasters.md).

| Identity | Value |
| --- | --- |
| Dataset | `ef3bbbb028ce8aebaf2aa3412bf610ec64394c640506db220ce0f1417bb9e6b2` |
| Implementation SHA-256 | `da5627026c158ee1db4d085018c9ffc30171ef9856d6bdb10aafe08cd4fd63db` |
| TIFF SHA-256 | `586aec83987a783ea086b4e7f3bab75ea44caec40645c72b813e17a507ca1722` |

The final bundle took 2.54 seconds in one local preparation; TIFF size is
7,100,160 bytes. Across the core, modeled dominant-road levels ranged from
46.13 to 70 dB and discomfort from 0.038 to 0.833. These reflect the assumed
reference and free-field proxy; they are not measured noise statistics.

Acoustic propagation can exceed the acquisition halo. A new context flag tests
whether an exterior source could dominate under configured source-strength
limits, using a conservative cell-diagonal margin. It passed for 84.75% of
core cells. Remaining cells add amber context hatching and an inspector message.
This is a conditional model-support check, not a real-world completeness claim.
The raster retains known-source estimates in unsupported cells and can
underestimate exposure there. Source dB and exterior bounds are separately
inspectable; the retained-halo flag alone is not full acoustic support.

Fourteen Python acquisition/raster/inspection checks and fourteen frontend
checks passed. Added tests cover reference intensity, correct line/point doubling
loss, near-field clamp, class intensity factors, score normalization, invalid
configuration, duplicate geometry, propagation beyond 300 m, and exterior-context
risk. The preserved v1 linear model remains covered by regression checks.
Production TypeScript/build checks passed, retaining the existing shared-chunk
size warning. Headless Chrome displayed the new dB inspector, context flag, and
all overlays without runtime exceptions. Local screenshots remain under
`artifacts/environment-inspection/`.

At the saved stream-side inspection point, the updated road score was about
0.294 at a modeled 53.8 dB; the three other scores remained unchanged. Independent
source-level calibration and runner judgments remain outstanding in the roadmap.
