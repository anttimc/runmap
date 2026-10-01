# 6. Leppävaara terrain-gradient experiment

Status: active experiment. A provisional 6 × 6 km elevation crop has been downloaded and structurally inspected. Raw, Gaussian, spectral thin-plate, and one nonlinear preset have been compared on a central 2 × 2 km crop and synthetic terrain; see the [first results](08-smoothing-comparison-results.md). More representative validation and parameter exploration remain.

## Purpose and scope

Study Leppävaara, Espoo, Finland. First display the magnitude and direction of terrain gradients, and compare ways to reduce derivative noise without excessive loss of hill detail. Runnable-path analysis follows this experiment.

Exact bounds remain open. Start with a small representative crop for numerical comparisons, then expand to the study area. Download and process surrounding terrain beyond the displayed crop to control boundary effects. The supported maximum viewport remains approximately 20 km × 20 km.

## Mathematical conventions

Let `z(x,y)` be elevation in metres, with `x` increasing grid-east and `y` grid-north in EPSG:3067.

```text
g = (gx, gy) = (dz/dx, dz/dy)
s = sqrt(gx² + gy²)
slope_percent = 100 s
slope_degrees = atan(s) × 180/pi
uphill_grid_bearing = atan2(gx, gy) × 180/pi, wrapped into [0,360)
downhill_grid_bearing = (uphill_grid_bearing + 180) mod 360
```

The gradient points uphill. Standard terrain aspect commonly describes downhill direction; label the choice explicitly. Bearing above is clockwise from grid north, not automatically true north. Transform vector directions consistently when drawing on a reprojected/rotated map; apply meridian convergence if displaying true compass bearings.

Use the raster affine transform and physical cell spacing. North-up raster rows usually increase southward, so an array row derivative has the opposite sign to a north derivative. Test this explicitly. Initial derivatives can use central differences with an explicit edge/mask policy; hold the estimator fixed when comparing smoothers.

Direction is undefined at zero magnitude and unreliable near flat terrain. Hide arrows below an experimental magnitude threshold and show undefined/low-confidence direction rather than a fabricated bearing. This threshold is a display rule, not a validated uncertainty model.

Reference: [GDAL aspect conventions](https://gdal.org/en/stable/programs/gdal_raster_aspect.html).

## Recommended processing order

```text
Original elevation + valid-data mask
  -> selected elevation smoother (or none)
  -> gradient components
  -> magnitude and direction
  -> numerical cache
  -> map magnitude tiles + sparse direction arrows
```

Compute raw derivatives as the comparison baseline. Smooth elevation before differentiation for the main experiment: magnitude and direction then come from one coherent surface. Independently filtering magnitude and bearing can produce inconsistent results; angles also wrap at 360 degrees. Direct gradient-field regularization is a possible later experiment but must consider whether the field remains the gradient of a surface.

## Smoother comparison

### A — No smoothing

Establish the noise level and feature detail in the supplied model. Raw data is a baseline, not terrain ground truth.

### B — Light Gaussian smoothing

Use a reproducible linear baseline with standard deviation expressed in metres and converted to pixels. Proposed trial scales: 2, 4, and 8 m; these are experiment values, not production defaults. Expect progressively reduced fine detail. Record the finite kernel truncation radius.

### C — Regularized Perona–Malik-type nonlinear diffusion

A PDE candidate that reduces diffusion where a spatially smoothed gradient indicates a strong transition:

```text
u(x,y,0) = z(x,y)
du/dt = div(c(|grad(G_sigma * u)| / kappa) grad(u))
c(q) = 1 / (1 + q²)
```

Here `u` is the evolving elevation, `G_sigma` is a Gaussian used to stabilize the edge indicator, and `kappa` is a gradient scale in m/m. Diffusion time controls smoothing strength; with metre-based spatial derivatives it has units of square metres. This is a regularized variant, not the unmodified original Perona–Malik equation.

Test a small parameter sweep selected after inspecting the noise and slope distribution. Record sigma, kappa, time step, iteration count/total diffusion time, discretization, boundary conditions, and source grid. More iterations alone do not describe a reproducible physical smoothing scale.

Use a stable conservative discretization. For a four-neighbour explicit flux scheme with conductivities bounded by one, the usual sufficient diffusion time-step bound is `dt <= 1 / (2/dx² + 2/dy²)`; re-derive/check the bound if the stencil or scheme changes. Do not use nodata as elevation zero or allow diffusion across invalid boundaries.

The original nonlinear model has mathematical/numerical pitfalls, and regularization does not establish terrain accuracy. A steep smooth hillside, a ridge, and a height discontinuity are different structures. Gradient-dependent diffusion may preserve unwanted artifacts, alter slopes, or create unnatural transitions. Compare measured feature retention rather than choosing the sharpest-looking image.

References: [Perona and Malik, original paper](https://people.eecs.berkeley.edu/~malik/papers/MP-aniso.pdf), [Catté et al., regularized nonlinear diffusion](https://doi.org/10.1137/0729012).

### D — Spectral thin-plate smoothing

Penalize squared curvature while fitting the observed elevation:

```text
minimize integral((u-z)²) + lambda * integral(u_xx² + 2 u_xy² + u_yy²)
(I + lambda * Delta²) u = z
```

On the complete regular raster, a discrete cosine transform diagonalizes the reflecting-boundary discrete Laplacian. Each spectral coefficient is multiplied by `1 / (1 + lambda * mu²)`, where `mu` is the positive eigenvalue of the negative Laplacian. Removing and restoring the best-fitting plane preserves the affine null space exactly.

Express the setting as a half-amplitude wavelength `w` in metres, with `lambda = (w / (2*pi))^4`. A continuous sinusoid of wavelength `w` is therefore reduced to half amplitude; the discrete-grid value differs slightly near the cell scale. Initial trials use 20 m and 40 m, close to the half-amplitude wavelengths of Gaussian σ=4 m and σ=8 m respectively.

The DCT avoids periodic wrap between opposite edges, but its reflecting boundary condition can still affect results near the crop edge. Process with context and crop the result. The current fast spectral implementation requires a complete rectangular grid; masked grids would require filling or an iterative solver. Check for ringing, overshoot, and new extrema around sharp transitions.

### E — Optional higher-order regularization

If diffusion produces terraces or distorts smooth slopes, investigate second-order total generalized variation (TGV). Ordinary total variation tends toward piecewise-constant surfaces and can introduce staircasing. TGV supports piecewise-smooth structure and is a more relevant additional candidate for terrain ramps. This is a variational method, not simply another Gaussian-like blur; it adds solver complexity and is deferred until A–C are compared.

Terrain suitability is a hypothesis to test, not a result established by image-denoising papers. Reference: [Bredies, Kunisch, and Pock: TGV](https://static.uni-graz.at/fileadmin/nawi-institute/Mathematik/files/Optcon/Bredies/tgv.html).

## Numerical storage and tiling

- Preserve source elevation unchanged.
- Cache filtered elevation and `gx`, `gy` in float32 COGs with masks and provenance. Magnitude can be cached for display; derive bearing from components when useful.
- Key results by source version, area/grid, smoothing parameters, derivative operator, and algorithm version.
- Process with boundary context. Iterative diffusion needs sufficient overlap or exchanged boundaries at each iteration; a fixed one-cell halo is not sufficient for a long independent solve.
- Validate tiled processing against a single-domain crop. Select overlap using measured interior agreement or use a global solver for the initial small crop.
- Distinguish analysis resolution from display zoom. Averaging opposing vectors can cancel at ridges; do not interpret that cancellation as flat terrain. Do not arithmetically average bearings.
- For the first UI use cached method presets. Changing parameters may launch a new computation; promise interactive recolouring and tile navigation, not real-time full-area PDE solves.

## UI proposal

1. Magnitude: a continuous, perceptually ordered colour overlay, default units percent grade, with a visible legend and opacity control.
2. Direction: sparse equal-length uphill arrows with a clear label; optional downhill toggle. Magnitude is primarily encoded by colour so arrow density and length do not obscure the map.
3. Sampling: control arrow density in screen space with stable locations to reduce clutter and visual jumping during zoom.
4. Inspection: click a location for original/filtered elevation, magnitude, direction convention, method, and physical smoothing parameters.
5. Comparison: raw/Gaussian/nonlinear presets, a synchronized split view or toggle, and optionally an elevation-difference layer.
6. Flat/invalid areas: hide unreliable arrows; render nodata distinctly from flat ground.

If a continuous direction-colour mode is added later, use a cyclic legend. Keep hillshade optional: illumination shading is not a substitute for measured magnitude or bearing.

## Evaluation and acceptance evidence

Use known 2D synthetic surfaces: flat plane, tilted planes in several directions, rounded hill, ridge/valley, saddle, and a continuous surface with a sharp slope change. Add reproducible noise at multiple amplitudes; test nodata holes and tile boundaries.

Measure:

- Gradient-component error against the analytic surface.
- False slope on flat regions and magnitude error on tilted planes.
- Angular error only where the reference slope is large enough for meaningful direction.
- Peak slope attenuation, ridge displacement, and profile distortion across hills.
- Elevation residuals and any artificial terraces or new extrema.
- Rotation sensitivity and agreement between tiled and whole-crop processing.
- Runtime and memory for each method on cold and cached data.

Inspect matching cross-sections and maps at familiar Leppävaara locations. Prefer a method only after documenting its noise reduction versus feature loss at comparable denoising strength. A visually pleasing output alone is insufficient validation.

## Remaining choices

Exact crop/bounds, familiar validation locations, the next thin-plate and diffusion parameter sweeps, flat-direction threshold, and the final smoother remain open. The current synthetic result favors thin-plate smoothing at a 40 m half-amplitude wavelength, but it is not yet an accepted production algorithm.
