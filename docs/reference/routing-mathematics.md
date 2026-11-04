# Run planner: cost calculation

The current cost-model identifier is `distance-preferences-v1`. The hill model
is `minetti-excess-uphill-preference-v4`, with weight table
`minetti-excess-equivalent-metres-v4`. These identify separate contracts: a
stable composition can contain a revised feature model. Native search evaluates
costs in `rust/src/routing_cost.rs`, using prepared hill features from
`routing/hills.py` and shared settings from `routing/settings.py`. Python
reference costs and request preparation live in `routing_reference/costs.py`
and `routing_reference/cost_preparation.py`. That planner uses the scalar Rust
adapter in `routing/fields.py`; the comparison Python integrator is in
`routing_reference/fields.py`.
See the [API reference](api.md#work-and-result-lifetimes) for request/cache
lifetimes and the [guide](../guide.md) for controls and result interpretation. This model is
separate from the CLI anchor-baseline experiment.

The planner supports a directed one-way route from start to destination and a
round trip from start through a turnaround and back. Round trips minimize route
distance plus penalties for loop shape, repeated paths and hills:

$$
\boxed{C(P)=L(P)+C_{\mathrm{centre}}(P)+C_{\mathrm{side}}(P)
+C_{\mathrm{reuse}}(P)+C_{\mathrm{hills}}(P).}
$$

All terms are measured in metres or equivalent penalty metres. A longer path
wins when its penalty savings exceed its additional distance. There is no fixed
target distance. Access rules, surface selection and stair avoidance determine
which paths are eligible before scoring.

One-way mode minimizes $C_{\mathrm{one\ way}}(P)=L(P)+C_{\mathrm{hills}}(P)$
with one directed search. Effective loop width is zero, so centre, side, and reuse
strengths are zero regardless of saved round-trip settings. Hill preferences,
access rules, and surface/stair policies still apply. A return connection is not
required. Zero-width round-trip mode still requires both outward and return paths.

## 1. Geometry and settings

Coordinates are projected into EPSG:3067, in metres. Define:

| Symbol | Meaning | Default |
| --- | --- | ---: |
| $H,T$ | Snapped start and turnaround positions (destination for one way) | User selected |
| $D=\lVert T-H\rVert$ | Straight-line separation | Derived |
| $M=(H+T)/2$ | Midpoint | Derived |
| $a$ | Loop avoidance ratio | 0.18 |
| $\rho$ | Centre radius ratio | 0.15 |
| $\tau$ | Side transition ratio | 0.05 |
| $s_0$ | Side strength | 2 |
| $u_0$ | Reuse strength | 4 |
| $w$ | Hill weight | Off: 0; Low: 1; High: 6 |

The spatial scales are $R=\rho D$ and $B=\tau D$.
$L(P)$ sums stored graph lengths. Integrals use horizontal distance $ds$ along
the projected path geometry; these two length measures can differ slightly.

## 2. Loop-shape penalties

### Strengths

Shape penalties increase smoothly with the loop avoidance ratio:

$$
t=\min(1,a/0.18),\qquad \eta=t^2(3-2t),
$$

$$
k_c=\eta\exp\left(\frac{a^2}{2\rho^2}\right),
\qquad k_s=\eta s_0,\qquad k_u=\eta u_0.
$$

At the defaults, $k_c\approx2.0544$, $k_s=2$ and $k_u=4$.
Disabling centre avoidance sets $k_c=0$. Setting loop width to zero ($a=0$)
sets all three strengths to zero; hill avoidance remains active.

### Centre avoidance

$$
C_{\mathrm{centre}}(P)
=k_c\int_P\exp\left(-\frac{\lVert x-M\rVert^2}{2R^2}\right)ds.
$$

This discourages paths near the midpoint. The penalty decreases smoothly with
distance from it. Crossing the centre remains legal.

### Side preference

Let $v=T-H$. The signed distance from the home-to-turnaround line is

$$
y(x)=\frac{v_x(x_y-H_y)-v_y(x_x-H_x)}{D}.
$$

Positive $y$ means left of $H\to T$. This reference direction stays fixed on
both legs. For preferred side $\sigma=+1$ (left) or $\sigma=-1$ (right),

$$
C_{\mathrm{side}}(P;\sigma)
=k_s\int_P\frac{1-\sigma\tanh(y(x)/B)}{2}\,ds.
$$

The penalty density approaches zero on the preferred side and $k_s$ on the
opposite side. It is $k_s/2$ on the dividing line.

### Reuse

Let $U$ be the set of segments used by the outbound path. For the return path,

$$
C_{\mathrm{reuse}}(P;U)
=k_u\sum_{e\in P}L_g(e)\,\mathbf{1}\{\operatorname{segment}(e)\in U\},
$$

where $L_g(e)$ is the stored graph length of traversal $e$ and the indicator
is one for a reused segment, zero otherwise. Reuse is penalized in either
traversal direction. Segments are split at the endpoint pins before routing.
The outbound search has no reuse penalty.

## 3. Hill penalty

### Running-energy curve

Let $i=\Delta z/\Delta s$ be signed rise divided by horizontal run: a 5% climb
has $i=0.05$.

The base curve is the running fit from Figure 1 of Minetti et al. (2002),
*Energy cost of walking and running at extreme uphill and downhill slopes*,
[doi:10.1152/japplphysiol.01177.2001](https://doi.org/10.1152/japplphysiol.01177.2001)
([paper PDF](https://runscribe.com/wp-content/uploads/power/Minetti2002.pdf)):

$$
E(i)=155.4i^5-30.4i^4-43.3i^3+46.3i^2+19.5i+3.6.
$$

Energy is in J/kg per metre travelled. The fitted range is $[-0.45,0.45]$.
Outside it, use tangent continuation:

$$
b=\min(0.45,\max(-0.45,i)),\qquad
\widetilde E(i)=E(b)+E'(b)(i-b).
$$

This equals the polynomial inside the range and continues without an upper cap.

### Preference for gentle climbs

Normalize by flat energy and adjust uphill excess:

$$
q(i)=\frac{\widetilde E(i)}{3.6},\qquad
q_{\mathrm{pref}}(i)=
\begin{cases}
1+(q(i)-1)(i/0.05)^2,&i>0,\\
q(i),&i\le0.
\end{cases}
$$

The multiplier softens climbs below 5% and increasingly penalizes steeper climbs.
At 10%, uphill energy excess is multiplied by four; at 20%, by sixteen.

Convert horizontal run to sloping distance, then keep only excess above flat:

$$
r(i)=q_{\mathrm{pref}}(i)\sqrt{1+i^2},\qquad
\boxed{p(i)=\max(0,r(i)-1).}
$$

The hill cost is

$$
\boxed{C_{\mathrm{hills}}(P)=w\int_P p(i(s))\,ds.}
$$

Flat terrain and moderate descents add zero hill penalty. Uphill and sufficiently
steep downhill add positive penalties. Downhill cannot cancel uphill penalties.
Increasing $w$ changes the slope penalty while leaving the distance cost unchanged.

The uphill adjustment, geometric conversion, subtraction of flat effort,
clipping, preset weights and tangent continuation are application choices.
The underlying polynomial comes from the paper.

### Uncertain terrain

Missing measurements, bridges and tunnels use a direction-independent fallback:

$$
p_{?}=\max\left(0,
\frac{\widetilde E(0.20)}{3.6}\sqrt{1+0.20^2}-1\right)
\approx1.5514.
$$

This is unadjusted +20% energy excess, without the uphill multiplier. Per 100 m,
it adds approximately 155.14 penalty metres at Low or 930.84 at High. It is an
uncertainty allowance, not an estimated actual slope.

### Integration

Each terrain interval has a constant measured grade. Its contribution is
$w\ell p(i)$, where $\ell$ is its projected length. Partial traversals receive
the fraction overlapped in stored graph chainage. Uncertain intervals use
$p_{?}$ in place of $p(i)$.

Reverse traversal negates the grade and recalculates the penalty. Climbs and
descents within a segment are evaluated separately.

## 4. Worked example

Two climbs both gain 5 m. Assume graph and projected lengths agree and all shape
and reuse costs are equal:

| Alternative | Length | Grade | Distance + hills, Low | Distance + hills, High |
| --- | ---: | ---: | ---: | ---: |
| Shorter climb | 100 m | 5% | 130.31 | 281.84 |
| Milder climb | 140 m | 3.57143% | 155.05 | 230.29 |

For the 5% climb, $r\approx1.30307$, so High gives

$$
100+6\times100\times(1.30307-1)\approx281.84.
$$

Low chooses the shorter climb. High accepts 40 extra metres for the milder slope.

## 5. Selecting the route

One-way mode returns one candidate containing the lowest-cost outward path.
Round-trip mode returns two-leg candidates. For each side assignment, the planner:

1. Finds the lowest-cost outbound path with one preferred side.
2. Finds the lowest-cost return path with the opposite preferred side and a
   reuse penalty for the selected outbound segments.
3. Reports the two paths as a candidate loop; its cost is their summed cost.

Nonzero loop width produces two side assignments using four independent directed
searches. Side preferences are soft, so the candidates may coincide.

The legs are optimized sequentially. The outbound search does not anticipate
return cost, so this procedure does not guarantee the globally cheapest loop.
At zero width there is one candidate. Its legs are searched independently when
hills are active; with hills Off, legal retracing can supply the return path.

Each completed leg is optimized using A*. All penalties are nonnegative, so
cost is at least graph distance. Spatial integrals use midpoint sampling. The
spacing limit is $h=\min(10\,\mathrm{m},R/4,B/4)$. Each straight piece of projected
geometry of length $\ell$ uses $n=\max(2,\lceil\ell/h\rceil)$ equally spaced
midpoint samples, so the actual step is $\ell/n$. The Python reference uses the
same rule. The [dated sampling comparison](../archive/history/2026-11-02-integration-comparison.md)
records the speed and numerical-error measurements behind this choice.
Hill integrals sum the measured intervals. Traffic lights, busy roads and
greenery currently add zero.

## 6. Observations and validation

Hill preparation validates graph/measurement identity and contiguous chainage,
then prepares forward and reverse interval responses once per dataset load.
Request caching keys directional features by directed arc. Missing profiles use
projected geometry length and the same uncertainty fallback as invalid intervals.
Numerically valid bridge/tunnel samples can contribute estimated ascent/descent,
but remain untrusted for hill energy. Report valid and trusted coverage and
fallback distance separately. Missing samples invent no height change; entirely
unmeasured traversals have null ascent/descent. With hills Off, integration is
skipped and terrain observations remain null.

Selected-leg cost breakdowns use the same component functions and fixed reuse
context as the search. Their artificial scores are distinct from raw observations
and physical distance. Unsupported non-Off signal/environment requests are
rejected rather than silently receiving zero cost.

`tests/test_hills.py`, `test_costs.py`, `test_run_planner.py`, and `test_run_api.py`
cover directional/partial integration, fallback, finite lower-bounded costs,
request caches, capabilities, and A* agreement with separate Dijkstra searches.
They do not establish surveyed path grades or local runner acceptance. Dated
[hill calibration reports](../archive/history/32-hill-routing.md) preserve earlier
models and selected examples; their identities must match before comparisons.

## Turnaround region estimate

The advisory map layer uses distance-only directed searches with the selected
surface/stair policy. For start $H$ and a sampled turnaround $T$, define
$b(T)=d(H,T)+d(T,H)$ and projected separation $r=\|T-H\|$. The return search
runs on reversed arcs; partial-segment distances reverse direction permissions
as well. Distinct positions without both legal legs are excluded.

For width $w\in[0,0.6]$, let $q=w/0.6$. The deployed mean is the simple
empirical cubic, anchored to the unpenalized round trip:

$$
\widehat L=b+r(2.293494214q-1.665533791q^2+3.411922003q^3).
$$

At zero width, $\widehat L=b$. The correction is nonnegative and increasing over
the supported width range. Full-precision coefficients ship in
`routing/turnaround_calibration.py`; the
[fit comparison](../archive/history/60-extended-width-polynomials.md) records
whole-start and whole-radius validation, including constrained Lauttasaari cases.

For uncertainty, use whole-start held-out predictions of this same cubic.
At each sampled width, compute $(L_{\rm actual}-\widehat L_{\rm held})/r$
for both individual route orientations. Each start/radius cell has equal total
weight, split equally between orientations. Weighted midpoint-CDF quantiles at
0.1 and 0.9 give residual bounds $e_{10}(w),e_{90}(w)$, enlarged to include zero.
Interpolate bounds linearly between the ten width knots. The predicted-length
interval is

$$
[\max(b,\widehat L+r e_{10}),\;\max(b,\widehat L+r e_{90})].
$$

The dark core is $0.95d\le\widehat L\le1.05d$. The light fringe contains
additional locations where this interval overlaps $[0.95d,1.05d]$. Both are
masked wherever $b>1.05d$. At zero width both residual bounds are zero, so no
fringe is emitted. The
[calibration record](../archive/history/61-cubic-turnaround-calibration.json)
preserves coefficients, width knots, weights, per-width counts/empirical coverage,
and source hashes. Its nominal 80% spread is not an independently validated
coverage guarantee: it is conditional on successful all-width cases at five
starts, with hills Off, all surfaces, stairs avoided and 1.5–10 km separations.
Other preferences and distances are unvalidated extrapolations.

The implementation samples network geometry at at most 25 m spacing and builds
a nearest-sample baseline grid. At grid cell centres, projected separation supplies
the cubic correction and residual allowance. The contour score maps the core to
0–1 and the asymmetric fringe to 1–2; it is not itself a distance or probability.
A snapshot must cover at least 115% of the target in each direction; the frontend
rounds that budget up to whole kilometres. This conservative search extent also
covers candidates in directed networks with unequal outward and return legs.
