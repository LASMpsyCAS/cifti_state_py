"""Random field theory: what a null map of this smoothness would have produced.

Two questions, two answers.

**Peak level.** How high does the statistic have to get somewhere -- anywhere --
in the search region before it is surprising?  The expected Euler
characteristic of the excursion set is, at high thresholds, very nearly the
probability that the maximum exceeds the threshold::

    P(max > u)  ≈  Σ_d  R_d · ρ_d(u)

where ``R_d`` are the resel counts of the search region and ``ρ_d`` are the EC
densities of the field.  A peak-level P value is strong evidence about one
vertex and says nothing about its neighbours.

**Cluster level.** How big does a blob above a chosen cluster-forming threshold
have to be?  Above threshold ``u`` the number of clusters is approximately
Poisson with mean ``E[N] = R_D · ρ_D(u)``, so one cluster's tail probability
becomes a family-wise one through ``1 - exp(-E[N] · P(S > s))``.

What ``P(S > s)`` is depends on something easy to get wrong.  If the field's
variance were *known*, a cluster's area on a surface would be very nearly
exponential with mean ``ρ_0(u)/ρ_D(u)``, and that closed form would be the
whole story.  In a t field the variance is not known -- it was estimated from
the same residuals -- and that extra randomness fattens the tail considerably.
At 20 degrees of freedom and a cluster-forming threshold of 3.1, the closed
form gives a mid-sized cluster a P value roughly a hundred times too small.  So
the exact distribution is used whenever the degrees of freedom are finite: the
cluster size is a product of independent random factors, its logarithm is
therefore a sum, and the density of a sum is one FFT away.

Cluster-level evidence is about the blob, not about any vertex in it: it
licenses "something is going on here", never "this vertex is significant".

The cluster-forming threshold is a real choice and it changes the answer -- a
low threshold favours large diffuse effects, a high one favours focal ones.  It
must be fixed before looking at the data, which is why it is an explicit
argument with no clever default.

**How well calibrated is any of this?**  Measured, not assumed.  On null data
smoothed to ~11 mm FWHM on the fs_LR 32k mesh, over 500 simulated one-sample
tests at n=28:

=========================  =====================================
peak level                 realised FWE 0.040 -- well calibrated
cluster extent             realised FWE 0.158 -- liberal
=========================  =====================================

The peak-level result is what the theory promises.  The cluster-extent result
is not, and the reason is visible in the intermediate quantities: the expected
*number* of clusters is right to within a few percent, but real clusters come
out 25-35% larger than the theory expects, because the vertices that cross the
threshold sit preferentially where the field happens to be locally rough.  That
is a known limitation of parametric cluster-extent inference rather than a
defect of this implementation -- the formulas here reproduce SurfStat's to
about one part in a thousand -- and it is why
:mod:`cifti_state.stats.permutation` exists and why cluster-level conclusions
should rest on it.

References: Worsley KJ, Marrett S, Neelin P, Vandal AC, Friston KJ, Evans AC
(1996), *A unified statistical approach for determining significant signals in
images of cerebral activation*, Human Brain Mapping 4:58-73; Friston KJ,
Worsley KJ, Frackowiak RSJ, Mazziotta JC, Evans AC (1994), *Assessing the
significance of focal activations using their spatial extent*, Human Brain
Mapping 1:210-220.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np
from scipy import optimize, stats
from scipy.special import gammaln

from ..logging_setup import get_logger
from .resels import FOUR_LOG_2

log = get_logger(__name__)

__all__ = ["RandomField", "ec_density"]


def ec_density(u, dimension: int, *, statistic: str = "z",
               dof: Optional[float] = None) -> np.ndarray:
    """Euler characteristic density ``ρ_d(u)``, per resel.

    ``dimension`` 0 gives the point-wise tail probability; 1 and 2 give the
    densities that multiply the search region's boundary length and area.
    """
    u = np.asarray(u, dtype=float)

    if statistic == "z" or dof is None or not np.isfinite(dof):
        if dimension == 0:
            return stats.norm.sf(u)
        gaussian = np.exp(-(u ** 2) / 2.0)
        if dimension == 1:
            return np.sqrt(FOUR_LOG_2) / (2.0 * np.pi) * gaussian
        if dimension == 2:
            return FOUR_LOG_2 / (2.0 * np.pi) ** 1.5 * u * gaussian
        raise ValueError(f"dimension must be 0, 1 or 2, got {dimension}")

    nu = float(dof)
    if dimension == 0:
        return stats.t.sf(u, nu)
    # (1 + u^2/nu)^(-(nu-1)/2), in logs so a large u cannot underflow to zero
    # before the prefactor has had a chance to matter.
    log_kernel = -((nu - 1.0) / 2.0) * np.log1p(u ** 2 / nu)
    if dimension == 1:
        return np.sqrt(FOUR_LOG_2) / (2.0 * np.pi) * np.exp(log_kernel)
    if dimension == 2:
        log_ratio = gammaln((nu + 1.0) / 2.0) - 0.5 * np.log(nu / 2.0) - gammaln(nu / 2.0)
        return (
            FOUR_LOG_2 / (2.0 * np.pi) ** 1.5
            * np.exp(log_ratio) * u * np.exp(log_kernel)
        )
    raise ValueError(f"dimension must be 0, 1 or 2, got {dimension}")


@dataclass
class RandomField:
    """A statistic field on a search region, ready to be asked for P values."""

    resels: np.ndarray        #: (3,) resel counts, from :func:`~cifti_state.stats.resels.compute_resels`
    n_vertices: int           #: vertices in the search region, for the Bonferroni cap
    dof: float = np.inf
    statistic: str = "z"      #: t | z
    dimension: int = 2
    direction: str = "two_sided"   #: two_sided | positive | negative

    #: A two-sided test searches both tails, so there are twice as many chances
    #: for the field to be surprising. Forgetting this is the easiest way to
    #: halve your P values by accident, so it lives in the field rather than in
    #: whatever calls it.
    @property
    def tails(self) -> int:
        return 2 if self.direction == "two_sided" else 1

    # -- densities ---------------------------------------------------------- #

    def density(self, u, dimension: int) -> np.ndarray:
        return ec_density(u, dimension, statistic=self.statistic, dof=self.dof)

    def expected_ec(self, u) -> np.ndarray:
        """``Σ_d R_d ρ_d(u)`` -- the expected Euler characteristic above *u*.

        One tail only; :attr:`tails` is applied by the callers that need it.
        """
        u = np.asarray(u, dtype=float)
        total = np.zeros(u.shape, dtype=float)
        for d in range(self.dimension + 1):
            total = total + float(self.resels[d]) * self.density(u, d)
        return total

    # -- peak level --------------------------------------------------------- #

    def peak_pvalue(self, u) -> np.ndarray:
        """FWE-corrected P value for a statistic height.

        The Bonferroni bound is applied as well and the smaller of the two is
        used: at low thresholds, or on a small or very rough search region, the
        EC approximation can exceed the honest bound of "one test per vertex".
        """
        u = np.asarray(u, dtype=float)
        rft = self.tails * self.expected_ec(u)
        bonferroni = self.tails * self.n_vertices * self.density(u, 0)
        return np.clip(np.minimum(rft, bonferroni), 0.0, 1.0)

    def peak_threshold(self, alpha: float = 0.05) -> float:
        """The height whose corrected P value is *alpha*."""
        return self._invert(lambda u: self.peak_pvalue(u) - alpha)

    # -- cluster level ------------------------------------------------------ #

    def expected_clusters(self, u: float) -> float:
        """``E[N]``: how many clusters a null map would show above *u*.

        Doubled for a two-sided test, where a blob in either tail counts.
        """
        return float(self.tails * self.resels[self.dimension]
                     * self.density(float(u), self.dimension))

    def expected_cluster_resels(self, u: float) -> float:
        """``E[S]``: the average size, in resels, of one of those clusters."""
        top = float(self.density(float(u), 0))
        bottom = float(self.density(float(u), self.dimension))
        if bottom <= 0:
            return np.inf
        return top / bottom

    def cluster_survival(self, extent_resels, u: float, *,
                         method: str = "auto") -> np.ndarray:
        """``P(S > s)`` for a *single* cluster, *s* in resels.

        ``method="gaussian"`` uses the closed form, which assumes the field's
        variance is known.  For a t field it is not -- it was estimated from the
        same residuals -- and that extra randomness fattens the tail of the
        cluster-size distribution considerably.  ``method="auto"`` (the
        default) therefore switches to the exact distribution whenever the
        degrees of freedom are finite.  The difference is not cosmetic: at
        df=20 and a cluster-forming threshold of 3.1 the closed form
        understates a mid-sized cluster's P value by two orders of magnitude,
        which is an inflated false-positive rate, not a rounding error.
        """
        extent = np.maximum(np.asarray(extent_resels, dtype=float), 0.0)
        use_exact = (
            method == "exact"
            or (method == "auto" and self.statistic == "t" and np.isfinite(self.dof))
        )
        if use_exact and self.dof > self.dimension:
            try:
                return _t_cluster_survival(
                    extent, float(u), float(self.dof), self.dimension,
                    float(self.density(float(u), 0)),
                    float(self.density(float(u), self.dimension)),
                )
            except Exception as exc:  # pragma: no cover - numerical edge cases
                log.warning(
                    "the exact cluster-size distribution failed (%s); falling "
                    "back to the Gaussian approximation, which is liberal", exc,
                )

        mean_size = self.expected_cluster_resels(u)
        if not np.isfinite(mean_size) or mean_size <= 0:
            return np.zeros(extent.shape)
        return np.exp(-((extent / mean_size) ** (2.0 / self.dimension)))

    def cluster_pvalue(self, extent_resels, u: float, *,
                       method: str = "auto") -> np.ndarray:
        """FWE-corrected P value for a cluster's extent, measured in resels.

        The number of clusters above *u* is Poisson, so one cluster's tail
        probability becomes a family-wise one through
        ``1 - exp(-E[N] · P(S > s))``.
        """
        single = self.cluster_survival(extent_resels, u, method=method)
        expected_n = self.expected_clusters(u)
        return np.clip(1.0 - np.exp(-expected_n * single), 0.0, 1.0)

    def cluster_extent_threshold(self, alpha: float = 0.05, *, u: float,
                                 method: str = "auto") -> float:
        """The extent in resels a cluster needs to reach corrected *alpha*."""
        expected_n = self.expected_clusters(u)
        if expected_n <= 0:
            return np.inf
        target = -np.log1p(-alpha) / expected_n
        if target >= 1:
            return 0.0
        if target <= 0:
            return np.inf

        def gap(s: float) -> float:
            return float(self.cluster_survival(np.array([s]), u, method=method)[0]) - target

        low, high = 0.0, max(self.expected_cluster_resels(u), 1.0)
        for _ in range(80):
            if gap(high) < 0:
                break
            high *= 1.6
        else:  # pragma: no cover
            return float(high)
        if gap(low) < 0:
            return 0.0
        return float(optimize.brentq(gap, low, high, xtol=1e-10, maxiter=200))

    # -- reporting ---------------------------------------------------------- #

    def describe(self, u: Optional[float] = None) -> str:
        parts = [
            f"resels {np.array2string(np.asarray(self.resels), precision=1)}",
            f"{self.n_vertices} vertices",
            f"{self.statistic}" + (f"({self.dof:g})" if np.isfinite(self.dof) else ""),
            f"peak FWE 0.05 at {self.peak_threshold(0.05):.3f}",
        ]
        if u is not None:
            parts.append(
                f"at u={u:g}: E[N]={self.expected_clusters(u):.2f}, "
                f"E[S]={self.expected_cluster_resels(u):.2f} resels, "
                f"cluster FWE 0.05 at "
                f"{self.cluster_extent_threshold(0.05, u=u):.2f} resels"
            )
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return {
            "resels": [float(r) for r in self.resels],
            "n_vertices": int(self.n_vertices),
            "statistic": self.statistic,
            "direction": self.direction,
            "dof": None if not np.isfinite(self.dof) else float(self.dof),
            "peak_threshold_05": float(self.peak_threshold(0.05)),
        }

    # -- internals ---------------------------------------------------------- #

    def _invert(self, function) -> float:
        """Solve ``function(u) = 0`` for a decreasing function of u."""
        low, high = 0.0, 5.0
        for _ in range(60):
            if function(high) < 0:
                break
            high *= 1.6
        else:  # pragma: no cover - would need an absurd search region
            return float(high)
        if function(low) < 0:
            return 0.0
        return float(optimize.brentq(function, low, high, xtol=1e-8, maxiter=200))


# --------------------------------------------------------------------------- #
# the exact cluster-size distribution for a t field
# --------------------------------------------------------------------------- #
#
# With the variance known, a cluster's area above threshold is very nearly
# exponential and the closed form above is the whole story.  With the variance
# *estimated* -- a t field -- the cluster size picks up several extra random
# factors, and the distribution of their product is what actually governs the
# tail.  Worsley's construction writes the size as
#
#     S = alpha * B^(d/2) * prod_i (chi^2_nu_i / nu_i)^a_i
#
# so the log of S is a sum of independent log-densities, and the density of a
# sum is a convolution -- done here with one FFT, on a log-spaced grid, exactly
# as SurfStat does it.  Only the case this package needs is implemented: a
# univariate t field on a 2-D surface with the smoothness treated as known.


@lru_cache(maxsize=64)
def _t_cluster_grid(dof: float, dimension: int, n_grid: int = 4096):
    """The log-density grid for ``log(S / alpha)``, cached per (df, D).

    Returns ``(y, survival, mu0)`` where *survival* is ``P(log(S/alpha) > y)``
    and *mu0* is the mean of the product, needed to scale the axis.
    """
    d = float(dimension)
    nu = float(dof)
    a = d / 2.0

    # Grid: wide enough that the density has decayed at both ends.
    upper = a * 10.0
    lower = a * np.log((1.0 - (1.0 - 1e-6) ** (2.0 / (nu - d))) * nu / 2.0)
    dy = (upper - lower) / n_grid
    lower = np.round(lower / dy) * dy
    y = np.arange(n_grid) * dy + lower

    densities = []
    means = []

    # 1. Beta(1, (nu - d)/2) ^ (d/2): the shape of the excursion set itself.
    scaled = np.exp(y / a) / nu * 2.0
    scaled = np.where(scaled < 1.0, scaled, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        beta_density = (
            (1.0 - scaled) ** ((nu - d) / 2.0 - 1.0) * ((nu - d) / 2.0) * scaled / a
        )
    densities.append(np.nan_to_num(beta_density))
    means.append(np.exp(
        gammaln(a + 1.0) + gammaln((nu - d + 2.0) / 2.0)
        - gammaln((nu + 2.0) / 2.0) + a * np.log(nu / 2.0)
    ))

    # 2. The chi-square factors: one from the numerator field, then one per
    #    dimension from the estimated variance.
    nus = [1.0 + nu - d] + [nu + 2.0 - k for k in range(1, dimension + 1)]
    powers = [d / 2.0] + [-0.5] * dimension
    for chi_dof, power in zip(nus, powers):
        shifted = y / power + np.log(chi_dof)
        densities.append(
            np.exp(
                chi_dof / 2.0 * shifted - np.exp(shifted) / 2.0
                - (chi_dof / 2.0) * np.log(2.0) - gammaln(chi_dof / 2.0)
            ) / abs(power)
        )
        means.append(np.exp(
            gammaln(chi_dof / 2.0 + power) - gammaln(chi_dof / 2.0)
            - power * np.log(chi_dof / 2.0)
        ))

    stacked = np.column_stack(densities)
    n_factors = stacked.shape[1]

    # Convolve in the frequency domain. The shift term puts the result back on
    # the same grid: convolving n densities that each start at `lower` gives a
    # result starting at n * lower, and this undoes that offset.
    omega = 2.0 * np.pi * np.arange(n_grid) / n_grid / dy
    shift = (np.cos(-lower * omega) + 1j * np.sin(-lower * omega)) * dy
    product = np.prod(np.fft.fft(stacked, axis=0), axis=1) * shift ** (n_factors - 1)
    density = np.real(np.fft.ifft(product))

    survival = np.flip(np.cumsum(np.flip(density)) * dy)
    survival = np.clip(survival, 0.0, 1.0)
    return y, survival, float(np.prod(means)), dy


def _t_cluster_survival(
    extent: np.ndarray, u: float, dof: float, dimension: int,
    density_0: float, density_d: float,
) -> np.ndarray:
    """``P(S > s)`` for one cluster of a t field, *s* in resels."""
    if density_d <= 0:
        return np.zeros(np.shape(extent))
    y, survival, mu0, dy = _t_cluster_grid(float(dof), int(dimension))
    # S = alpha * exp(Y): alpha carries the expected size, exp(Y) the shape.
    alpha = density_0 / density_d / mu0
    if alpha <= 0 or not np.isfinite(alpha):
        return np.zeros(np.shape(extent))

    extent = np.asarray(extent, dtype=float)
    out = np.ones(extent.shape)
    positive = extent > 0
    if positive.any():
        # The + dy/2 is the mid-point correction for the grid the density was
        # integrated on; without it small clusters are biased.
        log_extent = np.log(extent[positive] / alpha) + dy / 2.0
        out[positive] = np.interp(log_extent, y, survival, left=1.0, right=0.0)
    return np.clip(out, 0.0, 1.0)
