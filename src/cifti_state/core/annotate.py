"""Anatomical annotation of clusters against an atlas.

Replaces ``w_find_brain_region.m``.  Fixed along the way:

* the AAL branch loaded ``data_aal_L.label.gii`` for *both* hemispheres, so
  every right-hemisphere AAL label was wrong.  Each hemisphere now reads its
  own file (see :mod:`cifti_state.io.atlas`).
* ``label_index_name`` was never cleared between clusters, so a cluster that
  matched only one region silently inherited the previous cluster's second
  region.
* the hard-wired ``+1``/``+36``/``+max(L)`` label offsets are gone; regions are
  keyed by ``(hemisphere, label_id)`` against each hemisphere's own label table.
* the number of reported regions was fixed at two, with a one-region cluster
  padded by duplicating its row.  ``top_n`` is a parameter and nothing is
  duplicated.

Percentages match the MATLAB convention: they are shares of *all* vertices in
the cluster, so unlabelled (medial wall / background) vertices are in the
denominator.  ``percent_of_labelled`` is provided alongside for the share among
labelled vertices only.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..io.atlas import Atlas
from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from .cluster import ClusterResult

log = get_logger(__name__)

__all__ = ["annotate_clusters", "ANNOTATION_COLUMNS", "UNLABELLED"]

UNLABELLED = "Medial Wall / unlabelled"

ANNOTATION_COLUMNS = [
    "cluster_id",
    "hemisphere",
    "rank",
    "region",
    "label_id",
    "n_vertices",
    "percent",
    "percent_of_labelled",
    "peak_region",
    "peak_label_id",
]


def annotate_clusters(
    clusters: ClusterResult,
    atlas: Atlas,
    *,
    peaks: Optional[pd.DataFrame] = None,
    top_n: int = 2,
    min_percent: float = 0.0,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> pd.DataFrame:
    """Long-format table: one row per (cluster, contributing region).

    Parameters
    ----------
    peaks
        Optional output of :func:`cifti_state.core.peaks.cluster_peaks`; when
        given, the region containing each cluster's peak vertex is added.
    top_n
        Number of regions to report per cluster, ordered by share.  ``0`` or a
        negative value reports every contributing region.
    min_percent
        Drop regions contributing less than this share of the cluster.
    """
    peak_vertex_by_cluster: dict[int, int] = {}
    if peaks is not None and len(peaks):
        peak_vertex_by_cluster = dict(
            zip(peaks["cluster_id"].astype(int), peaks["peak_vertex"].astype(int))
        )

    rows = []
    total = max(clusters.n_clusters, 1)
    for step, info in enumerate(clusters.clusters, start=1):
        check_cancelled(cancel)
        report_progress(progress, step / total, f"annotating cluster {info.cluster_id}")

        hemi = info.hemisphere
        atlas_hemi = atlas.hemi(hemi)
        cluster_labels = clusters.labels(hemi)
        if atlas_hemi.n_vertices != cluster_labels.size:
            raise ValueError(
                f"atlas {atlas.name!r} {hemi} has {atlas_hemi.n_vertices} vertices "
                f"but the cluster map has {cluster_labels.size}; mesh mismatch"
            )

        vertices = np.flatnonzero(cluster_labels == info.cluster_id)
        region_ids = atlas_hemi.labels[vertices]
        total_vertices = int(vertices.size)

        ids, counts = np.unique(region_ids, return_counts=True)
        keep = np.array(
            [not atlas.is_unknown(hemi, int(i)) for i in ids], dtype=bool
        )
        ids, counts = ids[keep], counts[keep]
        n_labelled = int(counts.sum())

        order = np.argsort(-counts, kind="stable")
        ids, counts = ids[order], counts[order]

        peak_region = ""
        peak_label_id = -1
        vertex = peak_vertex_by_cluster.get(info.cluster_id)
        if vertex is not None:
            peak_label_id = int(atlas_hemi.labels[int(vertex)])
            peak_region = (
                UNLABELLED
                if atlas.is_unknown(hemi, peak_label_id)
                else atlas.region_name(hemi, peak_label_id)
            )

        if ids.size == 0:
            rows.append(
                {
                    "cluster_id": info.cluster_id,
                    "hemisphere": hemi,
                    "rank": 1,
                    "region": UNLABELLED,
                    "label_id": 0,
                    "n_vertices": total_vertices,
                    "percent": 100.0,
                    "percent_of_labelled": np.nan,
                    "peak_region": peak_region or UNLABELLED,
                    "peak_label_id": peak_label_id,
                }
            )
            continue

        limit = ids.size if top_n is None or top_n <= 0 else min(top_n, ids.size)
        rank = 0
        for label_id, count in zip(ids[:limit], counts[:limit]):
            percent = 100.0 * count / total_vertices if total_vertices else np.nan
            if percent < min_percent:
                continue
            rank += 1
            rows.append(
                {
                    "cluster_id": info.cluster_id,
                    "hemisphere": hemi,
                    "rank": rank,
                    "region": atlas.region_name(hemi, int(label_id)),
                    "label_id": int(label_id),
                    "n_vertices": int(count),
                    "percent": float(percent),
                    "percent_of_labelled": (
                        float(100.0 * count / n_labelled) if n_labelled else np.nan
                    ),
                    "peak_region": peak_region,
                    "peak_label_id": peak_label_id,
                }
            )

    table = pd.DataFrame(rows, columns=ANNOTATION_COLUMNS)
    report_progress(progress, 1.0, f"annotated {clusters.n_clusters} clusters")
    return table
