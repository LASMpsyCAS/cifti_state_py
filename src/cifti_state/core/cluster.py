"""Surface cluster labelling.

Replaces ``cluster_brain_surface.m`` + ``get_clusters_fsLR32k.m``.

Differences from the MATLAB original, all deliberate:

* **Both signs.**  The original masked with ``map .* (map > threshold)`` and
  then ran the search with ``threshold = 0``, so sub-threshold vertices became
  exactly zero and negative clusters could never be found.  ``direction`` now
  selects ``positive`` (the legacy behaviour), ``negative`` or ``two_sided``.
* **Symmetric infinity handling.**  ``map(isinf(map)) = max(...)`` turned
  ``-inf`` into a large *positive* value, which manufactures clusters.
  ``inf_policy='clip'`` sends ``+inf`` to the largest finite value and ``-inf``
  to the smallest; ``inf_policy='legacy'`` restores the old behaviour.
* **Linear-time search.**  The original pushed neighbours onto the queue
  without checking whether they were already queued, so vertices were visited
  many times.  Connected components come from ``scipy.sparse.csgraph``.

Cluster numbering is unchanged: clusters are ordered by hemisphere (left
first), then -- with ``two_sided`` -- by sign (positive first), then by the
smallest vertex index they contain.  For a positive-only analysis this is
exactly the order the MATLAB loop produced, and the output has been verified
vertex-for-vertex against the reference dscalar files it wrote.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from ..io.cifti import SurfaceStatMap
from ..logging_setup import get_logger
from ..types import (
    CancelToken,
    Direction,
    ProgressFn,
    check_cancelled,
    report_progress,
)
from .threshold import ThresholdResult

log = get_logger(__name__)

__all__ = ["ClusterParams", "ClusterInfo", "ClusterResult", "find_clusters", "sanitise"]


@dataclass(frozen=True)
class ClusterParams:
    """Everything that determined a :class:`ClusterResult`."""

    threshold_positive: Optional[float]
    threshold_negative: Optional[float]
    extent: int
    direction: Direction
    inf_policy: str = "clip"          # clip | legacy | nan
    restrict_to_present: bool = True
    threshold_method: str = "fixed"
    statistic: str = "z"
    df: Optional[float] = None
    legacy_mode: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClusterInfo:
    """Summary of a single cluster."""

    cluster_id: int
    hemisphere: str
    sign: int                 #: +1 or -1
    size_vertices: int
    min_vertex: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClusterResult:
    """Cluster labels on the full mesh plus per-cluster bookkeeping."""

    labels_left: np.ndarray       #: (n_vertices_left,) int, 0 = unassigned
    labels_right: np.ndarray      #: (n_vertices_right,) int
    clusters: list[ClusterInfo]
    params: ClusterParams
    source_name: str = ""

    @property
    def n_clusters(self) -> int:
        return len(self.clusters)

    @property
    def n_left(self) -> int:
        return sum(1 for c in self.clusters if c.hemisphere == "left")

    @property
    def n_right(self) -> int:
        return sum(1 for c in self.clusters if c.hemisphere == "right")

    def labels(self, hemisphere: str) -> np.ndarray:
        h = hemisphere.lower()
        if h in ("l", "lh", "left"):
            return self.labels_left
        if h in ("r", "rh", "right"):
            return self.labels_right
        raise KeyError(f"unknown hemisphere {hemisphere!r}")

    def concatenated(self) -> np.ndarray:
        """Left then right -- the 64984-long vector the MATLAB code returned."""
        return np.concatenate([self.labels_left, self.labels_right])

    def info(self, cluster_id: int) -> ClusterInfo:
        for c in self.clusters:
            if c.cluster_id == cluster_id:
                return c
        raise KeyError(f"no cluster with id {cluster_id}")

    def vertices(self, cluster_id: int) -> tuple[str, np.ndarray]:
        """Return ``(hemisphere, vertex_indices)`` for one cluster."""
        info = self.info(cluster_id)
        return info.hemisphere, np.flatnonzero(self.labels(info.hemisphere) == cluster_id)

    def sizes(self) -> dict[int, int]:
        return {c.cluster_id: c.size_vertices for c in self.clusters}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "n_clusters": self.n_clusters,
            "n_left": self.n_left,
            "n_right": self.n_right,
            "params": self.params.to_dict(),
            "clusters": [c.to_dict() for c in self.clusters],
        }


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def find_clusters(
    stat_map: SurfaceStatMap,
    adjacency: dict[str, csr_matrix],
    threshold: ThresholdResult,
    *,
    extent: int = 20,
    direction: Optional[Direction] = None,
    inf_policy: str = "clip",
    restrict_to_present: bool = True,
    legacy_mode: bool = False,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> ClusterResult:
    """Label connected supra-threshold clusters on both hemispheres.

    Parameters
    ----------
    adjacency
        ``{"left": csr_matrix, "right": csr_matrix}`` -- see
        :func:`cifti_state.io.neighbors.load_neighbors`.
    extent
        Minimum cluster size in vertices.  Clusters smaller than this are
        discarded and do not consume a cluster number.
    inf_policy
        ``clip`` (default), ``legacy`` or ``nan``; see the module docstring.
    legacy_mode
        Convenience switch that forces ``direction='positive'`` and
        ``inf_policy='legacy'``.
    """
    if legacy_mode:
        direction = "positive"
        inf_policy = "legacy"
    direction = direction or threshold.direction

    params = ClusterParams(
        threshold_positive=threshold.positive,
        threshold_negative=threshold.negative,
        extent=int(extent),
        direction=direction,
        inf_policy=inf_policy,
        restrict_to_present=restrict_to_present,
        threshold_method=threshold.method,
        statistic=stat_map.statistic,
        df=stat_map.df,
        legacy_mode=legacy_mode,
    )

    signs: list[int] = []
    if direction in ("positive", "two_sided"):
        signs.append(+1)
    if direction in ("negative", "two_sided"):
        signs.append(-1)
    if not signs:
        raise ValueError(f"unknown direction {direction!r}")

    labels: dict[str, np.ndarray] = {}
    pending: list[tuple[str, int, np.ndarray]] = []   # (hemi, sign, vertices)

    total_steps = 2 * len(signs)
    step = 0
    for hemi in ("left", "right"):
        hemi_data = stat_map.hemi(hemi)
        labels[hemi] = np.zeros(hemi_data.n_vertices, dtype=np.int32)
        values = sanitise(hemi_data.values, inf_policy=inf_policy)
        adj = adjacency[hemi]
        if adj.shape[0] != hemi_data.n_vertices:
            raise ValueError(
                f"{hemi}: adjacency has {adj.shape[0]} vertices but the map has "
                f"{hemi_data.n_vertices}"
            )
        for sign in signs:
            check_cancelled(cancel)
            step += 1
            report_progress(
                progress,
                step / total_steps,
                f"clustering {hemi} hemisphere ({'positive' if sign > 0 else 'negative'})",
            )
            mask = _mask_for(values, threshold, sign)
            if restrict_to_present:
                mask &= hemi_data.present
            for component in _components(mask, adj, extent, cancel):
                pending.append((hemi, sign, component))

    # Deterministic numbering: left before right, positive before negative,
    # then by the smallest vertex index in the cluster.
    hemi_rank = {"left": 0, "right": 1}
    pending.sort(key=lambda item: (hemi_rank[item[0]], 0 if item[1] > 0 else 1, int(item[2].min())))

    clusters: list[ClusterInfo] = []
    for cluster_id, (hemi, sign, vertices) in enumerate(pending, start=1):
        labels[hemi][vertices] = cluster_id
        clusters.append(
            ClusterInfo(
                cluster_id=cluster_id,
                hemisphere=hemi,
                sign=sign,
                size_vertices=int(vertices.size),
                min_vertex=int(vertices.min()),
            )
        )

    result = ClusterResult(
        labels_left=labels["left"],
        labels_right=labels["right"],
        clusters=clusters,
        params=params,
        source_name=stat_map.name,
    )
    log.info(
        "found %d clusters (left %d, right %d) at %s, extent >= %d",
        result.n_clusters,
        result.n_left,
        result.n_right,
        threshold.describe(),
        extent,
    )
    report_progress(progress, 1.0, f"{result.n_clusters} clusters")
    return result


def sanitise(values: np.ndarray, *, inf_policy: str = "clip") -> np.ndarray:
    """Replace non-finite values so that comparisons behave predictably.

    ``clip``
        ``+inf`` -> largest finite value, ``-inf`` -> smallest finite value.
    ``legacy``
        Both infinities -> largest finite value (what the MATLAB code did).
    ``nan``
        Infinities become NaN, i.e. never supra-threshold.

    NaN is always mapped to 0 *after* the supra-threshold test would have
    excluded it, so it never joins a cluster.
    """
    out = np.array(values, dtype=float, copy=True)
    finite = np.isfinite(out)
    if not finite.any():
        return np.zeros_like(out)

    hi = float(out[finite].max())
    lo = float(out[finite].min())

    pos_inf = np.isposinf(out)
    neg_inf = np.isneginf(out)
    if inf_policy == "clip":
        out[pos_inf] = hi
        out[neg_inf] = lo
    elif inf_policy == "legacy":
        out[pos_inf | neg_inf] = hi
    elif inf_policy == "nan":
        out[pos_inf | neg_inf] = np.nan
    else:
        raise ValueError(f"unknown inf_policy {inf_policy!r}")
    return out


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _mask_for(values: np.ndarray, threshold: ThresholdResult, sign: int) -> np.ndarray:
    finite = np.isfinite(values)
    if sign > 0:
        if threshold.positive is None:
            return np.zeros(values.shape, dtype=bool)
        return finite & (values > threshold.positive)
    if threshold.negative is None:
        return np.zeros(values.shape, dtype=bool)
    return finite & (values < threshold.negative)


def _components(
    mask: np.ndarray,
    adjacency: csr_matrix,
    extent: int,
    cancel: Optional[CancelToken],
) -> list[np.ndarray]:
    """Connected components of the sub-graph induced by *mask*, size-filtered."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    check_cancelled(cancel)
    sub = adjacency[idx][:, idx]
    n_components, membership = connected_components(sub, directed=False)
    order = np.argsort(membership, kind="stable")
    boundaries = np.flatnonzero(np.diff(membership[order])) + 1
    out: list[np.ndarray] = []
    for group in np.split(order, boundaries):
        if group.size >= extent:
            out.append(np.sort(idx[group]))
    log.debug(
        "%d supra-threshold vertices -> %d components, %d pass extent %d",
        idx.size, n_components, len(out), extent,
    )
    return out
