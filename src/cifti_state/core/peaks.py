"""Per-cluster peak vertices and summary statistics.

Replaces ``w_find_peak_incluster.m``, with three corrections:

* the MATLAB field named ``zstat`` actually held the cluster *mean*.  Both
  ``peak_value`` and ``mean_value`` are reported here, under honest names.
* ``find(zdata_clustered == max(...))`` returns *every* tied vertex; the first
  is taken here, deterministically (lowest vertex index).
* for a negative cluster the "peak" is the most negative vertex, not the most
  positive one.

Surface area, centroid and peak coordinates are new: they need a surface mesh,
which is optional.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..io.cifti import SurfaceStatMap
from ..io.surface import Surface, vertex_areas
from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from .cluster import ClusterResult

log = get_logger(__name__)

__all__ = ["cluster_peaks", "find_local_peaks", "PEAK_COLUMNS"]

PEAK_COLUMNS = [
    "cluster_id",
    "hemisphere",
    "sign",
    "size_vertices",
    "size_mm2",
    "peak_value",
    "peak_vertex",
    "mean_value",
    "sd_value",
    "min_value",
    "max_value",
    "peak_x",
    "peak_y",
    "peak_z",
    "centroid_x",
    "centroid_y",
    "centroid_z",
]


def cluster_peaks(
    stat_map: SurfaceStatMap,
    clusters: ClusterResult,
    *,
    surfaces: Optional[dict[str, Surface]] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> pd.DataFrame:
    """One row per cluster: peak, extent and (if surfaces are given) geometry.

    Parameters
    ----------
    surfaces
        ``{"left": Surface, "right": Surface}``.  Pass the *midthickness*
        surface for anatomically meaningful areas and coordinates; an inflated
        surface would give inflated areas.  Omit to skip the geometry columns.
    """
    areas: dict[str, np.ndarray] = {}
    coords: dict[str, np.ndarray] = {}
    if surfaces:
        for hemi, surface in surfaces.items():
            areas[hemi] = vertex_areas(surface.coords, surface.faces)
            coords[hemi] = np.asarray(surface.coords, dtype=float)

    rows = []
    total = max(clusters.n_clusters, 1)
    for step, info in enumerate(clusters.clusters, start=1):
        check_cancelled(cancel)
        report_progress(progress, step / total, f"cluster {info.cluster_id}")

        hemi = info.hemisphere
        vertices = np.flatnonzero(clusters.labels(hemi) == info.cluster_id)
        values = stat_map.hemi(hemi).values[vertices]
        finite = np.isfinite(values)
        usable = values[finite] if finite.any() else values

        if info.sign > 0:
            local = int(np.argmax(np.where(finite, values, -np.inf)))
        else:
            local = int(np.argmin(np.where(finite, values, np.inf)))
        peak_vertex = int(vertices[local])
        peak_value = float(values[local])

        row: dict[str, object] = {
            "cluster_id": info.cluster_id,
            "hemisphere": hemi,
            "sign": info.sign,
            "size_vertices": info.size_vertices,
            "size_mm2": (
                float(areas[hemi][vertices].sum()) if hemi in areas else np.nan
            ),
            "peak_value": peak_value,
            "peak_vertex": peak_vertex,
            "mean_value": float(np.mean(usable)) if usable.size else np.nan,
            "sd_value": float(np.std(usable, ddof=1)) if usable.size > 1 else np.nan,
            "min_value": float(np.min(usable)) if usable.size else np.nan,
            "max_value": float(np.max(usable)) if usable.size else np.nan,
        }

        if hemi in coords:
            xyz = coords[hemi]
            row["peak_x"], row["peak_y"], row["peak_z"] = (
                float(v) for v in xyz[peak_vertex]
            )
            weights = np.abs(values)
            weights = np.where(np.isfinite(weights), weights, 0.0)
            if weights.sum() > 0:
                centroid = np.average(xyz[vertices], axis=0, weights=weights)
            else:
                centroid = xyz[vertices].mean(axis=0)
            row["centroid_x"], row["centroid_y"], row["centroid_z"] = (
                float(v) for v in centroid
            )
        else:
            for key in ("peak_x", "peak_y", "peak_z",
                        "centroid_x", "centroid_y", "centroid_z"):
                row[key] = np.nan

        rows.append(row)

    table = pd.DataFrame(rows, columns=PEAK_COLUMNS)
    report_progress(progress, 1.0, f"{len(table)} cluster peaks")
    return table


def find_local_peaks(
    stat_map: SurfaceStatMap,
    clusters: ClusterResult,
    neighbors: dict[str, list[np.ndarray]],
    *,
    surfaces: Optional[dict[str, Surface]] = None,
    min_distance_mm: float = 20.0,
    max_per_cluster: int = 3,
) -> pd.DataFrame:
    """Sub-peaks inside each cluster (local maxima, distance-pruned).

    A vertex is a local maximum when no neighbour is more extreme in the
    cluster's direction.  Peaks are then taken in order of magnitude, skipping
    any that lie within *min_distance_mm* (Euclidean, on the given surface) of
    an already accepted peak.
    """
    coords = (
        {h: np.asarray(s.coords, float) for h, s in surfaces.items()}
        if surfaces
        else {}
    )
    rows = []
    for info in clusters.clusters:
        hemi = info.hemisphere
        labels = clusters.labels(hemi)
        values = stat_map.hemi(hemi).values
        vertices = np.flatnonzero(labels == info.cluster_id)
        signed = values * info.sign

        candidates = []
        for vertex in vertices:
            neighbours = neighbors[hemi][vertex]
            inside = neighbours[labels[neighbours] == info.cluster_id]
            if inside.size == 0 or np.all(signed[vertex] >= signed[inside]):
                candidates.append(vertex)
        candidates = np.array(candidates, dtype=int)
        if candidates.size == 0:
            continue
        candidates = candidates[np.argsort(-signed[candidates], kind="stable")]

        accepted: list[int] = []
        for vertex in candidates:
            if len(accepted) >= max_per_cluster:
                break
            if hemi in coords and accepted:
                d = np.linalg.norm(coords[hemi][accepted] - coords[hemi][vertex], axis=1)
                if float(d.min()) < min_distance_mm:
                    continue
            accepted.append(int(vertex))

        for rank, vertex in enumerate(accepted, start=1):
            row = {
                "cluster_id": info.cluster_id,
                "hemisphere": hemi,
                "sign": info.sign,
                "peak_rank": rank,
                "peak_vertex": int(vertex),
                "peak_value": float(values[vertex]),
            }
            if hemi in coords:
                row["peak_x"], row["peak_y"], row["peak_z"] = (
                    float(v) for v in coords[hemi][vertex]
                )
            rows.append(row)

    return pd.DataFrame(rows)
