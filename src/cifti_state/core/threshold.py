"""Statistic thresholds: fixed, FDR-derived, or percentile.

The FDR path is a faithful port of ``w_find_fdr_p_z.m``:

    mask   = data > 0
    p      = 1 - normcdf(data[mask])            # one sided
    p_adj  = mafdr(p, 'BHFDR', true)            # Benjamini-Hochberg
    thresh = min(data[p_adj < q])

with two things made explicit that the MATLAB version left implicit:

* the direction is a parameter rather than hard-wired to the positive tail, so
  negative and two-sided contrasts can be thresholded too;
* the statistic type is declared.  ``1 - normcdf`` treats its input as a
  z-score.  Feeding it t-values (as ``surfstate.m`` did with ``slm.t``) silently
  mis-states the p-values, so a t map must come with degrees of freedom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy import stats

from ..logging_setup import get_logger
from ..types import Direction, Statistic

log = get_logger(__name__)

__all__ = [
    "ThresholdResult",
    "compute_threshold",
    "fdr_threshold",
    "benjamini_hochberg",
    "to_pvalues",
    "suprathreshold_mask",
]


@dataclass(frozen=True)
class ThresholdResult:
    """A threshold pair plus a full record of how it was obtained."""

    positive: Optional[float]
    negative: Optional[float]
    method: str
    direction: Direction
    statistic: Statistic
    df: Optional[float] = None
    n_suprathreshold: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        parts = []
        if self.positive is not None:
            parts.append(f"+{self.positive:.6g}")
        if self.negative is not None:
            parts.append(f"{self.negative:.6g}")
        return f"{self.method}({', '.join(parts) or 'none'})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "positive": self.positive,
            "negative": self.negative,
            "method": self.method,
            "direction": self.direction,
            "statistic": self.statistic,
            "df": self.df,
            "n_suprathreshold": self.n_suprathreshold,
            "params": dict(self.params),
        }


# --------------------------------------------------------------------------- #
# p-value helpers
# --------------------------------------------------------------------------- #


def to_pvalues(
    values: np.ndarray,
    *,
    statistic: Statistic = "z",
    df: Optional[float] = None,
    tail: str = "upper",
) -> np.ndarray:
    """Convert statistics to p-values.

    ``tail`` is ``"upper"``, ``"lower"`` or ``"two"``.
    """
    values = np.asarray(values, dtype=float)
    if statistic == "z":
        dist: Any = stats.norm
        args: tuple = ()
    elif statistic == "t":
        if df is None:
            raise ValueError("statistic='t' requires df")
        dist = stats.t
        args = (float(df),)
    else:
        raise ValueError(
            f"cannot convert statistic={statistic!r} to p-values; "
            "declare the map as 'z' or 't', or use a fixed/percentile threshold"
        )

    if tail == "upper":
        return dist.sf(values, *args)
    if tail == "lower":
        return dist.cdf(values, *args)
    if tail == "two":
        return 2.0 * dist.sf(np.abs(values), *args)
    raise ValueError(f"unknown tail {tail!r}")


def benjamini_hochberg(pvalues: np.ndarray) -> np.ndarray:
    """BH step-up adjusted p-values (equivalent to MATLAB ``mafdr(...,'BHFDR',true)``)."""
    p = np.asarray(pvalues, dtype=float)
    n = p.size
    if n == 0:
        return p.copy()
    order = np.argsort(p)
    ranked = p[order]
    adjusted = ranked * n / np.arange(1, n + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    out = np.empty(n, dtype=float)
    out[order] = adjusted
    return out


# --------------------------------------------------------------------------- #
# threshold computation
# --------------------------------------------------------------------------- #


def fdr_threshold(
    values: np.ndarray,
    *,
    q: float = 0.05,
    direction: Direction = "positive",
    statistic: Statistic = "z",
    df: Optional[float] = None,
    legacy: bool = True,
) -> ThresholdResult:
    """Smallest statistic value whose BH-adjusted p-value is below *q*.

    ``legacy=True`` reproduces ``w_find_fdr_p_z.m``: only values strictly
    greater than zero enter the FDR correction, and the p-values are one-sided.
    ``legacy=False`` runs the correction over every finite value and, for
    ``direction='two_sided'``, uses two-sided p-values.
    """
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]

    positive: Optional[float] = None
    negative: Optional[float] = None
    detail: dict[str, Any] = {"q": q, "legacy": legacy, "n_tested": {}}

    if direction in ("positive", "two_sided"):
        pool = finite[finite > 0] if legacy else finite
        tail = "two" if (direction == "two_sided" and not legacy) else "upper"
        positive = _tail_threshold(
            pool, q=q, statistic=statistic, df=df, tail=tail, upper=True
        )
        detail["n_tested"]["positive"] = int(pool.size)

    if direction in ("negative", "two_sided"):
        pool = finite[finite < 0] if legacy else finite
        tail = "two" if (direction == "two_sided" and not legacy) else "lower"
        negative = _tail_threshold(
            pool, q=q, statistic=statistic, df=df, tail=tail, upper=False
        )
        detail["n_tested"]["negative"] = int(pool.size)

    result = ThresholdResult(
        positive=positive,
        negative=negative,
        method="fdr",
        direction=direction,
        statistic=statistic,
        df=df,
        params=detail,
    )
    return _count(result, values)


def compute_threshold(
    values: np.ndarray,
    *,
    method: str = "fdr",
    value: Optional[float] = None,
    q: float = 0.05,
    percentile: float = 95.0,
    direction: Direction = "positive",
    statistic: Statistic = "z",
    df: Optional[float] = None,
    legacy: bool = True,
) -> ThresholdResult:
    """Dispatch to the requested thresholding method.

    ``fixed``
        Use *value* directly (and ``-value`` for the negative tail unless
        *value* is already negative).
    ``fdr``
        See :func:`fdr_threshold`.
    ``percentile``
        Take the *percentile* of the positive values (and the matching lower
        percentile of the negative values).
    """
    values = np.asarray(values, dtype=float)

    if method == "fixed":
        if value is None:
            raise ValueError("method='fixed' requires value=...")
        magnitude = abs(float(value))
        positive = magnitude if direction in ("positive", "two_sided") else None
        negative = -magnitude if direction in ("negative", "two_sided") else None
        result = ThresholdResult(
            positive=positive,
            negative=negative,
            method="fixed",
            direction=direction,
            statistic=statistic,
            df=df,
            params={"value": float(value)},
        )
        return _count(result, values)

    if method == "fdr":
        return fdr_threshold(
            values, q=q, direction=direction, statistic=statistic, df=df, legacy=legacy
        )

    if method == "percentile":
        finite = values[np.isfinite(values)]
        positive = negative = None
        if direction in ("positive", "two_sided"):
            pool = finite[finite > 0]
            positive = float(np.percentile(pool, percentile)) if pool.size else None
        if direction in ("negative", "two_sided"):
            pool = finite[finite < 0]
            negative = (
                float(np.percentile(pool, 100.0 - percentile)) if pool.size else None
            )
        result = ThresholdResult(
            positive=positive,
            negative=negative,
            method="percentile",
            direction=direction,
            statistic=statistic,
            df=df,
            params={"percentile": percentile},
        )
        return _count(result, values)

    raise ValueError(
        f"unknown threshold method {method!r}; use 'fixed', 'fdr' or 'percentile'"
    )


def suprathreshold_mask(
    values: np.ndarray, threshold: ThresholdResult, *, sign: str = "any"
) -> np.ndarray:
    """Boolean mask of vertices surviving *threshold*.

    ``sign`` selects ``"positive"``, ``"negative"`` or ``"any"``.
    NaN never survives.
    """
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    mask = np.zeros(values.shape, dtype=bool)
    if sign in ("positive", "any") and threshold.positive is not None:
        mask |= finite & (values > threshold.positive)
    if sign in ("negative", "any") and threshold.negative is not None:
        mask |= finite & (values < threshold.negative)
    return mask


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _tail_threshold(
    pool: np.ndarray,
    *,
    q: float,
    statistic: Statistic,
    df: Optional[float],
    tail: str,
    upper: bool,
) -> Optional[float]:
    if pool.size == 0:
        log.warning("FDR: no values in the %s tail", "positive" if upper else "negative")
        return None
    p = to_pvalues(pool, statistic=statistic, df=df, tail=tail)
    p_adj = benjamini_hochberg(p)
    survives = (p_adj < q) & (p_adj != 0)
    if not survives.any():
        log.warning(
            "FDR: nothing survives q=%.4g in the %s tail (min adjusted p = %.4g)",
            q,
            "positive" if upper else "negative",
            float(p_adj.min()),
        )
        return None
    return float(pool[survives].min() if upper else pool[survives].max())


def _count(result: ThresholdResult, values: np.ndarray) -> ThresholdResult:
    n = int(suprathreshold_mask(values, result).sum())
    return ThresholdResult(
        positive=result.positive,
        negative=result.negative,
        method=result.method,
        direction=result.direction,
        statistic=result.statistic,
        df=result.df,
        n_suprathreshold=n,
        params=result.params,
    )
