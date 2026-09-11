"""End-to-end orchestration.

One function, :func:`run_analysis`, drives load -> threshold -> cluster ->
peaks -> annotate -> report -> (optionally) render and write.  The CLI and the
GUI both call it, so there is a single code path and no behaviour that exists
only in the interface.

It accepts ``progress`` and ``cancel`` and does all its own IO, so a GUI can run
it on a worker thread and keep the window responsive.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .config import ConfigError, Settings
from .core.annotate import annotate_clusters
from .core.cluster import find_clusters
from .core.peaks import cluster_peaks
from .core.report import build_report, save_report
from .core.threshold import compute_threshold
from .io.atlas import load_atlas, load_registry
from .io.cifti import (
    load_surface_stat_map,
    load_surface_stat_map_from_gifti,
    make_dense_surface_template,
    save_like,
)
from .io.neighbors import load_neighbors
from .io.surface import load_surface
from .logging_setup import get_logger
from .results import AnalysisResult, AnalysisSpec
from .types import CancelToken, ProgressFn, check_cancelled, report_progress

log = get_logger(__name__)

__all__ = ["run_analysis", "load_input", "build_adjacency", "save_cluster_mask"]


def load_input(spec: AnalysisSpec):
    """Load the statistic map described by *spec* (CIFTI or GIFTI pair)."""
    if spec.input_path:
        return load_surface_stat_map(
            spec.input_path,
            statistic=spec.statistic,
            df=spec.df,
            column=spec.column,
            fill=spec.fill,
        )
    if spec.input_left and spec.input_right:
        return load_surface_stat_map_from_gifti(
            spec.input_left,
            spec.input_right,
            statistic=spec.statistic,
            df=spec.df,
            column=spec.column,
        )
    raise ValueError("spec needs either input_path or input_left + input_right")


def build_adjacency(
    stat_map, settings: Settings, spec: AnalysisSpec
) -> dict[str, Any]:
    """Load the adjacency matrices matching the map's mesh size.

    ``neighbor_source="txt"`` asks for the MATLAB neighbour tables.  When none
    are configured -- a fresh checkout, or the bundled example data -- the graph
    is derived from the surface mesh instead rather than failing: the two were
    verified to be the identical graph on fs_LR 32k (0 differing entries), so
    this changes the result in no way, and refusing to run would only send the
    user hunting for a 2.6 MB file they do not need.
    """
    source = spec.neighbor_source
    if source == "txt":
        try:
            settings.resources.neighbor_path(spec.mesh, "left")
        except ConfigError:
            log.info(
                "no neighbour tables configured for mesh %r; deriving the "
                "adjacency from the surface mesh (same graph)", spec.mesh,
            )
            source = "surface"

    adjacency = {}
    for hemi in ("left", "right"):
        n_vertices = stat_map.hemi(hemi).n_vertices
        adj = load_neighbors(
            settings,
            hemi,
            mesh=spec.mesh,
            source=source,
            n_vertices=n_vertices,
        )
        if adj.shape[0] != n_vertices:
            raise ValueError(
                f"{hemi}: the {spec.neighbor_source} adjacency has {adj.shape[0]} "
                f"vertices but the map has {n_vertices}. Check defaults.mesh "
                f"(currently {spec.mesh!r}) or switch neighbor_source to 'surface'."
            )
        adjacency[hemi] = adj
    return adjacency


def save_cluster_mask(
    clusters,
    cluster_ids,
    out_path: Path | str,
    template,
    *,
    label_values: bool = False,
    map_name: Optional[str] = None,
) -> Path:
    """Write the selected cluster(s) as a binary mask ``dscalar.nii``.

    *template* is the :class:`~cifti_state.io.cifti.CiftiTemplate` of the map the
    clusters came from, so the mask lands in the same greyordinate layout (91k,
    59k or 64k) and can be used directly with the original data.  Pass
    ``label_values=True`` to keep each cluster's own id instead of writing 1s.
    """
    from .core.mask import clusters_mask, suggest_mask_name

    ids = [int(i) for i in (
        cluster_ids if isinstance(cluster_ids, (list, tuple, set)) else [cluster_ids]
    )]
    left, right = clusters_mask(clusters, ids, label_values=label_values)
    out_path = Path(out_path)
    name = map_name or suggest_mask_name(clusters.source_name or "clusters", ids)
    path = save_like(out_path, left, right, template, map_name=name)
    log.info(
        "wrote mask for cluster(s) %s: %d vertices -> %s",
        ids, int((left > 0).sum() + (right > 0).sum()), path,
    )
    return path


def run_analysis(
    spec: AnalysisSpec,
    settings: Settings,
    *,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> AnalysisResult:
    """Run the whole pipeline and (if ``spec.output_dir`` is set) write results."""
    started = time.perf_counter()
    warnings: list[str] = []

    # 1. input ------------------------------------------------------------- #
    report_progress(progress, 0.02, "loading input map")
    check_cancelled(cancel)
    stat_map = load_input(spec)
    description = stat_map.describe()

    # 2. adjacency ---------------------------------------------------------- #
    report_progress(progress, 0.10, "loading vertex adjacency")
    check_cancelled(cancel)
    adjacency = build_adjacency(stat_map, settings, spec)

    # 3. threshold ---------------------------------------------------------- #
    report_progress(progress, 0.18, "computing threshold")
    check_cancelled(cancel)
    threshold = compute_threshold(
        stat_map.finite_values(),
        method=spec.threshold_method,
        value=spec.threshold_value,
        q=spec.fdr_q,
        percentile=spec.percentile,
        direction=spec.direction,
        statistic=spec.statistic,
        df=spec.df,
        legacy=spec.fdr_legacy_tail,
    )
    if threshold.positive is None and threshold.negative is None:
        warnings.append(
            f"no threshold could be determined ({threshold.method}); no clusters possible"
        )

    # 4. clusters ----------------------------------------------------------- #
    check_cancelled(cancel)
    clusters = find_clusters(
        stat_map,
        adjacency,
        threshold,
        extent=spec.extent,
        direction=spec.direction,
        inf_policy=spec.inf_policy,
        legacy_mode=spec.legacy_mode,
        progress=_sub_progress(progress, 0.20, 0.50),
        cancel=cancel,
    )
    if clusters.n_clusters == 0:
        warnings.append("no cluster survived the extent threshold")

    # 5. peaks -------------------------------------------------------------- #
    report_progress(progress, 0.55, "measuring cluster peaks")
    check_cancelled(cancel)
    surfaces = _geometry_surfaces(settings, spec, stat_map, warnings)
    peaks = cluster_peaks(
        stat_map,
        clusters,
        surfaces=surfaces,
        progress=_sub_progress(progress, 0.55, 0.70),
        cancel=cancel,
    )

    # 6. annotation --------------------------------------------------------- #
    report_progress(progress, 0.72, "annotating against atlas")
    check_cancelled(cancel)
    atlas = load_atlas(spec.atlas, settings, registry=load_registry())
    _check_atlas_mesh(atlas, stat_map, warnings)
    annotations = annotate_clusters(
        clusters,
        atlas,
        peaks=peaks,
        top_n=spec.top_n_regions,
        min_percent=spec.min_region_percent,
        progress=_sub_progress(progress, 0.72, 0.85),
        cancel=cancel,
    )

    # 7. report ------------------------------------------------------------- #
    report_progress(progress, 0.88, "building report")
    metadata = {
        "input": description.get("source"),
        "layout": description.get("layout"),
        "statistic": spec.statistic,
        "df": spec.df,
        "threshold_method": threshold.method,
        "threshold_positive": threshold.positive,
        "threshold_negative": threshold.negative,
        "direction": spec.direction,
        "extent": spec.extent,
        "atlas": atlas.name,
        "neighbor_source": spec.neighbor_source,
        "legacy_mode": spec.legacy_mode,
    }
    report = build_report(
        peaks, annotations, style=spec.report_style, metadata=metadata
    )

    result = AnalysisResult(
        spec=spec,
        threshold=threshold,
        clusters=clusters,
        peaks=peaks,
        annotations=annotations,
        report=report,
        input_description=description,
        atlas_name=atlas.name,
        warnings=warnings,
    )

    # 8. outputs ------------------------------------------------------------ #
    if spec.output_dir:
        report_progress(progress, 0.92, "writing outputs")
        check_cancelled(cancel)
        _write_outputs(result, stat_map, settings, spec)

    result.duration_s = time.perf_counter() - started
    report_progress(progress, 1.0, result.summary())
    log.info("%s (%.2fs)", result.summary(), result.duration_s)
    for message in warnings:
        log.warning(message)
    return result


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _write_outputs(
    result: AnalysisResult, stat_map, settings: Settings, spec: AnalysisSpec
) -> None:
    out_dir = Path(spec.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = spec.prefix()

    thr = result.threshold.positive if result.threshold.positive is not None else 0.0
    stem = f"{prefix}_cluster_extent{spec.extent}_thr{_fmt(thr)}"

    if spec.write_cluster_map:
        template = stat_map.template or make_dense_surface_template(
            stat_map.left.n_vertices, stat_map.right.n_vertices
        )
        path = save_like(
            out_dir / f"{stem}.dscalar.nii",
            result.clusters.labels_left.astype(float),
            result.clusters.labels_right.astype(float),
            template,
            map_name=stem,
        )
        result.outputs["cluster_map"] = path

    for fmt in spec.report_formats:
        path = save_report(
            result.report,
            out_dir / f"{stem}_report.{fmt}",
            fmt=fmt,
            metadata=result.report.attrs.get("metadata"),
        )
        result.outputs[f"report_{fmt}"] = path

    result.outputs["peaks_csv"] = save_report(
        result.peaks, out_dir / f"{stem}_peaks.csv", fmt="csv"
    )
    result.outputs["annotations_csv"] = save_report(
        result.annotations, out_dir / f"{stem}_regions.csv", fmt="csv"
    )

    if spec.render:
        try:
            from .viz.render import render_stat_map, save_figure

            figure = render_stat_map(
                stat_map,
                settings,
                clusters=result.clusters,
                surface=spec.render_surface or settings.render.surface,
                layout=spec.render_layout or settings.render.layout,
            )
            fmt = spec.render_format or settings.render.format
            result.outputs["figure"] = save_figure(
                figure, out_dir / f"{stem}.{fmt}", settings=settings
            )
        except Exception as exc:  # rendering must never lose the tables
            message = f"rendering failed: {exc}"
            log.warning(message)
            result.warnings.append(message)

    record_path = out_dir / f"{stem}_analysis.json"
    result.to_json(record_path)
    result.outputs["record"] = record_path


def _geometry_surfaces(
    settings: Settings, spec: AnalysisSpec, stat_map, warnings: list[str]
):
    """Load the surfaces used for areas and coordinates, tolerantly."""
    try:
        surfaces = {
            hemi: load_surface(
                settings.resources.surface_path(hemi, spec.geometry_surface),
                hemisphere=hemi,
                kind=spec.geometry_surface,
            )
            for hemi in ("left", "right")
        }
    except Exception as exc:
        message = (
            f"geometry surfaces unavailable ({exc}); cluster areas and "
            f"coordinates will be blank"
        )
        log.warning(message)
        warnings.append(message)
        return None

    for hemi in ("left", "right"):
        if surfaces[hemi].n_vertices != stat_map.hemi(hemi).n_vertices:
            message = (
                f"{hemi} surface has {surfaces[hemi].n_vertices} vertices but the "
                f"map has {stat_map.hemi(hemi).n_vertices}; skipping geometry"
            )
            log.warning(message)
            warnings.append(message)
            return None
    return surfaces


def _check_atlas_mesh(atlas, stat_map, warnings: list[str]) -> None:
    for hemi in ("left", "right"):
        n_atlas = atlas.hemi(hemi).n_vertices
        n_map = stat_map.hemi(hemi).n_vertices
        if n_atlas != n_map:
            raise ValueError(
                f"atlas {atlas.name!r} {hemi} has {n_atlas} vertices but the map "
                f"has {n_map}. The atlas and the data must be on the same mesh."
            )


def _sub_progress(
    progress: Optional[ProgressFn], start: float, end: float
) -> Optional[ProgressFn]:
    """Map a 0..1 child progress range onto a slice of the parent range."""
    if progress is None:
        return None

    def inner(fraction: float, message: str) -> None:
        report_progress(progress, start + (end - start) * fraction, message)

    return inner


def _fmt(value: float) -> str:
    text = f"{float(value):.6g}"
    return text.rstrip("0").rstrip(".") if "." in text else text
