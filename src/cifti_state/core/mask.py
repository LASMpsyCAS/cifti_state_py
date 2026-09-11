"""Turn clusters into masks.

Pure array work lives here; writing a mask to disk is
:func:`cifti_state.pipeline.save_cluster_mask`, which needs a CIFTI template.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np

from ..logging_setup import get_logger
from .cluster import ClusterResult

log = get_logger(__name__)

__all__ = ["cluster_mask", "clusters_mask", "mask_summary", "suggest_mask_name"]


def cluster_mask(
    clusters: ClusterResult,
    cluster_id: int,
    *,
    value: float = 1.0,
    dtype: type = np.float32,
) -> tuple[np.ndarray, np.ndarray]:
    """Binary ``(left, right)`` full-mesh masks for one cluster.

    The hemisphere that does not contain the cluster comes back all zeros, so
    the pair can be written straight into a two-hemisphere CIFTI layout.
    """
    return clusters_mask(clusters, [cluster_id], value=value, dtype=dtype)


def clusters_mask(
    clusters: ClusterResult,
    cluster_ids: Iterable[int],
    *,
    value: float = 1.0,
    label_values: bool = False,
    dtype: type = np.float32,
) -> tuple[np.ndarray, np.ndarray]:
    """Binary ``(left, right)`` masks covering every id in *cluster_ids*.

    ``label_values=True`` writes each cluster's own id instead of *value*,
    which turns the result into a small parcellation of the selected clusters.
    """
    wanted = [int(i) for i in cluster_ids]
    if not wanted:
        raise ValueError("no cluster ids given")

    known = {c.cluster_id for c in clusters.clusters}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise KeyError(
            f"no cluster with id {unknown}; available ids are 1..{max(known) if known else 0}"
        )

    out = {}
    for hemi, labels in (("left", clusters.labels_left), ("right", clusters.labels_right)):
        selected = np.isin(labels, wanted)
        if label_values:
            out[hemi] = np.where(selected, labels, 0).astype(dtype)
        else:
            out[hemi] = np.where(selected, dtype(value), dtype(0)).astype(dtype)
    return out["left"], out["right"]


def mask_summary(
    clusters: ClusterResult,
    cluster_ids: Sequence[int],
    *,
    vertex_areas: Optional[dict[str, np.ndarray]] = None,
) -> dict[str, object]:
    """Counts (and, with vertex areas, mm²) for a set of clusters."""
    left, right = clusters_mask(clusters, cluster_ids)
    n_left, n_right = int((left > 0).sum()), int((right > 0).sum())
    summary: dict[str, object] = {
        "cluster_ids": [int(i) for i in cluster_ids],
        "n_vertices_left": n_left,
        "n_vertices_right": n_right,
        "n_vertices": n_left + n_right,
    }
    if vertex_areas:
        area = 0.0
        for hemi, mask in (("left", left), ("right", right)):
            if hemi in vertex_areas:
                area += float(np.asarray(vertex_areas[hemi])[mask > 0].sum())
        summary["area_mm2"] = area
    return summary


def suggest_mask_name(
    source_name: str, cluster_ids: Sequence[int], *, hemisphere: str = ""
) -> str:
    """A stable, informative file stem for a saved mask."""
    stem = str(source_name).split(".")[0] or "clusters"
    ids = sorted(int(i) for i in cluster_ids)
    if len(ids) == 1:
        tag = f"cluster{ids[0]:02d}"
    elif ids == list(range(ids[0], ids[-1] + 1)):
        tag = f"clusters{ids[0]:02d}-{ids[-1]:02d}"
    else:
        tag = "clusters" + "-".join(f"{i:02d}" for i in ids[:6])
        if len(ids) > 6:
            tag += f"-and{len(ids) - 6}more"
    hemi = f"_{hemisphere[:1].upper()}" if hemisphere else ""
    return f"{stem}_{tag}{hemi}_mask"
