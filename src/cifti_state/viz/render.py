"""Surface rendering with surfplot.

One renderer, two sinks: :func:`render_stat_map` returns a matplotlib
``Figure``.  Save it with :func:`save_figure` for a PNG/PDF, or embed the same
object in a Qt window with ``FigureCanvasQTAgg`` -- what the interface shows and
what gets exported come from the identical code path.

surfplot (and the VTK underneath it) needs a rendering context.  On a desktop
that is automatic; on a headless Linux box run under ``xvfb-run -a`` or install
a VTK built with OSMesa/EGL.  :func:`check_backend` reports what is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ..config import Settings
from ..core.cluster import ClusterResult
from ..io.cifti import SurfaceStatMap
from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from . import colormaps as cmaps
from .layouts import Layout, get_layout

log = get_logger(__name__)

__all__ = [
    "render_stat_map",
    "render_clusters",
    "render_atlas",
    "save_figure",
    "check_backend",
    "RenderError",
]


class RenderError(RuntimeError):
    """Rendering could not be completed (missing dependency or no display)."""


def _require_surfplot():
    try:
        from surfplot import Plot  # noqa: F401
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RenderError(
            "surfplot is not installed. Install the visualisation extra:\n"
            "    pip install 'cifti-state[viz]'"
        ) from exc
    from surfplot import Plot

    return Plot


def check_backend() -> dict[str, Any]:
    """Report whether rendering is likely to work here."""
    info: dict[str, Any] = {"surfplot": False, "vtk": None, "offscreen": None}
    try:
        import surfplot  # noqa: F401

        info["surfplot"] = True
    except ImportError:
        return info
    try:
        import vtk

        info["vtk"] = vtk.vtkVersion.GetVTKVersion()
        window = vtk.vtkRenderWindow()
        window.SetOffScreenRendering(1)
        window.SetSize(10, 10)
        window.Render()
        info["offscreen"] = True
    except Exception as exc:  # pragma: no cover - environment dependent
        info["offscreen"] = False
        info["error"] = str(exc)
    return info


# --------------------------------------------------------------------------- #
# public rendering entry points
# --------------------------------------------------------------------------- #


def render_stat_map(
    stat_map: SurfaceStatMap,
    settings: Settings,
    *,
    clusters: Optional[ClusterResult] = None,
    mask_to_clusters: bool = False,
    outline_clusters: bool = True,
    surface: Optional[str] = None,
    layout: Optional[str | Layout] = None,
    colormap: Optional[str] = None,
    color_range: Optional[tuple[float, float]] = None,
    symmetric: Optional[bool] = None,
    colorbar: bool = True,
    colorbar_label: Optional[str] = None,
    title: Optional[str] = None,
    outline_color: str = "#1a1a1a",
    background: tuple[float, float, float] = (1.0, 1.0, 1.0),
    underlay: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
):
    """Render a statistic map, optionally with cluster outlines drawn on top.

    Parameters
    ----------
    mask_to_clusters
        Show statistic values only inside surviving clusters.
    outline_clusters
        Draw cluster borders as a second layer.
    color_range
        ``(vmin, vmax)``.  Defaults to a robust symmetric range for two-sided
        data and to ``(threshold, max)`` style positive range otherwise.
    """
    Plot = _require_surfplot()
    check_cancelled(cancel)
    report_progress(progress, 0.1, "loading surfaces")

    layout_obj = _as_layout(layout or settings.render.layout)
    surface_kind = surface or settings.render.surface
    surf_paths = _surface_paths(settings, surface_kind, layout_obj)

    left = np.array(stat_map.left.values, dtype=float, copy=True)
    right = np.array(stat_map.right.values, dtype=float, copy=True)
    left[~np.isfinite(left)] = 0.0
    right[~np.isfinite(right)] = 0.0

    if clusters is not None and mask_to_clusters:
        left = np.where(clusters.labels_left > 0, left, 0.0)
        right = np.where(clusters.labels_right > 0, right, 0.0)

    data = _stack(left, right, layout_obj)

    if symmetric is None:
        symmetric = bool(np.any(data < 0))
    if color_range is None:
        color_range = (
            cmaps.symmetric_range(data)
            if symmetric
            else _positive_range(data)
        )

    cmap_name = colormap or (
        "workbench_hot_cool" if symmetric else settings.render.colormap
    )

    report_progress(progress, 0.35, "building scene")
    check_cancelled(cancel)
    plot = Plot(
        surf_lh=surf_paths.get("left"),
        surf_rh=surf_paths.get("right"),
        layout=layout_obj.layout_style,
        views=list(layout_obj.views),
        mirror_views=layout_obj.mirror_views,
        flip=layout_obj.flip,
        size=layout_obj.size or settings.render.size,
        zoom=layout_obj.zoom or settings.render.zoom,
        background=background,
    )
    _add_underlay(plot, settings, layout_obj, underlay)
    plot.add_layer(
        data,
        cmap=cmaps.get_colormap(cmap_name),
        color_range=color_range,
        cbar=colorbar,
        cbar_label=colorbar_label,
        zero_transparent=True,
    )

    if clusters is not None and outline_clusters:
        outline = _stack(
            (clusters.labels_left > 0).astype(float),
            (clusters.labels_right > 0).astype(float),
            layout_obj,
        )
        plot.add_layer(
            outline,
            cmap=cmaps.solid_colormap(outline_color),
            color_range=(0, 1),
            as_outline=True,
            cbar=False,
        )

    report_progress(progress, 0.6, "rendering")
    check_cancelled(cancel)
    figure = _build(plot, settings, colorbar=colorbar, title=title)
    report_progress(progress, 1.0, "rendered")
    return figure


def render_clusters(
    clusters: ClusterResult,
    settings: Settings,
    *,
    surface: Optional[str] = None,
    layout: Optional[str | Layout] = None,
    outline: bool = False,
    colorbar: bool = False,
    title: Optional[str] = None,
    underlay: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
):
    """Render the cluster label map with one colour per cluster."""
    Plot = _require_surfplot()
    check_cancelled(cancel)

    layout_obj = _as_layout(layout or settings.render.layout)
    surf_paths = _surface_paths(settings, surface or settings.render.surface, layout_obj)

    data = _stack(
        clusters.labels_left.astype(float),
        clusters.labels_right.astype(float),
        layout_obj,
    )
    n = clusters.n_clusters

    plot = Plot(
        surf_lh=surf_paths.get("left"),
        surf_rh=surf_paths.get("right"),
        layout=layout_obj.layout_style,
        views=list(layout_obj.views),
        mirror_views=layout_obj.mirror_views,
        flip=layout_obj.flip,
        size=layout_obj.size or settings.render.size,
        zoom=layout_obj.zoom or settings.render.zoom,
    )
    _add_underlay(plot, settings, layout_obj, underlay)
    plot.add_layer(
        data,
        cmap=cmaps.cluster_colormap(n),
        color_range=(0, max(n, 1)),
        as_outline=outline,
        cbar=colorbar,
        zero_transparent=True,
    )
    report_progress(progress, 0.6, "rendering clusters")
    figure = _build(plot, settings, colorbar=colorbar, title=title)
    report_progress(progress, 1.0, f"rendered {n} clusters")
    return figure


def render_atlas(
    atlas,
    settings: Settings,
    *,
    surface: Optional[str] = None,
    layout: Optional[str | Layout] = None,
    outline: bool = True,
    title: Optional[str] = None,
):
    """Render an atlas parcellation, by default as boundaries."""
    Plot = _require_surfplot()
    layout_obj = _as_layout(layout or settings.render.layout)
    surf_paths = _surface_paths(settings, surface or settings.render.surface, layout_obj)

    data = _stack(
        atlas.left.labels.astype(float), atlas.right.labels.astype(float), layout_obj
    )
    n = int(data.max())
    plot = Plot(
        surf_lh=surf_paths.get("left"),
        surf_rh=surf_paths.get("right"),
        layout=layout_obj.layout_style,
        views=list(layout_obj.views),
        mirror_views=layout_obj.mirror_views,
        flip=layout_obj.flip,
        size=layout_obj.size or settings.render.size,
        zoom=layout_obj.zoom or settings.render.zoom,
    )
    plot.add_layer(
        data,
        cmap=cmaps.cluster_colormap(n),
        color_range=(0, max(n, 1)),
        as_outline=outline,
        cbar=False,
        zero_transparent=True,
    )
    return _build(plot, settings, colorbar=False, title=title or atlas.display_name)


def save_figure(
    figure,
    path: Path | str,
    *,
    settings: Optional[Settings] = None,
    dpi: Optional[int] = None,
    transparent: bool = False,
    close: bool = True,
) -> Path:
    """Write a rendered figure to PNG / PDF / SVG / TIFF."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolution = dpi or (settings.render.dpi if settings else 300)
    figure.savefig(
        path, dpi=resolution, bbox_inches="tight", transparent=transparent
    )
    if close:
        import matplotlib.pyplot as plt

        plt.close(figure)
    log.info("wrote figure to %s", path)
    return path


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _as_layout(layout: str | Layout) -> Layout:
    return layout if isinstance(layout, Layout) else get_layout(str(layout))


def _surface_paths(
    settings: Settings, kind: str, layout: Layout
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for hemi in layout.hemispheres:
        paths[hemi] = str(settings.resources.surface_path(hemi, kind))
    return paths


def _add_underlay(plot, settings, layout: Layout, name=None) -> bool:
    """Put the greyscale folding pattern down before anything else.

    surfplot composites layers in the order they are added, so this has to be
    the first ``add_layer`` call: the statistic then sits on top of the sulci
    rather than under them.  ``zero_transparent=False`` matters here -- a grey
    of exactly zero is a legitimate value for an underlay, not "nothing".
    """
    from .underlay import load_underlay

    shading = load_underlay(
        settings,
        name,
        dark=settings.render.underlay_dark,
        light=settings.render.underlay_light,
    )
    if shading is None:
        return False
    try:
        plot.add_layer(
            _stack(shading.left, shading.right, layout),
            cmap="Greys_r",
            color_range=(0.0, 1.0),
            cbar=False,
            zero_transparent=False,
        )
    except Exception as exc:  # pragma: no cover - surfplot version dependent
        log.warning("could not draw the underlay: %s", exc)
        return False
    return True


def _stack(left: np.ndarray, right: np.ndarray, layout: Layout) -> np.ndarray:
    """Concatenate the hemispheres surfplot will actually draw."""
    parts = []
    if "left" in layout.hemispheres:
        parts.append(np.asarray(left, dtype=float))
    if "right" in layout.hemispheres:
        parts.append(np.asarray(right, dtype=float))
    if not parts:
        raise RenderError(f"layout {layout.name!r} selects no hemisphere")
    return np.concatenate(parts)


def _detach_from_pyplot(figure) -> None:
    """Take the figure out of pyplot's registry, keeping the object usable.

    surfplot builds through pyplot, so without this every render would leak a
    figure into ``plt.get_fignums()``. The Figure itself survives and can be
    attached to a fresh canvas -- which is exactly what embedding does.
    """
    try:
        import matplotlib.pyplot as plt

        if figure.number in plt.get_fignums():
            plt.close(figure)
    except Exception:  # pragma: no cover - pyplot is optional here
        pass


def _positive_range(data: np.ndarray) -> tuple[float, float]:
    finite = data[np.isfinite(data) & (data > 0)]
    if finite.size == 0:
        return (0.0, 1.0)
    return (float(finite.min()), float(np.percentile(finite, 99.5)))


def _build(plot, settings: Settings, *, colorbar: bool, title: Optional[str] = None):
    try:
        figure = plot.build(colorbar=colorbar)
        if title:
            figure.suptitle(title, fontsize=settings.render.label_size + 2, y=0.99)
        _detach_from_pyplot(figure)
        return figure
    except RenderError:
        raise
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RenderError(
            f"surfplot could not render ({exc}). On a headless Linux machine "
            f"run the command under 'xvfb-run -a', or install a VTK build with "
            f"OSMesa/EGL support."
        ) from exc
