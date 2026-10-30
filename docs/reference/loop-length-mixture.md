# Mixtures of polynomial loop-length regressions

The first MAP implementation is available through the
[development command](../development.md#loop-length-map-mixtures).
The adaptive sampling and posterior uncertainty extensions below remain proposed.
The model extends the [polynomial baseline experiment](../archive/history/55-loop-length-baselines.md)
with several local polynomial experts and an input-dependent regime selector.
Existing fitting commands and validation contracts are documented in
[development](../development.md#loop-length-baseline-fits).

## Inputs and polynomial experts

Let the response $L$ be physical loop length in metres. Define

$$
q=w/0.18,\qquad R=r/1000,\qquad D=(b-2r)/1000,
$$

where $w$ is width, $r$ is snapped projected separation in metres, and
$b=d(H,T)+d(T,H)$ is the independently computed directed shortest network
round-trip distance under the experiment's surface/stair policy.

Use the existing `network_affine` basis:

$$
\phi(x)=(1,q,q^2,R,Rq,Rq^2,D,Dq,Dq^2)^\top.
$$

Each of $K$ experts has its own nine coefficients:

$$
m_k(x)=\phi(x)^\top\beta_k
      =A_{2,k}(q)+RB_{2,k}(q)+DC_{2,k}(q).
$$

These are quadratic polynomials in width with affine radial and detour
interactions, rather than a full multivariate quadratic basis. Reuse the
explicit basis instead of applying `PolynomialFeatures` indiscriminately.
The regression is linear in its coefficients despite being polynomial in inputs.

Include side assignment as a known categorical input. A first experiment can
put it in the gate; add side terms to the experts if validation indicates that
sharing each expert's mean across sides is too restrictive.

## Conditional mixture and gate

Introduce an unobserved regime $z$:

$$
z\mid x\sim\operatorname{Categorical}(\pi_1(x),\ldots,\pi_K(x)),
\qquad
L\mid x,z=k\sim\mathcal N(m_k(x),\sigma_k^2).
$$

The conditional density is

$$
p(L\mid x)=\sum_{k=1}^K\pi_k(x)
\mathcal N(L;m_k(x),\sigma_k^2).
$$

The planner is deterministic for fixed inputs. Here the Gaussian spread models
approximation error and unresolved inputs, not randomness in route execution.
Start with one constant residual variance per expert.

Let $g(x)$ contain an intercept and scaled gate features, initially requested
forward/lateral turnaround coordinates, $q$, $D$, and a binary side indicator.
Explicitly, the proposed initial gate feature vector is

$$
g(x)=\begin{pmatrix}
1\\
\widetilde f\\
\widetilde\ell\\
\widetilde q\\
\widetilde D\\
s
\end{pmatrix},
\qquad
\widetilde a=\frac{a-\mu_{a,\mathrm{train}}}
{\tau_{a,\mathrm{train}}},
\quad a\in\{f,\ell,q,D\}.
$$

Here $f$ and $\ell$ are requested forward and lateral turnaround coordinates,
and $s\in\{0,1\}$ encodes side assignment. The means $\mu$ and standard
deviations $\tau$ are fitted on training data only and reused for prediction;
use a scale of one for any constant training feature. The intercept and binary
side indicator are not standardized. Thus $g$ is a chosen feature transformation,
while the six coefficients in each $\gamma_k$ are learned. It differs from
$\phi$, which supplies the features for predicting length within each expert.

Requested coordinates retain the sampling frame; the expert's $r,b$ retain
their snapped-network definitions. All gate inputs must be available before
evaluating a candidate route. Define

$$
\pi_k(x)=\frac{\exp(g(x)^\top\gamma_k)}
{\sum_{j=1}^K\exp(g(x)^\top\gamma_j)}.
$$

Set $\gamma_K=0$ to remove the redundant common shift in softmax logits.
A linear gate makes simple decision boundaries; richer gate features can be
tested later if those boundaries cannot follow network transitions.

### RBF gate

The optional `--gate rbf` adds Gaussian radial basis functions to the linear
gate. Let $u(x)=(\widetilde f,\widetilde\ell,\widetilde q)^\top$ and define

$$
\rho_j(x)=\exp\left(-\frac{\|u(x)-c_j\|^2}{2h^2}\right),
\qquad j=1,\ldots,M,
$$

$$
g_{\mathrm{RBF}}(x)=
(1,\widetilde f,\widetilde\ell,\widetilde q,\widetilde D,
\rho_1(x),\ldots,\rho_M(x),s)^\top.
$$

The default is $M=16$ centers and length scale $h=0.8$ in standardized
coordinate/width units. Centers cover distinct training triples: first select
the triple closest to their mean, then repeatedly select the triple farthest
from the selected set. If fewer than $M$ distinct triples exist, use all of them.
Standardization and center selection happen inside each training fold and do
not use observed lengths. Prediction reuses the fitted scaling and centers.
The radial features stay in [0,1] and are not separately standardized.

Softmax logits remain linear in this expanded feature vector, but their
coordinate boundaries can now curve or form disconnected regions. Including
width in each radial feature permits boundaries that vary with width. Side
retains an additive logit effect; side-specific radial interactions are not
included. The same Gaussian gate coefficient priors and MAP EM updates apply.
Gate feature count changes the induced function prior, so the same coefficient
prior scale does not make the linear and RBF function priors identical.

Category plots use $\arg\max_k\pi_k(x)$ from a single full-data fit, evaluated
without supplying measured lengths to prediction. Thus category indices are
consistent across width and side panels within that fit. They are arbitrary
and must not be matched by number across different fits, restarts, or folds.
Full-data category maps describe the learned partition; held-out metrics assess
prediction. Grey cells have no successful observation at that width and side.
Companion plots show maximum gate probability, which describes the fitted
gate's selection strength rather than posterior parameter confidence.

Constant weights describe multiple possible response regimes but do not give
a more flexible predictive mean:

$$
\sum_k\pi_km_k(x)=\phi(x)^\top\left(\sum_k\pi_k\beta_k\right).
$$

Input-dependent weights are therefore central to this proposal.

## Soft and hard predictions

The conditional mean and variance are

$$
\mu(x)=\sum_k\pi_k(x)m_k(x),
$$

$$
v(x)=\sum_k\pi_k(x)\left[\sigma_k^2+(m_k(x)-\mu(x))^2\right].
$$

This variance includes within-expert spread and disagreement between experts.
It does not include uncertainty about fitted parameters. Predictive quantiles
come from the mixture CDF, not generally from $\mu\pm1.96\sqrt v$:

$$
F(\ell\mid x)=\sum_k\pi_k(x)
\Phi\left(\frac{\ell-m_k(x)}{\sigma_k}\right).
$$

Use a scalar root finder to solve $F(\ell\mid x)=p$ for a desired quantile $p$.

A hard piecewise prediction is

$$
k^*(x)=\arg\max_k\pi_k(x),\qquad
\widehat L_{\mathrm{hard}}(x)=m_{k^*(x)}(x).
$$

The soft mean is smooth for a smooth gate and may lie between distinct route
regimes. Hard selection allows jumps at regime boundaries; it does not impose
continuity there and is not generally the mode of the mixture density.
Evaluate both predictions. Keep the full density when representing ambiguity
near transitions is useful. Gaussian components and unconstrained polynomials
do not enforce positive lengths, monotonicity, or the exact zero-width identity.
Restrict initial evaluation to the sampled domain and report such violations.

## Penalized likelihood and EM fitting

Write $\phi_i=\phi(x_i)$, $g_i=g(x_i)$, and $\pi_{ik}=\pi_k(x_i)$.
Minimize the negative conditional log-likelihood with coefficient penalties:

$$
J=-\sum_i\log\left[\sum_k\pi_{ik}
\mathcal N(L_i;\phi_i^\top\beta_k,\sigma_k^2)\right]
+\frac{\lambda_\beta}{2}\sum_k\|P\beta_k\|^2
+\frac{\lambda_\gamma}{2}\sum_{k<K}\|Q\gamma_k\|^2.
$$

$P$ and $Q$ omit intercepts from shrinkage. Scale nonconstant columns using
training data before applying penalties. The formulas below use those scaled
designs, with explicit intercept columns.

An EM iteration alternates the following steps.

### E-step: posterior responsibilities

$$
r_{ik}=\frac{\pi_{ik}\mathcal N(L_i;\phi_i^\top\beta_k,\sigma_k^2)}
{\sum_j\pi_{ij}\mathcal N(L_i;\phi_i^\top\beta_j,\sigma_j^2)}.
$$

Compute this in log space with `scipy.special.logsumexp`. Responsibilities use
observed training lengths; prediction at unseen points uses only the gate.

### Expert update: weighted ridge regression

For fixed variances and responsibilities, update each expert by minimizing

$$
\frac{1}{2\sigma_k^2}\sum_i r_{ik}(L_i-\phi_i^\top\beta_k)^2
+\frac{\lambda_\beta}{2}\|P\beta_k\|^2.
$$

Equivalently,

$$
\beta_k=(\Phi^\top W_k\Phi+
\lambda_\beta\sigma_k^2P^\top P)^{-1}\Phi^\top W_kL,
\qquad W_k=\operatorname{diag}(r_{1k},\ldots,r_{nk}).
$$

Use a linear solve, not an explicit inverse. With scikit-learn, remove the
explicit constant column and use
`Ridge(alpha=lambda_beta * sigma_k_squared, fit_intercept=True)` with
`sample_weight=r[:, k]`. Its objective is weighted squared error plus
`alpha` times the squared coefficient norm. This variance-dependent `alpha`
keeps the update consistent with the stated likelihood penalty.

### Variance update

After updating the means, set

$$
\sigma_k^2=\max\left(\sigma_{\min}^2,
\frac{\sum_i r_{ik}(L_i-m_k(x_i))^2}{\sum_i r_{ik}}\right).
$$

The positive variance floor prevents likelihood collapse around a few samples.
Alternating expert and variance updates increases the expected penalized
complete-data objective when each update is solved accurately.

### Gate update

Minimize soft-label cross-entropy plus its penalty:

$$
J_g=-\sum_{i,k}r_{ik}\log\pi_{ik}
+\frac{\lambda_\gamma}{2}\sum_{k<K}\|Q\gamma_k\|^2.
$$

For each nonreference component, its gradient is

$$
\nabla_{\gamma_k}J_g=
G^\top(\pi_{:k}-r_{:k})+\lambda_\gamma Q^\top Q\gamma_k.
$$

Use `scipy.optimize.minimize(method="L-BFGS-B", jac=True)` with this objective
and gradient for the linear gate. The larger RBF gate uses `method="trust-exact"`
with the exact Hessian; its block for free components $k,j$ is

$$
H_{kj}=G^\top\operatorname{diag}
\left(\pi_{:k}(\mathbb 1_{k=j}-\pi_{:j})\right)G
+\mathbb 1_{k=j}\Lambda_\gamma.
$$

If trust-region convergence stalls near a mode, continue with L-BFGS-B from its
last iterate. Both solve the same convex conditional gate objective, and the
outer EM procedure still checks the actual MAP objective. A stalled RBF solve
is accepted only if its gradient infinity norm is at most $10^{-4}$ and its
objective has not increased beyond numerical tolerance. Other optimizer failures
raise an error. Scikit-learn's
ordinary `LogisticRegression.fit` expects class
labels; it does not directly accept the responsibility matrix as soft targets.
Optimizing the small gate directly avoids expanding each sample into $K$
weighted class-labelled rows.

These expert, variance, and gate updates form a generalized EM procedure.
Monitor the actual penalized observed-data objective after complete iterations;
check optimizer success and reject numerical increases. Stop at a configured
relative objective tolerance or iteration limit, recording convergence status.

## Bayesian formulation and MAP EM

The quadratic penalties above are negative log Gaussian priors, up to constants.
With fixed prior scales, penalized maximum likelihood is therefore maximum a
posteriori (MAP) estimation. EM can maximize this posterior while integrating
out the latent assignments $z_i$. It still estimates a single parameter vector;
it does not integrate out uncertainty about the fitted parameters.

### Generative model and priors

Condition on the inputs and a fixed component count $K$. Let $v_k=\sigma_k^2$.
A proper-prior version of the model is

$$
\begin{aligned}
\beta_k &\sim \mathcal N(\mu_\beta,\Lambda_\beta^{-1}),
&&k=1,\ldots,K,\\
\gamma_k &\sim \mathcal N(0,\Lambda_\gamma^{-1}),
&&k=1,\ldots,K-1,\qquad \gamma_K=0,\\
v_k &\sim \operatorname{InvGamma}(a_0,b_0),
&&k=1,\ldots,K,\\
z_i\mid x_i,\Gamma &\sim \operatorname{Categorical}(\pi(x_i;\Gamma)),\\
L_i\mid x_i,z_i=k,\beta,v &\sim
\mathcal N(\phi_i^\top\beta_k,v_k).
\end{aligned}
$$

The priors are independent across components and parameter groups. Here
$\Lambda$ denotes precision, and the inverse-gamma convention is
$p(v)\propto v^{-(a_0+1)}\exp(-b_0/v)$, with $a_0,b_0>0$.
The coefficient priors are independent of variance; this choice preserves the
variance-dependent ridge penalty derived above.

For zero-centered expert priors, setting
$\Lambda_\beta=\lambda_\beta P^\top P$ and
$\Lambda_\gamma=\lambda_\gamma Q^\top Q$ recovers the existing coefficient
penalties exactly. Because those matrices leave intercepts unpenalized, they
describe improper flat priors on intercepts rather than proper Gaussians.
Use small positive intercept precisions to make the priors proper, and report
that this slightly changes the objective. With proper priors, interpret their
scales in the training-standardized feature basis and chosen response units.

A nonzero $\mu_\beta$ can shrink all experts toward a plausible global model
instead of zero. A baseline estimated from the same training data is a
data-dependent, empirical-Bayes-style choice, not an independently specified
prior; it must be fitted within each training fold. A hierarchical global mean
with its own prior is another possible extension.

The gate coefficient priors induce a distribution over input-dependent weights.
There is no separate Dirichlet weight vector in this model. A Dirichlet prior
would be natural for a constant-weight mixture, which has different behavior.

### Posterior objective

Writing $\theta=(\beta,\Gamma,v)$ and $\mathcal D=\{(x_i,L_i)\}$,

$$
p(\theta,z\mid\mathcal D)\propto p(\theta)
\prod_i \pi_{i,z_i}(\Gamma)
\mathcal N(L_i;\phi_i^\top\beta_{z_i},v_{z_i}).
$$

After summing over assignments, MAP minimizes

$$
\begin{aligned}
J_{\mathrm{MAP}}={}&-\sum_i\log\sum_k
\pi_{ik}(\Gamma)\mathcal N(L_i;\phi_i^\top\beta_k,v_k)\\
&+\frac12\sum_k(\beta_k-\mu_\beta)^\top
\Lambda_\beta(\beta_k-\mu_\beta)
+\frac12\sum_{k<K}\gamma_k^\top\Lambda_\gamma\gamma_k\\
&+\sum_k\left[(a_0+1)\log v_k+\frac{b_0}{v_k}\right].
\end{aligned}
$$

This expression omits constants only because prior hyperparameters are fixed.
Learning prior scales requires retaining their normalization terms and defining
an appropriate estimation or hyperprior scheme.

### MAP expectation and maximization steps

At iteration $t$, the E-step computes exactly the same responsibilities as
before, evaluated at $\theta^{(t)}$:

$$
r_{ik}=p(z_i=k\mid x_i,L_i,\theta^{(t)}).
$$

The priors do not appear explicitly in this conditional distribution because
the parameters are held fixed. They influence it through the fitted parameters.
The M-step increases

$$
Q_{\mathrm{MAP}}(\theta\mid\theta^{(t)})
=\mathbb E_{z\mid\mathcal D,\theta^{(t)}}
[\log p(L,z\mid X,\theta)]+\log p(\theta).
$$

For fixed responsibilities, alternate the following conditional updates:

$$
\beta_k\leftarrow
(\Phi^\top W_k\Phi+v_k\Lambda_\beta)^{-1}
(\Phi^\top W_kL+v_k\Lambda_\beta\mu_\beta),
$$

$$
v_k\leftarrow
\frac{b_0+\tfrac12\sum_i r_{ik}(L_i-\phi_i^\top\beta_k)^2}
{a_0+1+\tfrac12N_k},
\qquad N_k=\sum_i r_{ik},
$$

$$
\Gamma\leftarrow\arg\min_\Gamma
\left[-\sum_{i,k}r_{ik}\log\pi_{ik}(\Gamma)
+\frac12\sum_{k<K}\gamma_k^\top\Lambda_\gamma\gamma_k\right].
$$

The variance expression is a conditional posterior mode, not a posterior mean.
With $b_0>0$, this prior discourages variance collapse and can replace the hard
floor; a separate numerical floor, if retained, should be documented. The
original clipped variance update instead corresponds to constrained likelihood
estimation without this inverse-gamma term. Do not combine its objective with
the new variance update without adding the variance prior to the objective.

Conditional maximizations give an expectation/conditional-maximization form of
MAP EM. Accurate updates increase the observed-data log posterior; numerical
gate steps should be checked against that objective. Mixture optimization is
nonconvex, so priors do not remove the need for multiple initializations.

### Implementation changes and full Bayesian prediction

The same NumPy/SciPy approach applies. Replace the objective and variance update
with the MAP versions. Isotropic, zero-centered slope priors with flat intercepts
still permit scikit-learn `Ridge`. For general precision matrices or proper
intercept priors, use `scipy.linalg.solve` on the small expert systems instead.
The gate gradient replaces its scalar penalty term with
$\Lambda_\gamma\gamma_k$.

MAP prediction plugs in $\widehat\theta_{\mathrm{MAP}}$. Full Bayesian prediction
instead averages over the posterior:

$$
p(L_*\mid x_*,\mathcal D)=\int
\left[\sum_k\pi_k(x_*;\Gamma)
\mathcal N(L_*;\phi(x_*)^\top\beta_k,v_k)\right]
p(\theta\mid\mathcal D)\,d\theta.
$$

Posterior sampling or a variational approximation would be needed to estimate
this integral. The MAP EM procedure above does not provide those parameter
uncertainties. Component labels may also permute between equivalent solutions;
compare predictive distributions rather than averaging unaligned component
coefficients across fits or posterior draws. A Bayesian model can use the same
held-out spatial validation to check its predictive accuracy and calibration.

## Gaussian process interpretation and adaptive sampling

A Gaussian prior on polynomial coefficients induces a Gaussian process on each
expert's latent mean. If

$$
\beta_k\sim\mathcal N(\mu_\beta,\Sigma_\beta),
\qquad f_k(x)=\phi(x)^\top\beta_k,
$$

then

$$
f_k\sim\operatorname{GP}\left(
\phi(x)^\top\mu_\beta,
\;k(x,x')=\phi(x)^\top\Sigma_\beta\phi(x')\right).
$$

This is a finite-rank GP whose functions stay in the chosen polynomial span.
Coefficient prior covariance determines plausible variation and correlation
between inputs. The overall gated mixture is generally not itself a GP.
Interpret this kernel in the same training-standardized feature basis used by
the implementation.

For one Gaussian regression expert with known observation covariance $V$, the
coefficient posterior is available analytically:

$$
\Sigma_{\mathrm{post}}=
(\Sigma_\beta^{-1}+\Phi^\top V^{-1}\Phi)^{-1},
$$

$$
\mu_{\mathrm{post}}=\Sigma_{\mathrm{post}}
(\Sigma_\beta^{-1}\mu_\beta+\Phi^\top V^{-1}L).
$$

Its latent-function variance is

$$
\operatorname{Var}[f(x)\mid\mathcal D]
=\phi(x)^\top\Sigma_{\mathrm{post}}\phi(x).
$$

With latent assignments and an uncertain gate or variance, this expression is
not the full posterior covariance of the mixture. A local Gaussian (Laplace)
approximation around a MAP solution is one possible next step, but it can miss
alternative partitions and should be checked near regime transitions. The first
implementation returns plug-in mixture spread only, not parameter uncertainty.

For adaptive target-length sampling, start with a small set of route evaluations,
fit the model, and seek candidates near the requested length. Choose additional
evaluations using both target proximity and uncertainty that can be reduced by
new observations. Refit after evaluation and stop only when an evaluated route
meets the chosen tolerance or the budget is exhausted.

Distinguish coefficient and gate uncertainty from residual variance and
plug-in disagreement between experts. Mixture spread alone is not an information
gain criterion: it may repeatedly attract evaluations to unresolved transitions.
Polynomial uncertainty can also become small while a local network discontinuity
remains unexplained. A polynomial mean plus a spatial residual GP within an
expert is a possible extension if held-out validation demonstrates that need.
Validate target-length success and routing-call cost against a simple sampling
baseline under equal budgets before claiming fewer function evaluations.

## NumPy, SciPy, and scikit-learn implementation

The first implementation uses NumPy and SciPy only. Proper Gaussian priors on
intercepts use `scipy.linalg.solve` rather than scikit-learn `Ridge`; the weighted
ridge formulation above remains an alternative for flat intercept priors.
The data-only containers and fitting functions are in
`src/runmap/experiments/loop_length_mixture.py`; the offline experiment command
is in `src/runmap/experiments/fit_loop_length_mixture.py`.

It uses zero-mean independent coefficient priors with standard deviations
3,000 m for standardized slopes and 10,000 m for intercepts. All free gate
coefficients, including intercepts, have standard deviation 3. The variance
prior is inverse-gamma with shape 2 and scale 270,000 m² (prior mode 90,000 m²).
These are initial configurable choices, not calibrated scientific priors.
The default gate remains linear; RBF expansion is optional as described above.
Each expert starts from a smoothed random-projection partition of the gate
features. Seeded restarts, MAP objective histories, convergence flags, and
effective component counts are recorded. The best converged restart is selected
by MAP objective, falling back to the best unfinished fit if none converges;
inspect the flags and occupancy before using results. No automatic hyperparameter
tuning, parameter posterior approximation, adaptive sampler, or positivity
constraint is implemented.

Keep arrays and fitted parameters in data-only dataclasses, with module-level
functions for feature construction, initialization, fitting, and prediction.
Suggested responsibilities:

| Function | Responsibility |
| --- | --- |
| `build_expert_features` | Construct the explicit nine-column polynomial basis |
| `build_gate_features` | Construct coordinates, width, detour, and side features |
| `fit_scalers` | Fit training-only feature scaling; preserve intercepts |
| `initialize_parameters` | Initialize expert coefficients, gate, and variances |
| `component_log_probabilities` | Evaluate log gate weights plus Gaussian log densities |
| `responsibilities` | Normalize component log probabilities with `logsumexp` |
| `update_experts` | Run weighted `Ridge` fits |
| `update_variances` | Estimate component variances with a floor |
| `update_gate` | Optimize soft-label cross-entropy with SciPy |
| `penalized_objective` | Evaluate the convergence and restart-selection criterion |
| `fit_mixture` | Run EM for each restart and retain the best converged fit |
| `predict_mixture` | Return weights, component means, soft/hard predictions, and variance |

Core array operations, with shapes $\Phi:(n,p)$, $B:(p,K)$,
$G:(n,h)$, and $\Gamma:(h,K-1)$:

```python
# Algorithm sketch: inputs are already scaled with training-fitted transforms.
means = expert_design @ coefficients
free_logits = gate_design @ gate_coefficients
logits = np.column_stack((free_logits, np.zeros(len(gate_design))))
log_weights = logits - logsumexp(logits, axis=1, keepdims=True)
log_density = -0.5 * (
    np.log(2.0 * np.pi * variances)[None, :]
    + (lengths[:, None] - means) ** 2 / variances[None, :]
)
log_joint = log_weights + log_density
log_evidence = logsumexp(log_joint, axis=1, keepdims=True)
responsibility = np.exp(log_joint - log_evidence)
negative_log_likelihood = -log_evidence.sum()
```

Here `logsumexp` comes from `scipy.special`. The gate optimization uses the
same stable log-softmax calculation. Use `numpy.random.default_rng` with saved
seeds for reproducibility, and float64 for fitting.

Initialize with several seeded partitions, for example `KMeans` on scaled
spatial/width features followed by regularized expert fits and a gate fit to
slightly smoothed assignments. Include diverse starts so the algorithm can
escape a poor initial partition. Identical experts with a uniform gate can
remain stuck in a symmetric solution. Monitor component effective sample sizes
$N_k=\sum_i r_{ik}$; reject or restart fits with inadequately supported experts.

NumPy and SciPy are existing project dependencies. Scikit-learn is not required
by the first implementation. For an alternative isolated prototype, run a script with
`uv run --extra experiments --with scikit-learn python path/to/prototype.py`.
An implementation using scikit-learn should declare it in the appropriate
dependency extra and update the lockfile.

## Proposed comparison and validation

Start with $K\in\{1,2,3,4\}$ and the existing polynomial degree. Tune expert
shrinkage, gate shrinkage, and the variance floor on training data. Keep all
widths and sides at each turnaround together, retaining the existing buffered
spatial folds. Fit scalers, initialization, and every model parameter inside
each training fold. Use nested spatial validation or reserve a separate final
test set when selecting hyperparameters; repeated model selection on the same
holdouts remains exploratory.

Compare against the original global `network_affine` baseline. Report soft and
hard prediction MAE, RMSE, p95 absolute error, per-width and per-side bias,
conditional negative log-likelihood, predictive interval coverage and width,
component occupancy, and convergence across restarts. Plot gate probabilities,
selected regimes, and residuals over space and width. Check whether transitions
reflect changed route geometry rather than merely fitting outliers.

Do not treat side differences as unobserved noise when side is available as an
input. Failed route evaluations still have no length labels, so this model
predicts length conditional on successful evaluation; feasibility needs separate
treatment. A different start or preference preset requires separate validation,
and a coordinate gate learned at one start does not establish transferability.

## Library references

- [scikit-learn Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html): weighted expert regression.
- [SciPy minimize](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html): gate optimization.
- [SciPy logsumexp](https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.logsumexp.html): stable likelihood and responsibilities.
- [scikit-learn GaussianMixture](https://scikit-learn.org/stable/modules/generated/sklearn.mixture.GaussianMixture.html): a density estimator, not this conditional regression model; its `y` argument is ignored.
