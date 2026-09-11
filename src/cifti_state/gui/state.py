"""The session state the interface binds to.

One object holds everything the current analysis has produced.  Panels read
from it and emit signals; they never talk to each other directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ..config import Settings
from ..core.cluster import ClusterResult
from ..core.threshold import ThresholdResult
from ..io.atlas import Atlas
from ..io.cifti import SurfaceStatMap
from ..io.surface import Surface
from ..results import AnalysisSpec

__all__ = ["SessionState"]


@dataclass
class SessionState:
    """Everything the window currently knows about."""

    settings: Settings
    spec: AnalysisSpec = field(default_factory=AnalysisSpec)

    stat_map: Optional[SurfaceStatMap] = None
    adjacency: Optional[dict[str, Any]] = None
    surfaces: Optional[dict[str, Surface]] = None

    threshold: Optional[ThresholdResult] = None
    clusters: Optional[ClusterResult] = None
    peaks: Optional[pd.DataFrame] = None

    atlas: Optional[Atlas] = None
    annotations: Optional[pd.DataFrame] = None
    report: Optional[pd.DataFrame] = None

    #: Where the cluster map was last written (needed to open it in wb_view).
    cluster_map_path: Optional[Path] = None
    #: Directory the user last saved something to, used as the dialog default.
    last_output_dir: Optional[Path] = None

    # -- readiness ---------------------------------------------------------- #

    @property
    def has_map(self) -> bool:
        return self.stat_map is not None

    @property
    def has_clusters(self) -> bool:
        return self.clusters is not None and self.clusters.n_clusters > 0

    @property
    def has_report(self) -> bool:
        return self.report is not None and len(self.report) > 0

    # -- mutation ----------------------------------------------------------- #

    def reset_map(self) -> None:
        self.stat_map = None
        self.adjacency = None
        self.reset_clusters()

    def reset_clusters(self) -> None:
        self.threshold = None
        self.clusters = None
        self.peaks = None
        self.cluster_map_path = None
        self.reset_report()

    def reset_report(self) -> None:
        self.annotations = None
        self.report = None

    # -- description -------------------------------------------------------- #

    def map_facts(self) -> list[tuple[str, str]]:
        if self.stat_map is None:
            return []
        described = self.stat_map.describe()
        return [
            ("file", Path(described["source"]).name if described["source"] else "—"),
            ("layout", described["layout"]),
            (
                "vertices",
                f"L {described['n_present_left']}/{described['n_vertices_left']}   "
                f"R {described['n_present_right']}/{described['n_vertices_right']}",
            ),
            (
                "medial wall",
                "excluded by file" if described["medial_wall_excluded"] else "included",
            ),
            (
                "values",
                f"{described['n_finite']} finite, "
                f"{described['min']:.4g} … {described['max']:.4g}"
                if described["n_finite"] else "none",
            ),
            (
                "statistic",
                described["statistic"]
                + (f" (df={described['df']:g})" if described["df"] else ""),
            ),
        ]

    def cluster_facts(self) -> list[tuple[str, str]]:
        if self.clusters is None or self.threshold is None:
            return []
        params = self.clusters.params
        pieces = []
        if self.threshold.positive is not None:
            pieces.append(f"> {self.threshold.positive:.4g}")
        if self.threshold.negative is not None:
            pieces.append(f"< {self.threshold.negative:.4g}")
        return [
            ("threshold", f"{self.threshold.method}  {'  /  '.join(pieces) or 'none'}"),
            ("suprathreshold", f"{self.threshold.n_suprathreshold} vertices"),
            ("extent", f"≥ {params.extent} vertices"),
            ("direction", params.direction),
            (
                "clusters",
                f"{self.clusters.n_clusters}  "
                f"(L {self.clusters.n_left} / R {self.clusters.n_right})",
            ),
        ]
