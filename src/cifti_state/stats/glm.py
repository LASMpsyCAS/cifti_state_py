"""The vertex-wise linear model, fitted once for every vertex at a time.

One ordinary least squares fit per vertex, done as a handful of matrix
products rather than a loop: with a shared design matrix the whole surface is
fitted in one ``pinv(X) @ Y``.

What comes out is not only the t map.  :class:`GLMFit` also keeps the
**residuals**, and they are not an implementation detail that happens to be
lying around -- they are what the random field correction needs.  The
smoothness of the underlying field has to be measured from something that is
pure noise, and the residuals are the only thing in the analysis that is.
Estimating it from the finished t map instead is biased low, which inflates the
family-wise error rate; that is why ``cifti_state`` could not offer an honest
RFT correction until this module existed.

Two variance models are available for a two-sample comparison:

``pooled`` (the default)
    One error variance for both groups, estimated from the residuals of the
    full model.  This is the standard general linear model, it is what
    SurfStat fits, and its degrees of freedom are a single number -- which is
    what the RFT correction assumes.

``welch``
    A separate variance per group and a Satterthwaite denominator.  Safer when
    the groups differ in size *and* in variance, at the cost of degrees of
    freedom that change from vertex to vertex.  Since RFT wants one number,
    a Welch map is converted to a z map (vertex by vertex, through the
    p-value) before correction, and that is stated rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats

from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress

log = get_logger(__name__)

__all__ = ["GLMFit", "fit_glm", "welch_two_sample", "t_to_z"]


@dataclass
class GLMFit:
    """The fitted model: the effect, its test statistic, and the residuals."""

    beta: np.ndarray              #: (n_predictors, n_vertices)
    residuals: np.ndarray         #: (n_observations, n_vertices)
    sigma: np.ndarray             #: (n_vertices,) residual standard deviation
    effect: np.ndarray            #: (n_vertices,) the contrast, c' beta
    standard_error: np.ndarray    #: (n_vertices,)
    t: np.ndarray                 #: (n_vertices,)
    dof: float
    mask: np.ndarray              #: (n_vertices,) bool -- vertices actually fitted
    statistic: str = "t"          #: t | z
    variance: str = "pooled"      #: pooled | welch
    dof_map: Optional[np.ndarray] = None   #: welch only: per-vertex dof
    notes: list[str] = field(default_factory=list)

    @property
    def n_observations(self) -> int:
        return int(self.residuals.shape[0])

    @property
    def n_vertices(self) -> int:
        return int(self.residuals.shape[1])

    def pvalues(self, *, direction: str = "two_sided") -> np.ndarray:
        """Uncorrected per-vertex p-values."""
        values = np.asarray(self.t, dtype=float)
        out = np.ones(values.size)
        inside = self.mask
        if not inside.any():
            return out
        if self.statistic == "z":
            survival = stats.norm.sf(values[inside])
        else:
            dof = self.dof_map[inside] if self.dof_map is not None else self.dof
            survival = stats.t.sf(values[inside], dof)
        if direction == "positive":
            out[inside] = survival
        elif direction == "negative":
            out[inside] = 1.0 - survival
        else:
            out[inside] = 2.0 * np.minimum(survival, 1.0 - survival)
        return np.clip(out, 0.0, 1.0)

    def describe(self) -> dict:
        inside = self.mask
        values = self.t[inside]
        return {
            "statistic": self.statistic,
            "variance": self.variance,
            "dof": None if self.dof_map is not None else float(self.dof),
            "observations": self.n_observations,
            "vertices_fitted": int(inside.sum()),
            "t_min": float(values.min()) if values.size else 0.0,
            "t_max": float(values.max()) if values.size else 0.0,
        }


def fit_glm(
    data: np.ndarray,
    design_matrix: np.ndarray,
    contrast: np.ndarray,
    *,
    mask: Optional[np.ndarray] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> GLMFit:
    """Fit ``Y = X B + E`` at every vertex and test one contrast.

    *data* is ``(n_observations, n_vertices)``; *design_matrix* is
    ``(n_observations, n_predictors)``; *contrast* is ``(n_predictors,)``.

    Vertices outside *mask*, and vertices with no residual variance at all
    (every subject identical -- usually a region nobody has data in), are left
    at zero and dropped from the returned mask rather than producing infinities.
    """
    y = np.asarray(data, dtype=np.float64)
    x = np.asarray(design_matrix, dtype=np.float64)
    c = np.asarray(contrast, dtype=np.float64).ravel()

    if y.ndim != 2:
        raise ValueError(f"data must be 2-D (observations, vertices), got {y.shape}")
    if x.shape[0] != y.shape[0]:
        raise ValueError(
            f"the design has {x.shape[0]} rows but the data has {y.shape[0]} "
            f"observations"
        )
    if c.size != x.shape[1]:
        raise ValueError(
            f"the contrast has {c.size} entries but the design has {x.shape[1]} "
            f"predictors"
        )

    n_obs, n_vertices = y.shape
    rank = int(np.linalg.matrix_rank(x))
    dof = float(n_obs - rank)
    if dof < 1:
        raise ValueError(
            f"{n_obs} observations and a rank-{rank} design leave {dof} "
            f"residual degrees of freedom"
        )

    vertex_mask = (
        np.ones(n_vertices, dtype=bool) if mask is None
        else np.asarray(mask, dtype=bool).copy()
    )
    report_progress(progress, 0.1, "fitting the model")
    check_cancelled(cancel)

    # One pseudo-inverse for the whole surface: the design is shared, so the
    # per-vertex fit is a single matrix product rather than V little solves.
    pinv = np.linalg.pinv(x)                       # (p, n)
    beta = pinv @ y                                # (p, V)
    residuals = y - x @ beta                       # (n, V)

    report_progress(progress, 0.6, "computing the statistic")
    check_cancelled(cancel)

    sse = np.einsum("ij,ij->j", residuals, residuals)
    sigma2 = sse / dof
    sigma = np.sqrt(np.maximum(sigma2, 0.0))

    # Var(c'b) = sigma^2 * c' (X'X)^+ c -- the design part is one scalar.
    xtx_inv = np.linalg.pinv(x.T @ x)
    variance_factor = float(c @ xtx_inv @ c)
    if variance_factor <= 0:
        raise ValueError(
            "the contrast is not estimable from this design (its variance "
            "factor is zero) -- it asks about a direction the model cannot see"
        )

    effect = c @ beta                              # (V,)
    standard_error = sigma * np.sqrt(variance_factor)

    # "No residual variance" has to be judged relative to the data, not against
    # a hard zero: a vertex where every subject is identical leaves residuals of
    # 1e-17 rather than 0.0, and dividing by that manufactures a huge t.
    total = np.einsum("ij,ij->j", y, y)
    flat = sse <= 1e-12 * np.maximum(total, np.finfo(float).tiny)
    if flat.any():
        log.info(
            "%d vertices have no residual variance and are excluded",
            int((flat & vertex_mask).sum()),
        )
    vertex_mask &= ~flat
    vertex_mask &= np.isfinite(effect) & np.isfinite(standard_error)

    t = np.zeros(n_vertices)
    np.divide(effect, standard_error, out=t, where=vertex_mask)
    t[~vertex_mask] = 0.0

    report_progress(progress, 1.0, "model fitted")
    fit = GLMFit(
        beta=beta,
        residuals=residuals,
        sigma=sigma,
        effect=effect,
        standard_error=standard_error,
        t=t,
        dof=dof,
        mask=vertex_mask,
        statistic="t",
        variance="pooled",
    )
    log.info(
        "GLM: %d observations, %d predictors, df=%g, %d vertices fitted, "
        "t in [%.3f, %.3f]",
        n_obs, x.shape[1], dof, int(vertex_mask.sum()),
        float(t[vertex_mask].min()) if vertex_mask.any() else 0.0,
        float(t[vertex_mask].max()) if vertex_mask.any() else 0.0,
    )
    return fit


def welch_two_sample(
    data: np.ndarray,
    group: np.ndarray,
    *,
    mask: Optional[np.ndarray] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> GLMFit:
    """Two-sample t without assuming the groups share a variance.

    *group* is a boolean array, ``True`` for the second group; the statistic is
    *second minus first*, matching :func:`~cifti_state.stats.design.two_sample_design`.

    The residuals kept here are within-group centred, which is what they would
    be under a model with a separate mean per group -- so the smoothness
    estimate the correction uses is still measured on noise.
    """
    y = np.asarray(data, dtype=np.float64)
    second = np.asarray(group, dtype=bool)
    first = ~second
    n1, n2 = int(first.sum()), int(second.sum())
    if n1 < 2 or n2 < 2:
        raise ValueError(f"Welch needs at least 2 per group, got {n1} and {n2}")

    report_progress(progress, 0.2, "computing group means")
    check_cancelled(cancel)

    mean1 = y[first].mean(axis=0)
    mean2 = y[second].mean(axis=0)
    var1 = y[first].var(axis=0, ddof=1)
    var2 = y[second].var(axis=0, ddof=1)

    residuals = np.empty_like(y)
    residuals[first] = y[first] - mean1
    residuals[second] = y[second] - mean2

    term1 = var1 / n1
    term2 = var2 / n2
    denominator = term1 + term2

    vertex_mask = (
        np.ones(y.shape[1], dtype=bool) if mask is None
        else np.asarray(mask, dtype=bool).copy()
    )
    vertex_mask &= denominator > 0

    effect = mean2 - mean1
    standard_error = np.sqrt(np.maximum(denominator, 0.0))
    t = np.zeros(y.shape[1])
    np.divide(effect, standard_error, out=t, where=vertex_mask)

    # Satterthwaite: the degrees of freedom now differ from vertex to vertex.
    dof_map = np.zeros(y.shape[1])
    np.divide(
        denominator ** 2,
        term1 ** 2 / max(n1 - 1, 1) + term2 ** 2 / max(n2 - 1, 1),
        out=dof_map,
        where=vertex_mask,
    )
    dof_map = np.clip(dof_map, 1.0, float(n1 + n2 - 2))

    pooled_dof = float(n1 + n2 - 2)
    report_progress(progress, 1.0, "Welch statistic computed")
    inside = vertex_mask
    log.info(
        "Welch: n=%d/%d, df %.1f-%.1f (median %.1f), t in [%.3f, %.3f]",
        n1, n2,
        float(dof_map[inside].min()) if inside.any() else 0.0,
        float(dof_map[inside].max()) if inside.any() else 0.0,
        float(np.median(dof_map[inside])) if inside.any() else 0.0,
        float(t[inside].min()) if inside.any() else 0.0,
        float(t[inside].max()) if inside.any() else 0.0,
    )
    return GLMFit(
        beta=np.vstack([mean1, mean2 - mean1]),
        residuals=residuals,
        sigma=np.sqrt(np.maximum((var1 * (n1 - 1) + var2 * (n2 - 1)) / pooled_dof, 0.0)),
        effect=effect,
        standard_error=standard_error,
        t=t,
        dof=pooled_dof,
        mask=vertex_mask,
        statistic="t",
        variance="welch",
        dof_map=dof_map,
        notes=["Satterthwaite degrees of freedom vary by vertex"],
    )


def t_to_z(fit: GLMFit) -> GLMFit:
    """Convert a t map to the equivalent z map, vertex by vertex.

    Needed when the degrees of freedom are not one number -- a Welch test --
    because random field theory wants a single field with known behaviour.
    Converting through the p-value gives a field that is standard normal under
    the null everywhere, which is exactly what the Gaussian EC densities assume.
    """
    if fit.statistic == "z":
        return fit
    values = np.asarray(fit.t, dtype=float)
    z = np.zeros_like(values)
    inside = fit.mask
    if inside.any():
        dof = fit.dof_map[inside] if fit.dof_map is not None else fit.dof
        # Work in the log-survival domain so large |t| does not saturate to
        # p = 0 and come back as an infinite z.
        log_sf = stats.t.logsf(np.abs(values[inside]), dof)
        magnitude = stats.norm.isf(np.exp(np.clip(log_sf, -700, 0)))
        z[inside] = np.sign(values[inside]) * magnitude
    return GLMFit(
        beta=fit.beta,
        residuals=fit.residuals,
        sigma=fit.sigma,
        effect=fit.effect,
        standard_error=fit.standard_error,
        t=z,
        dof=np.inf,
        mask=fit.mask,
        statistic="z",
        variance=fit.variance,
        dof_map=None,
        notes=list(fit.notes) + ["t converted to z vertex-wise"],
    )
