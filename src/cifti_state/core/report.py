"""Assemble the cluster report table and write it out.

Three layouts:

``wide`` (default)
    One row per cluster.  Contributing regions are collapsed into a single
    ``regions`` column (``"Region A (61.2%); Region B (28.4%)"``), which is what
    a manuscript table usually wants.
``long``
    One row per (cluster, region) -- the shape ``w_find_brain_region.m``
    returned, but without the duplicated padding row.
``legacy``
    ``long`` restricted to the original column set and names, for diffing
    against old MATLAB output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = ["build_report", "save_report", "format_coordinates", "WIDE_COLUMN_ORDER"]

_HEMI_SHORT = {"left": "L", "right": "R"}

#: Column order for the wide report: what identifies a cluster and where it is,
#: then how big and how strong, then the secondary measurements.  Anything not
#: listed keeps its relative position at the end, so extra columns never vanish.
WIDE_COLUMN_ORDER = (
    "cluster_id",
    "hemi",
    "direction",
    "peak_region",
    "primary_region",
    "primary_region_percent",
    "regions",
    "size_vertices",
    "size_mm2",
    "peak_value",
    "peak_x",
    "peak_y",
    "peak_z",
    "mean_value",
    "sd_value",
    "min_value",
    "max_value",
    "peak_vertex",
    "centroid_x",
    "centroid_y",
    "centroid_z",
)


def build_report(
    peaks: pd.DataFrame,
    annotations: Optional[pd.DataFrame] = None,
    *,
    style: str = "wide",
    decimals: int = 3,
    coordinate_decimals: int = 0,
    metadata: Optional[dict[str, Any]] = None,
) -> pd.DataFrame:
    """Combine the peak table and the annotation table into one report."""
    peaks = peaks.copy()
    if annotations is None:
        annotations = pd.DataFrame(columns=["cluster_id", "rank", "region", "percent"])

    if style == "long":
        table = _long(peaks, annotations)
    elif style == "legacy":
        table = _legacy(peaks, annotations)
    elif style == "wide":
        table = _wide(peaks, annotations)
    else:
        raise ValueError(f"unknown report style {style!r}; use wide, long or legacy")

    table = _round(table, decimals=decimals, coordinate_decimals=coordinate_decimals)

    if metadata:
        table.attrs["metadata"] = dict(metadata)
    return table


def save_report(
    table: pd.DataFrame,
    path: Path | str,
    *,
    fmt: Optional[str] = None,
    sheet_name: str = "clusters",
    metadata: Optional[dict[str, Any]] = None,
) -> Path:
    """Write the report. Format is taken from the suffix unless *fmt* is given."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or path.suffix.lstrip(".")).lower()

    if fmt in ("csv", ""):
        table.to_csv(path, index=False, encoding="utf-8-sig")
    elif fmt in ("tsv", "txt"):
        table.to_csv(path, index=False, sep="\t", encoding="utf-8-sig")
    elif fmt in ("xlsx", "xls"):
        meta = metadata or table.attrs.get("metadata")
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            table.to_excel(writer, sheet_name=sheet_name, index=False)
            if meta:
                pd.DataFrame(
                    {"key": list(meta), "value": [str(v) for v in meta.values()]}
                ).to_excel(writer, sheet_name="parameters", index=False)
    elif fmt == "md":
        path.write_text(table.to_markdown(index=False), encoding="utf-8")
    elif fmt == "json":
        table.to_json(path, orient="records", indent=2, force_ascii=False)
    else:
        raise ValueError(f"unsupported report format {fmt!r}")

    log.info("wrote report to %s (%d rows)", path, len(table))
    return path


def format_coordinates(
    table: pd.DataFrame,
    *,
    prefix: str = "peak",
    column: str = "peak_xyz",
    decimals: int = 0,
) -> pd.DataFrame:
    """Collapse ``<prefix>_x/y/z`` into a single ``"x, y, z"`` string column."""
    cols = [f"{prefix}_{axis}" for axis in "xyz"]
    if not all(c in table.columns for c in cols):
        return table
    out = table.copy()
    fmt = f"{{:.{decimals}f}}"
    out[column] = [
        ""
        if any(pd.isna(row[c]) for c in cols)
        else ", ".join(fmt.format(float(row[c])) for c in cols)
        for _, row in out.iterrows()
    ]
    return out


# --------------------------------------------------------------------------- #
# layouts
# --------------------------------------------------------------------------- #


def _wide(peaks: pd.DataFrame, annotations: pd.DataFrame) -> pd.DataFrame:
    table = peaks.copy()

    if len(annotations):
        grouped = (
            annotations.sort_values(["cluster_id", "rank"])
            .groupby("cluster_id")
            .apply(
                lambda g: "; ".join(
                    f"{r.region} ({r.percent:.1f}%)" for r in g.itertuples()
                ),
                include_groups=False,
            )
            .rename("regions")
        )
        table = table.merge(grouped, left_on="cluster_id", right_index=True, how="left")

        primary = (
            annotations[annotations["rank"] == 1]
            .set_index("cluster_id")[["region", "percent"]]
            .rename(columns={"region": "primary_region",
                             "percent": "primary_region_percent"})
        )
        table = table.merge(primary, left_on="cluster_id", right_index=True, how="left")

        peak_region = (
            annotations.drop_duplicates("cluster_id")
            .set_index("cluster_id")[["peak_region"]]
        )
        table = table.merge(
            peak_region, left_on="cluster_id", right_index=True, how="left"
        )

    # 'hemi' replaces 'hemisphere' and 'direction' replaces 'sign' -- keeping
    # both of each pair would just be the same fact twice in the table.
    table.insert(
        1, "hemi", table["hemisphere"].map(_HEMI_SHORT).fillna(table["hemisphere"])
    )
    table.insert(2, "direction", np.where(table["sign"] > 0, "positive", "negative"))
    table = table.drop(
        columns=[c for c in ("hemisphere", "sign") if c in table.columns]
    )
    return _order_columns(table, WIDE_COLUMN_ORDER)


def _long(peaks: pd.DataFrame, annotations: pd.DataFrame) -> pd.DataFrame:
    if not len(annotations):
        return _wide(peaks, annotations)
    peak_cols = [
        c
        for c in (
            "cluster_id", "hemisphere", "sign", "size_vertices", "size_mm2",
            "peak_value", "peak_vertex", "mean_value",
            "peak_x", "peak_y", "peak_z",
        )
        if c in peaks.columns
    ]
    table = annotations.merge(peaks[peak_cols], on="cluster_id", how="left",
                              suffixes=("", "_peak"))
    if "hemisphere_peak" in table.columns:
        table = table.drop(columns=["hemisphere_peak"])
    front = ["cluster_id", "hemisphere", "rank", "region", "percent"]
    rest = [c for c in table.columns if c not in front]
    return table[front + rest]


def _legacy(peaks: pd.DataFrame, annotations: pd.DataFrame) -> pd.DataFrame:
    """The columns ``w_find_brain_region.m`` produced, with MATLAB's names."""
    table = _long(peaks, annotations)
    mapping = {
        "cluster_id": "cluster_id",
        "peak_region": "peak_region",
        "percent": "percent",
        "region": "name",
    }
    available = {k: v for k, v in mapping.items() if k in table.columns}
    out = table[list(available)].rename(columns=available)
    # MATLAB's peak table called the cluster mean "zstat".
    if "mean_value" in table.columns:
        out.insert(1, "zstat", table["mean_value"].to_numpy())
    if "size_vertices" in table.columns:
        out.insert(2, "cluster_size", table["size_vertices"].to_numpy())
    return out


def _order_columns(table: pd.DataFrame, order: tuple[str, ...]) -> pd.DataFrame:
    """Put the named columns first, in that order; keep the rest after them."""
    known = [c for c in order if c in table.columns]
    rest = [c for c in table.columns if c not in known]
    return table[known + rest]


def _round(
    table: pd.DataFrame, *, decimals: int, coordinate_decimals: int
) -> pd.DataFrame:
    out = table.copy()
    coordinate_cols = {
        c for c in out.columns
        if c.endswith(("_x", "_y", "_z")) or c in ("size_mm2",)
    }
    for column in out.columns:
        if not pd.api.types.is_float_dtype(out[column]):
            continue
        digits = coordinate_decimals if column in coordinate_cols else decimals
        out[column] = out[column].round(digits)
    return out
