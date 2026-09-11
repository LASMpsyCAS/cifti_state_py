"""Core analysis: thresholding, clustering, peaks, annotation, reporting.

Everything in this sub-package is a pure function over NumPy arrays and small
dataclasses.  No module here reads or writes files, reads configuration, or
prints -- which is what makes it safe to call from a GUI worker thread and easy
to unit test.
"""

from .annotate import ANNOTATION_COLUMNS, UNLABELLED, annotate_clusters
from .cluster import ClusterInfo, ClusterParams, ClusterResult, find_clusters, sanitise
from .mask import cluster_mask, clusters_mask, mask_summary, suggest_mask_name
from .peaks import PEAK_COLUMNS, cluster_peaks, find_local_peaks
from .report import build_report, format_coordinates, save_report
from .threshold import (
    ThresholdResult,
    benjamini_hochberg,
    compute_threshold,
    fdr_threshold,
    suprathreshold_mask,
    to_pvalues,
)

__all__ = [
    "ANNOTATION_COLUMNS",
    "ClusterInfo",
    "ClusterParams",
    "ClusterResult",
    "PEAK_COLUMNS",
    "ThresholdResult",
    "UNLABELLED",
    "annotate_clusters",
    "benjamini_hochberg",
    "build_report",
    "cluster_mask",
    "cluster_peaks",
    "clusters_mask",
    "compute_threshold",
    "fdr_threshold",
    "find_clusters",
    "find_local_peaks",
    "format_coordinates",
    "mask_summary",
    "sanitise",
    "save_report",
    "suggest_mask_name",
    "suprathreshold_mask",
    "to_pvalues",
]
