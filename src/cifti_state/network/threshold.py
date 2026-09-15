"""Choosing which edges to draw.

A 9-node directed matrix has 72 off-diagonal entries and a figure with 72
arrows on it says nothing, so every network figure is a thresholded one.  What
the threshold *means* is therefore part of the result and belongs in the
caption; this module implements three rules and names each one.

``edge_count``
    NeuroMArVL's slider.  Take the larger of the two directions for each
    unordered pair, sort those ``n(n-1)/2`` values from large to small, and use
    the ``k``-th as the cutoff; then draw every directed edge at or above it.
    Because the cutoff comes from pairs but is applied to directions, ``k`` is
    not the number of arrows -- it is usually fewer than the arrows you get.
    :func:`max_positive_edge_count` gives the largest ``k`` whose cutoff is
    still positive, past which negative weights start being drawn as if they
    were positive.

``threshold``
    A weight you choose.  Honest and boring; the right rule when the number is
    meaningful (a significance cutoff, a preregistered value).

``top``
    The ``n`` strongest directed edges.  The rule to use when the figure's
    claim is "these are the strongest connections".

The first rule is reproduced exactly, not approximately: its published
``k -> arrows`` table for five datasets is a test case in
``tests/test_network.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..logging_setup import get_logger
from .data import NetworkError

log = get_logger(__name__)

__all__ = [
    "EdgeSet",
    "pair_maxima",
    "threshold_for_edge_count",
    "max_positive_edge_count",
    "select_edges",
]


@dataclass(frozen=True)
class EdgeSet:
    """The edges that survived, and how they were chosen."""

    source: np.ndarray               #: (n_edges,) row index
    target: np.ndarray               #: (n_edges,) column index
    weight: np.ndarray               #: (n_edges,) matrix value, as written
    rule: str = ""                   #: human-readable, for the caption
    threshold: Optional[float] = None

    def __len__(self) -> int:
        return int(self.source.size)

    @property
    def n_edges(self) -> int:
        return len(self)

    @property
    def n_negative(self) -> int:
        return int((self.weight < 0).sum())

    @property
    def reciprocal(self) -> int:
        """How many pairs survived in both directions."""
        both = {(int(a), int(b)) for a, b in zip(self.source, self.target)}
        return sum(1 for a, b in both if (b, a) in both) // 2

    def describe(self) -> str:
        bits = [f"{self.n_edges} edges", self.rule]
        if self.threshold is not None:
            bits.append(f"cutoff {self.threshold:.6g}")
        if self.n_negative:
            bits.append(f"{self.n_negative} negative")
        return "; ".join(b for b in bits if b)


def _offdiag_mask(n: int) -> np.ndarray:
    return ~np.eye(n, dtype=bool)


def pair_maxima(matrix: np.ndarray) -> np.ndarray:
    """For each unordered pair, the larger of the two directions, descending.

    This is the quantity NeuroMArVL ranks.  Note it takes the larger *signed*
    value, not the larger magnitude: a pair whose two directions are ``+0.1``
    and ``-0.4`` ranks at ``+0.1``.
    """
    matrix = np.asarray(matrix, dtype=float)
    n = matrix.shape[0]
    iu = np.triu_indices(n, k=1)
    values = np.maximum(matrix[iu], matrix.T[iu])
    return np.sort(values)[::-1]


def threshold_for_edge_count(matrix: np.ndarray, edge_count: int) -> float:
    """The cutoff NeuroMArVL's slider produces at position ``edge_count``."""
    values = pair_maxima(matrix)
    n_pairs = values.size
    if not 1 <= edge_count <= n_pairs:
        raise NetworkError(
            f"edge_count must be between 1 and {n_pairs} "
            f"(n(n-1)/2 for this matrix), got {edge_count}"
        )
    return float(values[edge_count - 1])


def max_positive_edge_count(matrix: np.ndarray) -> int:
    """The largest ``edge_count`` whose cutoff is still above zero.

    Past this, the cutoff goes negative and negative weights are drawn as
    ordinary edges with nothing to distinguish them -- which is a figure that
    misleads, so the renderer warns when you cross it.
    """
    values = pair_maxima(matrix)
    positive = np.flatnonzero(values > 0)
    return int(positive[-1] + 1) if positive.size else 0


def select_edges(
    matrix: np.ndarray,
    *,
    edge_count: Optional[int] = None,
    threshold: Optional[float] = None,
    top: Optional[int] = None,
    absolute: bool = False,
    drop_negative: bool = False,
    self_edges: bool = False,
) -> EdgeSet:
    """Pick the edges to draw.  Give exactly one of the three rules.

    ``absolute`` ranks by ``|weight|`` instead of by the signed value, for both
    the ``top`` and ``edge_count`` rules; the weights returned keep their sign
    either way, so the renderer can still colour them by sign.

    ``drop_negative`` removes negative weights after selection.  It is the
    honest companion to a large ``edge_count``: rather than drawing an
    inhibitory connection as though it were excitatory, leave it out.
    """
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise NetworkError("select_edges needs a square matrix")
    n = matrix.shape[0]

    given = [name for name, value in
             (("edge_count", edge_count), ("threshold", threshold), ("top", top))
             if value is not None]
    if len(given) > 1:
        raise NetworkError(
            "give only one of edge_count, threshold, top -- got " + ", ".join(given)
        )

    ranked = np.abs(matrix) if absolute else matrix
    valid = np.ones((n, n), dtype=bool) if self_edges else _offdiag_mask(n)

    if edge_count is not None:
        cutoff = threshold_for_edge_count(ranked, edge_count)
        keep = valid & (ranked >= cutoff)
        limit = max_positive_edge_count(ranked)
        if cutoff <= 0 and limit:
            log.warning(
                "edge_count=%d puts the cutoff at %.6g, at or below zero: "
                "negative weights will be drawn with nothing marking them as "
                "negative. The largest edge_count with a positive cutoff here "
                "is %d.", edge_count, cutoff, limit,
            )
        rule = f"NeuroMArVL edge count k={edge_count}"
    elif threshold is not None:
        cutoff = float(threshold)
        keep = valid & (ranked >= cutoff)
        rule = f"weight >= {cutoff:.6g}" + (" (|weight|)" if absolute else "")
    elif top is not None:
        if top < 0:
            raise NetworkError("top must not be negative")
        candidates = np.where(valid, ranked, -np.inf)
        flat = candidates.ravel()
        n_avail = int(np.isfinite(flat).sum())
        n_take = min(int(top), n_avail)
        keep = np.zeros((n, n), dtype=bool)
        if n_take:
            order = np.argsort(flat, kind="stable")[::-1][:n_take]
            keep.ravel()[order] = True
            cutoff = float(flat[order[-1]])
        else:
            cutoff = float("nan")
        rule = f"strongest {n_take} directed edges" + (
            " by |weight|" if absolute else ""
        )
    else:
        keep = valid.copy()
        cutoff = None
        rule = "every edge"

    if drop_negative:
        keep &= matrix > 0
        rule += ", negative removed"

    source, target = np.nonzero(keep)
    weight = matrix[source, target]
    # Strongest first, so that in a crowded figure the weak edges are the ones
    # drawn on top and occluded rather than the other way round.
    order = np.argsort(np.abs(weight), kind="stable")[::-1]
    return EdgeSet(
        source=source[order],
        target=target[order],
        weight=weight[order],
        rule=rule,
        threshold=cutoff,
    )
