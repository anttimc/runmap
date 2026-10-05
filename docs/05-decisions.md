# 5. Decisions and open questions

Initial planning record: 2026-09-05.

## Agreed direction

| ID | Decision | Reason |
| --- | --- | --- |
| D1 | Explore runnable-path hilliness for easy runs and hill training | Primary user goals |
| D2 | Use OSM and open elevation data | Open inputs suitable for experimentation and eventual hosting |
| D3 | Use Python for backend and processing orchestration | Easy personal experimentation |
| D4 | Keep data access, profiles, mathematics, and visualization modular | Components must be understandable and replaceable |
| D5 | Design for a maximum viewport around 20 km × 20 km | User's intended UI scale |
| D6 | Keep the first web UI simple | Map exploration is the primary interaction |
| D14 | Study Leppävaara in Espoo, Finland | User selected the area |
| D15 | Start with terrain-gradient magnitude and direction | User selected the first visualization; path analysis follows |
| D16 | Investigate nonlinear smoothing that limits blur | User wants noise reduction with hill features retained; final method is open |
| D17 | Retain Gaussian σ=4 m and σ=8 m as current baselines | First synthetic comparison shows useful but different accuracy/detail tradeoffs |
| D18 | Add spectral thin-plate smoothing, parameterized by half-amplitude wavelength | It preserves planar trends, penalizes curvature, and is fast on the complete regular elevation grid; the 40 m trial leads the current synthetic comparison |
| D19 | Use spectral biharmonic thin-plate smoothing with a default 40 m half-amplitude wavelength | User selected it as the working method after smooth and sharp-feature comparisons; the setting remains adjustable in the UI |
| D20 | Compute interactive smoothing in Python and update a MapLibre image source | The full raster is fast enough locally, this keeps one numerical reference, and it avoids transferring source elevation or adding browser FFT/WASM complexity |
| D21 | Represent wider coverage as named regions over one shared 5 km EPSG:3067 elevation grid | Regions can overlap without duplicating downloads, and processing remains bounded |
| D22 | Retain downloaded MML source versions and fetch each required source tile once | The elevation changes slowly, reproducibility matters, and normal map navigation must not depend on live MML requests |
| D23 | Preload known public regions and keep anonymous acquisition disabled initially | Capital-region/default rendering should be predictable, while uncontrolled panning must not create unbounded downloads |
| D24 | Precompute the default 40 m product and generate a finite set of other hosted presets lazily | Default navigation stays fast without multiplying full-coverage storage by every slider position |
| D25 | Deliver hosted terrain as versioned Web Mercator XYZ tiles while calculating in EPSG:3067 | MapLibre/CDN delivery and metre-based numerical analysis have different grid requirements |
| D26 | Use a grid-aligned 10 × 10 km Leppävaara region (`E 370000–380000`, `N 6675000–6685000`) as the first wider coverage | It contains the original study area and has exactly four 5 km analysis cores; broader capital-region coverage is deferred |
| D27 | Use a 512 m processing halo for the first tiled implementation | At 40 m, trusted internal joins agree with a whole-domain reference to the `0.0001` slope-cache quantization; longer hosted wavelengths still require validation |
| D28 | Use fixed 5 m source-elevation contours, emphasizing each 25 m contour, instead of gradient arrows | Contour orientation shows the gradient axis and spacing shows terrain shape without adding a competing field of symbols; elevation labels can be added later if gradient sign proves necessary |
| D29 | Expand the public Leppävaara region to the grid-aligned 40 × 40 km bounds `E 360000–400000`, `N 6660000–6700000` | This places Leppävaara near the center and provides roughly 20 km of coverage in every cardinal direction while retaining complete 5 km analysis cores |

## Initial implementation defaults

These follow the stack discussion and can be revised with evidence.

| ID | Default | Revisit when |
| --- | --- | --- |
| D7 | Rasterio/GDAL, NumPy, Shapely, pyproj | A required operation is unsupported or a measured bottleneck remains |
| D8 | FastAPI as a thin API layer | Actual API requirements justify a different approach |
| D9 | TypeScript, MapLibre, plain HTML/CSS, Vite | UI complexity makes a component framework useful |
| D10 | Local files before a database server | Updates or query patterns justify PostGIS |
| D11 | Cached profiles and prepared map delivery | Benchmarks identify a better serving strategy |
| D12 | Maanmittauslaitos 2 m model for Finland | Study-area choice or coverage requires another source |
| D13 | Custom compiled code is optional | Profiling or an explicit learning objective provides a bounded task |

## Open questions

| ID | Question | Needed by |
| --- | --- | --- |
| Q1 | What exact bounds and processing buffer should represent Leppävaara? City/area resolved by D14. | M0 |
| Q2 | Which smoothing parameters best retain terrain features? Gradient visualization selected by D15; compare methods before choosing. | M0–M2 |
| Q3 | Which familiar locations form the validation set? | M0 |
| Q4 | What counts as runnable, including access, surfaces, and stairs? | Later path-analysis milestone |
| Q5 | Which baseline machine/browser should performance targets describe? | M2–M3 |
| Q6 | Refine magnitude colours and contour visibility; decide whether elevation labels or location details are needed to communicate gradient sign. | M3 |
| Q7 | Which basemap provider and hosting arrangement should be used? | M3; confirm production terms before M5 |
| Q8 | Is route drawing/import the next feature after path exploration? | M4 |
| Q9 | Is Odin or Go experimentation a separate learning goal? | Optional M6 |
| Q10 | What exact private Jämsä bounds should be configured, and when should public coverage expand beyond the 40 km Leppävaara region? | Later wider-coverage implementation |
| Q11 | Does the 512 m halo remain sufficient at the longest 100 m hosted wavelength? It passed the 40 m internal-join check to cache quantization. | Before treating every hosted preset as validated |

## Reference project

[Steepest Road in Town](https://xangregg.github.io/steepest/) and its [source/documentation](https://github.com/xangregg/steepest) are references for area-wide gradient visualization and replaceable processing components.

Its documented road selection focuses on drivable roads and excludes paths and tracks. Runmap needs its own pedestrian/path selection and validation. Referencing the project does not commit us to its formulas, thresholds, or implementation.

## Not decided

No exact performance promise, route-hilliness formula, path sample interval, automatic routing algorithm, hosting provider, or compiled-language requirement has been adopted. The working terrain smoother and default are D19; hosted storage and delivery defaults are detailed in the [region and hosting design](11-region-and-hosting-design.md) and remain subject to the validation named there.
