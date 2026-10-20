# Terrain mathematics

This reference describes the calculations used by terrain rendering and prepared
path measurements. See [architecture](../architecture.md) for storage and
sampling settings, and the [guide](../guide.md) for their interpretation in the UI.

## Gradient and path conventions

In EPSG:3067, let `x` increase grid-east and `y` grid-north, with elevation `z`
in metres. `terrain/derivatives.py` computes:

```text
gx = dz/dx
gy = dz/dy
magnitude = hypot(gx, gy)
slope_percent = 100 * magnitude
uphill_grid_bearing = atan2(gx, gy), clockwise from grid north
```

NumPy central differences apply in the interior and one-sided differences at
edges. North-up raster rows increase southward, so the row derivative is negated
for `gy`. A validity mask hides invalid outputs; it does not fill missing cells
or make neighboring finite differences independent of nodata. Bearing is NaN
where magnitude does not exceed the supplied minimum, including exactly flat
terrain. Grid bearing is not automatically a true-north compass bearing.

Path preparation samples derived magnitude/bearing at interval midpoints,
reconstructs gradient components, and projects onto the local unit tangent `t`:

```text
grade = magnitude * (sin(bearing) * t_east + cos(bearing) * t_north)
height_change = grade * projected_interval_length
grade_percent = 100 * grade
```

Each interval has constant sampled grade. Summing positive/negative height changes
gives estimated ascent/descent; cumulative sums give relative profile height.
Reverse traversal reverses order and grade sign and swaps ascent/descent.
Missing intervals break the profile; a later valid run restarts at zero. A
whole-segment mean must not erase an intervening climb and descent.

Sampling reads the quantized derived terrain product: magnitude has 0.0001
rise/run resolution and bearing 0.01 degree resolution. Both bands must contain
valid samples. An exactly flat cell has undefined bearing, encoded as nodata;
the current sampler therefore treats it as missing even when magnitude is zero.
This is a known limitation tracked in the [roadmap](../roadmap.md).
The profile estimates smoothed ground
height change; it is not sampled absolute elevation, does not establish bridge
or tunnel height, and is not constrained to integrate to zero around a loop.

## Thin-plate smoothing and its DCT implementation

This note derives the smoother in `src/runmap/terrain/smoothing.py` from the
continuous thin-plate bending energy, then states exactly which discrete
problem the code solves. The distinction matters at the raster boundary: the
implementation has the thin-plate frequency response in the interior, but
uses a squared Neumann finite-difference Laplacian rather than the natural
boundary conditions of the continuous variational problem.

## 1. Thin plates and the biharmonic equation

Let `z(x, y)` be measured elevation and `u(x, y)` the surface to estimate.
The bending energy of an isotropic thin plate is the squared Frobenius norm of
its Hessian:

```math
E_b[u] = \int_\Omega \left(u_{xx}^2 + 2u_{xy}^2 + u_{yy}^2\right)\,dA
       = \int_\Omega \|D^2u\|_F^2\,dA.
```

A smoothing thin plate trades this curvature against squared data error:

```math
J[u] = \int_\Omega (u-z)^2\,dA + \lambda E_b[u].
```

For a compactly supported perturbation `v`, the first variation is

```math
\frac{1}{2}\,\delta J[u;v]
 = \int_\Omega (u-z)v\,dA
   + \lambda\int_\Omega
       (u_{xx}v_{xx}+2u_{xy}v_{xy}+u_{yy}v_{yy})\,dA.
```

Integrating the second term by parts twice gives the interior Euler–Lagrange
equation

```math
(I+\lambda\Delta^2)u=z,
```

where

```math
\Delta^2u=(\partial_{xx}+\partial_{yy})^2u
          =u_{xxxx}+2u_{xxyy}+u_{yyyy}
```

is the biharmonic operator. Boundary terms from the integrations by parts
determine the thin plate's natural, or free-edge, boundary conditions. They
have not been written as `\partial_n u=0`; those are different boundary
conditions and lead to a different finite-domain problem.

The energy is zero for every affine surface

```math
p(x,y)=a+bx+cy,
```

because `D^2p=0`. This three-dimensional affine null space is a defining
feature of thin-plate regularization.

### Connection to the classical thin-plate spline

In two dimensions, a fundamental solution of the biharmonic operator is,
up to conventions and an irrelevant scale factor,

```math
G(r) \propto r^2\log r.
```

Consequently, the classical thin-plate spline fitted to scattered samples
has the form

```math
u(x,y)=a+bx+cy+\sum_i c_i\,\phi(\|(x,y)-(x_i,y_i)\|),
\qquad \phi(r)=r^2\log r,
```

with side constraints that separate the radial part from the affine null
space. The Runmap implementation does not assemble this dense radial-basis
system. Because its observations fill a regular rectangle, it solves a
discrete biharmonic regularization problem in a cosine eigenbasis instead.

## 2. Continuous spectral response

On the infinite plane, or on a periodic domain for this calculation, a
Fourier mode `exp(i(k_x x+k_y y))` is an eigenfunction of `-\Delta` with
eigenvalue

```math
|k|^2=k_x^2+k_y^2.
```

It is therefore an eigenfunction of `\Delta^2` with eigenvalue `|k|^4`.
Taking the Fourier transform of the Euler–Lagrange equation gives

```math
\widehat u(k_x,k_y)
=H(|k|)\widehat z(k_x,k_y),
\qquad
H(|k|)=\frac{1}{1+\lambda|k|^4}.
```

Thus the filter is isotropic, leaves the constant mode unchanged, and rolls
off as the inverse fourth power of spatial frequency. It does not have the
Gaussian response `exp(-\sigma^2|k|^2/2)`.

For a sinusoid of wavelength `w`, `|k|=2\pi/w`. Choosing

```math
\lambda=\left(\frac{w}{2\pi}\right)^4
```

makes

```math
H(2\pi/w)=\frac{1}{1+1}=\frac{1}{2}.
```

This is why the public parameter is named `half_amplitude_wavelength_m`.
`lambda` has units of metres to the fourth power, so `lambda * Delta^2` is
dimensionless.

## 3. The discrete rectangular-grid problem

Suppose a one-dimensional cell-centred grid has `N` samples separated by
`h`. Define the positive, reflecting-boundary negative Laplacian `L_N` by

```math
(L_Nv)_0       = (v_0-v_1)/h^2,
```

```math
(L_Nv)_j       = (2v_j-v_{j-1}-v_{j+1})/h^2,
\qquad 0<j<N-1,
```

```math
(L_Nv)_{N-1}   = (v_{N-1}-v_{N-2})/h^2.
```

The endpoint rows express zero flux through grid faces half a cell beyond the
first and last sample. Equivalently, the samples are extended evenly across
those faces. This avoids the wraparound that a discrete Fourier transform
would introduce.

The orthonormal DCT-II basis vectors are

```math
q_k(j)=\alpha_k\cos\!\left(\frac{\pi k(j+1/2)}{N}\right),
\qquad k=0,\ldots,N-1,
```

where `alpha_k` supplies the orthonormal normalization. They diagonalize
`L_N`, with eigenvalues

```math
\mu_k=\frac{4}{h^2}\sin^2\!\left(\frac{\pi k}{2N}\right).
```

For an `N_y` by `N_x` grid with the same resolution `h` in both directions,
the two-dimensional operator is the Kronecker sum

```math
L=L_{N_y}\otimes I+I\otimes L_{N_x}.
```

The tensor-product cosine mode `(k_y,k_x)` has eigenvalue

```math
\mu_{k_y,k_x}=\mu^{(y)}_{k_y}+\mu^{(x)}_{k_x}.
```

The discrete residual surface `v` is defined as the minimizer of

```math
\|v-r\|_F^2+\lambda\|Lv\|_F^2,
```

where `r` is the detrended input described below. Since `L` is symmetric, its
normal equation is

```math
(I+\lambda L^2)v=r.
```

If `C` denotes the orthonormal two-dimensional DCT-II and
`R=C(r)`, this equation separates into scalar equations:

```math
V_{k_y,k_x}
=\frac{R_{k_y,k_x}}
       {1+\lambda\mu_{k_y,k_x}^2},
\qquad
v=C^{-1}(V).
```

This is an exact solve of the stated discrete least-squares problem, apart
from floating-point roundoff. It is not an iteration or an approximation to
that discrete system.

For wavelengths much larger than a cell,
`4 sin^2(theta/2)/h^2` approaches the continuous squared wavenumber, so the
discrete and continuous responses agree. Near the grid scale the sine term
produces the expected finite-difference dispersion. A mode's exact discrete
half-amplitude setting also depends on its DCT frequencies through `mu`, not
only on the continuum wavelength formula.

## 4. Why the code removes a plane

The continuous Hessian energy leaves every affine surface unpenalized. In
contrast, the null space of the reflecting discrete Laplacian `L` contains
constants only: a sampled ramp has nonzero `L` at the boundary rows. Applying
the DCT filter directly would therefore bend a planar hillside near the
rectangle edges.

Runmap explicitly fits the least-squares plane

```math
p_{ij}=\bar z+b_x x_j+b_y y_i,
```

using coordinates centred on the grid. Centring makes the constant, x, and y
columns mutually orthogonal, so the coefficients reduce to

```math
b_x=\frac{\sum_{ij}z_{ij}x_j}{N_y\sum_jx_j^2},
\qquad
b_y=\frac{\sum_{ij}z_{ij}y_i}{N_x\sum_iy_i^2}.
```

It then sets `r=z-p`, filters `r`, and returns

```math
u=p+(I+\lambda L^2)^{-1}r.
```

This guarantees exact preservation of any input plane, including at the
boundary. More precisely, the code solves the discrete objective in Section
3 for a residual after fixing its fitted plane; it is not claiming that the
squared Neumann Laplacian itself has the full affine null space.

The `w=0` path returns an unmodified copy of the source instead of making a
DCT round trip.

## 5. Correspondence with `PreparedThinPlateSmoother`

The implementation maps directly onto the derivation:

| Code | Mathematics |
| --- | --- |
| centred `x`, `y`, `mean`, `slope_x`, `slope_y` | least-squares plane `p` |
| `dctn(z - self._trend, norm="ortho")` | orthonormal `R=C(z-p)` |
| `4*sin(pi*k/(2*N))**2 / resolution**2` | one-dimensional `mu_k` |
| `(eigen_y[:, None] + eigen_x[None, :])**2` | biharmonic eigenvalue `mu_(ky,kx)^2` |
| `(wavelength / (2*pi))**4` | penalty `lambda` |
| `1 / (1 + penalty * self._biharmonic)` | spectral response |
| `idctn(..., norm="ortho") + self._trend` | `p+C^-1(V)` |

Preparation computes the plane, source DCT, and eigenvalue grid once. Slider
updates only construct the response, multiply coefficients, and perform the
inverse DCT. For `N=N_xN_y` cells, storage is `O(N)` and each transform costs
approximately `O(N log N)`; no dense thin-plate kernel matrix is formed.

## 6. Assumptions and practical consequences

- The raster must be a complete rectangle. Missing cells destroy the tensor
  product diagonalization; the current code rejects them rather than treating
  missing elevation as zero.
- Grid spacing is currently one scalar `resolution`, so cells are assumed
  square. With rectangular cells, the x and y eigenvalues would use their
  respective spacings.
- DCT reflection prevents interaction between opposite edges, unlike a DFT,
  but it does not remove edge effects. Use surrounding context and crop to a
  trusted interior when possible.
- The response is positive but its spatial convolution kernel need not be
  everywhere positive. Ringing, overshoot, and new extrema remain validation
  concerns near sharp terrain transitions.
- Plane removal protects the global fitted grade. It does not make arbitrary
  curved trends immune to smoothing.
- The half-amplitude wavelength is exact for a continuous sinusoid. On the
  grid, the exact response is the discrete formula above.

## 7. Checks encoded in the tests

`tests/test_terrain.py` checks the main mathematical contracts:

1. a plane is preserved to floating-point precision, including boundaries;
2. a short DCT-aligned wave is attenuated by approximately one half at its
   requested physical wavelength while a longer wave is retained;
3. incomplete grids are rejected; and
4. the reusable prepared solver matches the one-shot function.

These tests protect the implementation-level formulation. They do not prove
that the reflecting boundary model or any particular wavelength is optimal
for terrain; those remain empirical modelling choices.
