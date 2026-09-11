"""Interactive 3D surfaces with PyVista.

Where :mod:`cifti_state.viz.render` produces a fixed publication figure, this
module produces live ``pyvista.PolyData`` meshes you can rotate, zoom and
inspect.  Both draw the same arrays with the same colour maps, so the
interactive view and the exported figure agree.

Nothing here touches Qt.  :func:`build_scene` returns plain PyVista objects and
a description of how to colour them; the GUI hands that to a
``pyvistaqt.QtInteractor``, and a script can hand it to ``pyvista.Plotter``
just as easily::

    scene = build_scene(stat_map, settings, clusters=clusters)
    plotter = pyvista.Plotter()
    scene.add_to(plotter)
    plotter.show()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import Settings
from ..core.cluster import ClusterResult
from ..io.cifti import SurfaceStatMap
from ..io.surface import Surface, load_surface
from ..logging_setup import get_logger
from . import colormaps as cmaps

log = get_logger(__name__)

__all__ = [
    "Scene",
    "SurfaceLayer",
    "build_scene",
    "surface_to_polydata",
    "check_pyvista",
    "CAMERA_VIEWS",
]

#: Named camera positions, as (azimuth-style keyword, description).
CAMERA_VIEWS: dict[str, str] = {
    "left": "Left lateral",
    "right": "Right lateral",
    "anterior": "Front",
    "posterior": "Back",
    "dorsal": "Top",
    "ventral": "Bottom",
}

_VIEW_VECTORS = {
    # (camera direction, view-up) in RAS
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "anterior": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "posterior": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "dorsal": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "ventral": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
}


class PyVistaMissing(RuntimeError):
    """PyVista is not installed."""


def _require_pyvista():
    try:
        import pyvista as pv
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise PyVistaMissing(
            "PyVista is not installed. It is in requirements.txt:\n"
            "    pip install pyvista pyvistaqt"
        ) from exc
    return pv


def check_pyvista() -> dict[str, Any]:
    """Report whether the interactive view can run here."""
    info: dict[str, Any] = {"pyvista": None, "pyvistaqt": None, "vtk": None}
    try:
        import pyvista

        info["pyvista"] = pyvista.__version__
    except ImportError:
        return info
    try:
        import pyvistaqt

        info["pyvistaqt"] = pyvistaqt.__version__
    except ImportError:
        pass
    try:
        import vtk

        info["vtk"] = vtk.vtkVersion.GetVTKVersion()
    except ImportError:
        pass
    return info


# --------------------------------------------------------------------------- #
# scene description
# --------------------------------------------------------------------------- #


@dataclass
class SurfaceLayer:
    """One hemisphere: its mesh, the scalars on it, and how to colour them."""

    hemisphere: str
    mesh: Any                       # pyvista.PolyData
    scalars: np.ndarray             # per-vertex values already on the mesh
    scalar_name: str = "value"
    cmap: Any = "viridis"
    clim: tuple[float, float] = (0.0, 1.0)
    below_color: Optional[str] = None
    nan_color: str = "#B4B4B4"
    show_scalar_bar: bool = False
    opacity: float = 1.0

    #: The same geometry carrying the greyscale folding pattern.  Drawn first,
    #: with the statistic floating just above it, so unlabelled cortex shows
    #: sulci and gyri instead of a featureless shell.
    underlay_mesh: Any = None
    underlay_name: str = "underlay"

    @property
    def n_vertices(self) -> int:
        return int(self.mesh.n_points)

    def meshes(self) -> list:
        return [m for m in (self.underlay_mesh, self.mesh) if m is not None]


@dataclass
class Scene:
    """Everything needed to draw the surfaces, toolkit-agnostic."""

    layers: list[SurfaceLayer] = field(default_factory=list)
    title: str = ""
    scalar_bar_label: str = ""
    background: str = "#FFFFFF"
    show_scalar_bar: bool = True
    clim: tuple[float, float] = (0.0, 1.0)
    cmap: Any = "viridis"
    discrete: bool = False          # cluster ids rather than a continuous map
    view: str = "dorsal"

    # -- drawing ------------------------------------------------------------ #

    def add_to(self, plotter, *, reset_camera: bool = True) -> None:
        """Add every layer to a ``pyvista.Plotter`` / ``QtInteractor``."""
        plotter.set_background(self.background)
        for index, layer in enumerate(self.layers):
            if layer.underlay_mesh is not None:
                # Matte and unlit-looking on purpose: the folding pattern is
                # context, and a shiny grey shell competes with the statistic.
                plotter.add_mesh(
                    layer.underlay_mesh,
                    scalars=layer.underlay_name,
                    cmap="Greys_r",
                    clim=(0.0, 1.0),
                    show_scalar_bar=False,
                    smooth_shading=True,
                    specular=0.02,
                    ambient=0.34,
                    diffuse=0.70,
                    reset_camera=False,
                )
            plotter.add_mesh(
                layer.mesh,
                scalars=layer.scalar_name,
                cmap=layer.cmap,
                clim=layer.clim,
                nan_color=layer.nan_color,
                # With an underlay beneath it, "no value here" must be a hole,
                # not grey paint -- otherwise the folding pattern is hidden
                # everywhere the statistic is absent, which is most of the
                # cortex.
                nan_opacity=0.0 if layer.underlay_mesh is not None else 1.0,
                opacity=layer.opacity,
                smooth_shading=True,
                specular=0.12,
                specular_power=12,
                ambient=0.28,
                diffuse=0.72,
                show_scalar_bar=(
                    self.show_scalar_bar and index == 0 and not self.discrete
                ),
                scalar_bar_args=self._scalar_bar_args() if index == 0 else None,
                reset_camera=False,
            )
        if self.scalar_bar_label:
            # The bar's own title is drawn rotated and cramped in a horizontal
            # bar, so the label goes beside it as plain text instead.
            try:
                plotter.add_text(
                    self.scalar_bar_label,
                    position="lower_left",
                    font_size=9,
                    color="#123338",
                )
            except Exception:  # pragma: no cover - renderer dependent
                pass
        if reset_camera:
            self.apply_view(plotter, self.view)

    def apply_view(self, plotter, view: str) -> None:
        """Point the camera at one of :data:`CAMERA_VIEWS`."""
        direction, up = _VIEW_VECTORS.get(view, _VIEW_VECTORS["left"])
        try:
            plotter.view_vector(direction, viewup=up)
            plotter.reset_camera()
        except Exception as exc:  # pragma: no cover - depends on the renderer
            log.debug("could not set the camera (%s)", exc)

    def _scalar_bar_args(self) -> dict[str, Any]:
        return {
            "title": "",
            "n_labels": 3,
            "fmt": "%.3g",
            "vertical": False,
            "position_x": 0.32,
            "position_y": 0.03,
            "width": 0.36,
            "height": 0.055,
            "title_font_size": 12,
            "label_font_size": 11,
            "color": "#123338",
        }


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #


def surface_to_polydata(surface: Surface, scalars: Optional[np.ndarray] = None,
                        name: str = "value"):
    """Convert a :class:`~cifti_state.io.surface.Surface` to ``pyvista.PolyData``."""
    pv = _require_pyvista()
    faces = np.asarray(surface.faces, dtype=np.int64)
    # PyVista wants a flat [n_points, p0, p1, p2, ...] connectivity array.
    cells = np.column_stack(
        [np.full(len(faces), 3, dtype=np.int64), faces]
    ).ravel()
    mesh = pv.PolyData(np.asarray(surface.coords, dtype=float), cells)
    if scalars is not None:
        mesh.point_data[name] = np.asarray(scalars, dtype=float)
        mesh.set_active_scalars(name)
    return mesh


def build_scene(
    stat_map: Optional[SurfaceStatMap],
    settings: Settings,
    *,
    clusters: Optional[ClusterResult] = None,
    mode: str = "masked",              # masked | full | clusters
    surface: Optional[str] = None,
    hemispheres: tuple[str, ...] = ("left", "right"),
    color_range: Optional[tuple[float, float]] = None,
    colormap: Optional[str] = None,
    background: str = "#FFFFFF",
    title: str = "",
    view: str = "dorsal",
    split_mm: float = 0.0,
    underlay: Optional[str] = None,
) -> Scene:
    """Build the interactive scene.

    ``mode``
        ``masked`` shows the statistic only inside surviving clusters,
        ``full`` shows the whole map, ``clusters`` colours each cluster by id.
    ``split_mm``
        Push the hemispheres apart by this many millimetres so both are visible
        at once; 0 keeps their true anatomical positions.
    ``underlay``
        Name of a configured underlay (``sulc``), ``"none"`` to suppress it, or
        ``None`` to use ``render.underlay`` from the settings.
    """
    _require_pyvista()

    if mode == "clusters" and clusters is None:
        raise ValueError("mode='clusters' needs a ClusterResult")
    if mode != "clusters" and stat_map is None:
        raise ValueError("a statistic map is required unless mode='clusters'")

    kind = surface or settings.render.surface
    layers: list[SurfaceLayer] = []
    values: dict[str, np.ndarray] = {}

    from .underlay import load_underlay

    shading = load_underlay(
        settings,
        underlay,
        dark=settings.render.underlay_dark,
        light=settings.render.underlay_light,
    )

    for hemi in hemispheres:
        path = settings.resources.surface_path(hemi, kind)
        mesh_surface = load_surface(path, hemisphere=hemi, kind=kind)
        values[hemi] = _values_for(hemi, stat_map, clusters, mode)
        if mesh_surface.n_vertices != values[hemi].size:
            raise ValueError(
                f"{hemi}: the {kind} surface has {mesh_surface.n_vertices} vertices "
                f"but the data has {values[hemi].size}"
            )
        stat_mesh = surface_to_polydata(mesh_surface, values[hemi], "value")

        underlay_mesh = None
        if shading is not None:
            grey = shading.hemi(hemi)
            if grey.size == mesh_surface.n_vertices:
                underlay_mesh = surface_to_polydata(mesh_surface, grey, "underlay")
                # The statistic is lifted a fraction of a millimetre off the
                # surface. Two coincident meshes fight for the same depth
                # values and the result flickers as you rotate ("z-fighting");
                # the offset is far below anatomical resolution but enough to
                # settle the depth test.
                _offset_along_normals(stat_mesh, 0.08)
            else:
                log.warning(
                    "underlay %s has %d vertices for the %s hemisphere but the "
                    "surface has %d; drawing without it",
                    shading.name, grey.size, hemi, mesh_surface.n_vertices,
                )

        layers.append(
            SurfaceLayer(
                hemisphere=hemi,
                mesh=stat_mesh,
                scalars=values[hemi],
                scalar_name="value",
                underlay_mesh=underlay_mesh,
            )
        )

    stacked = np.concatenate([values[h] for h in hemispheres])
    if mode == "clusters":
        n = clusters.n_clusters
        cmap = cmaps.cluster_colormap(n)
        clim = (0.0, float(max(n, 1)))
        discrete = True
        bar_label = "cluster"
    else:
        finite = stacked[np.isfinite(stacked)]
        symmetric = bool(np.any(finite < 0))
        if color_range is None:
            if symmetric:
                clim = cmaps.symmetric_range(finite)
            else:
                positive = finite[finite > 0]
                clim = (
                    (float(positive.min()), float(np.percentile(positive, 99.5)))
                    if positive.size
                    else (0.0, 1.0)
                )
        else:
            clim = tuple(float(v) for v in color_range)
        cmap = cmaps.get_colormap(
            colormap or ("workbench_hot_cool" if symmetric else settings.render.colormap)
        )
        discrete = False
        bar_label = stat_map.statistic if stat_map is not None else ""

    for layer in layers:
        layer.cmap = cmap
        layer.clim = clim

    if split_mm:
        _split_hemispheres(layers, split_mm)

    return Scene(
        layers=layers,
        title=title,
        scalar_bar_label=bar_label,
        background=background,
        clim=clim,
        cmap=cmap,
        discrete=discrete,
        view=view,
    )


def _values_for(hemi, stat_map, clusters, mode) -> np.ndarray:
    """The per-vertex array to colour by, with 'nothing here' set to NaN.

    NaN rather than zero, so the mesh shows its neutral grey there instead of
    whatever the bottom of the colour map happens to be.
    """
    if mode == "clusters":
        labels = clusters.labels(hemi).astype(float)
        return np.where(labels > 0, labels, np.nan)

    values = np.array(stat_map.hemi(hemi).values, dtype=float, copy=True)
    values[~np.isfinite(values)] = np.nan
    if mode == "masked":
        if clusters is None:
            raise ValueError("mode='masked' needs a ClusterResult")
        values = np.where(clusters.labels(hemi) > 0, values, np.nan)
    else:
        values = np.where(values == 0.0, np.nan, values)
    return values


def _offset_along_normals(mesh, distance: float) -> None:
    """Push every point out along its normal, in place."""
    try:
        normals = mesh.point_normals
        if normals is None:
            return
        mesh.points = np.asarray(mesh.points) + np.asarray(normals) * float(distance)
    except Exception as exc:  # pragma: no cover - geometry dependent
        log.debug("could not offset the overlay mesh (%s)", exc)


def _split_hemispheres(layers: list[SurfaceLayer], gap_mm: float) -> None:
    """Translate the hemispheres apart along x so neither hides the other."""
    for layer in layers:
        shift = -gap_mm / 2 if layer.hemisphere == "left" else gap_mm / 2
        for mesh in layer.meshes():
            mesh.translate((shift, 0.0, 0.0), inplace=True)
