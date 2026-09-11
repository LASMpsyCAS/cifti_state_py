"""Result containers.

Each analysis produces one :class:`AnalysisResult`.  It carries the data a GUI
binds to (labels, tables, figures) *and* a complete snapshot of the parameters
that produced it, so ``to_json()`` is enough to reproduce a run and to paste
into a methods section.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .core.cluster import ClusterResult
from .core.threshold import ThresholdResult
from .logging_setup import get_logger
from .types import Direction, Statistic

log = get_logger(__name__)

__all__ = ["AnalysisSpec", "AnalysisResult"]


@dataclass
class AnalysisSpec:
    """The complete description of one analysis.

    This is the object a GUI binds its widgets to and the CLI reads from YAML.
    Both drive the same :func:`cifti_state.pipeline.run_analysis`, so anything
    the interface can do is scriptable and vice versa.
    """

    # input
    input_path: Optional[Path] = None
    input_left: Optional[Path] = None      # GIFTI pair alternative
    input_right: Optional[Path] = None
    column: int = 0
    statistic: Statistic = "z"
    df: Optional[float] = None
    fill: float = 0.0

    # thresholding
    threshold_method: str = "fixed"        # fixed | fdr | percentile
    threshold_value: Optional[float] = None
    fdr_q: float = 0.05
    fdr_legacy_tail: bool = True   # only values on the tested side enter the FDR
    percentile: float = 95.0
    direction: Direction = "positive"

    # clustering
    extent: int = 20
    mesh: str = "32k"
    neighbor_source: str = "txt"           # txt | surface
    inf_policy: str = "clip"               # clip | legacy | nan
    legacy_mode: bool = False

    # annotation
    atlas: str = "Glasser_2016"
    top_n_regions: int = 2
    min_region_percent: float = 0.0
    geometry_surface: str = "midthickness"

    # output
    output_dir: Optional[Path] = None
    output_prefix: Optional[str] = None
    report_style: str = "wide"             # wide | long | legacy
    report_formats: tuple[str, ...] = ("csv",)
    write_cluster_map: bool = True
    render: bool = False
    render_surface: Optional[str] = None
    render_layout: Optional[str] = None
    render_format: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _stringify(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisSpec":
        known = {f for f in cls.__dataclass_fields__}
        kwargs: dict[str, Any] = {}
        for key, value in (data or {}).items():
            if key not in known:
                log.warning("ignoring unknown spec key %r", key)
                continue
            if key in ("input_path", "input_left", "input_right", "output_dir"):
                value = Path(value) if value else None
            elif key == "report_formats" and value is not None:
                value = tuple(value)
            kwargs[key] = value
        return cls(**kwargs)

    def prefix(self) -> str:
        if self.output_prefix:
            return self.output_prefix
        if self.input_path:
            return Path(self.input_path).name.split(".")[0]
        if self.input_left:
            return Path(self.input_left).name.split(".")[0]
        return "analysis"


@dataclass
class AnalysisResult:
    """Everything one run produced."""

    spec: AnalysisSpec
    threshold: ThresholdResult
    clusters: ClusterResult
    peaks: pd.DataFrame
    annotations: pd.DataFrame
    report: pd.DataFrame
    input_description: dict[str, Any] = field(default_factory=dict)
    atlas_name: str = ""
    outputs: dict[str, Path] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def n_clusters(self) -> int:
        return self.clusters.n_clusters

    def summary(self) -> str:
        return (
            f"{self.input_description.get('name', '')}: "
            f"{self.n_clusters} clusters "
            f"(L {self.clusters.n_left} / R {self.clusters.n_right}) "
            f"at {self.threshold.describe()}, extent >= {self.clusters.params.extent}, "
            f"atlas {self.atlas_name}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "spec": self.spec.to_dict(),
            "input": _stringify(self.input_description),
            "threshold": self.threshold.to_dict(),
            "clusters": self.clusters.to_dict(),
            "atlas": self.atlas_name,
            "outputs": {k: str(v) for k, v in self.outputs.items()},
            "warnings": list(self.warnings),
        }

    def to_json(self, path: Optional[Path | str] = None, *, indent: int = 2) -> str:
        text = json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            log.info("wrote analysis record to %s", path)
        return text


def _stringify(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_stringify(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj
