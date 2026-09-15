"""Assembling the 3D scene: glass brain, node spheres, directed edges.

The one thing worth knowing before reading this file is the draw order.  A
translucent surface has to be composited against whatever is behind it, and
VTK does that correctly only if what is behind it has already been drawn.  So
every opaque actor -- edges first, then nodes -- goes in before the shell, and
the shell goes in last.  Depth peeling would remove the constraint, but it is
unavailable or broken on software OpenGL, which is exactly where a batch of
figures gets rendered, so the scene is built to not need it.

The second thing is backface culling on the shell.  Cortex is folded, so a
line of sight crosses a dozen sulcal walls; at any honest per-surface opacity
they stack up and the "glass" brain comes out opaque.  Drawing only the
outward-facing triangles leaves a single layer of glass, and the opacity you
ask for is then the opacity you see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..config import Settings
from ..io.surface import load_surface
from ..logging_setup import get_logger
from ..viz.interactive import _require_pyvista, surface_to_polydata
from .data import NetworkData, NetworkError
from .style import (
    NetworkStyle,
    edge_colors,
    edge_radii,
    node_colors,
    node_radii,
)
from .threshold import EdgeSet, select_edges

log = get_logger(__name__)

__all__ = ["NetworkScene", "build_network_scene", "CAMERA_VIEWS"]

#: Camera names understood by :meth:`NetworkScene.apply_view`.
CAMERA_VIEWS: dict[str, str] = {
    "left": "Left lateral",
    "right": "Right lateral",
    "anterior": "Front",
    "posterior": "Back",
    "dorsal": "Top",
    "ventral": "Bottom",
}

_VIEW_VECTORS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "anterior": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "posterior": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "dorsal": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "ventral": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
}

_VIEW_ALIASES = {
    "lateral": "left", "top": "dorsal", "bottom": "ventral",
    "front": "anterior", "back": "posterior", "superior": "dorsal",
    "inferior": "ventral", "lh": "left", "rh": "right",
}


def resolve_view(name: str) -> str:
    key = str(name).strip().lower()
    key = _VIEW_ALIASES.get(key, key)
    if key not in _VIEW_VECTORS:
        raise NetworkError(
            f"unknown view {name!r}; choose from "
            + ", ".join(sorted(_VIEW_VECTORS))
        )
    return key


# --------------------------------------------------------------------------- #
# the scene
# --------------------------------------------------------------------------- #


@dataclass
class NetworkScene:
    """Meshes and their draw settings, ready to hand to a PyVista plotter."""

    #: ``(mesh, kwargs)`` for the opaque edge tubes and arrowheads.
    edge_actors: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    #: ``(mesh, kwargs)`` for the node spheres.
    node_actors: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    #: ``(mesh, kwargs)`` for the translucent shell, drawn last.
    brain_actors: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)

    label_points: Optional[np.ndarray] = None
    label_text: tuple[str, ...] = ()
    label_kwargs: dict[str, Any] = field(default_factory=dict)

    background: str = "#ffffff"
    view: str = "left"
    title: str = ""
    #: The edges that were drawn, for the caption and for tests.
    edges: Optional[EdgeSet] = None
    #: Node positions after any hemisphere split, for camera framing.
    node_points: Optional[np.ndarray] = None

    def add_to(self, plotter, *, reset_camera: bool = True) -> None:
        """Draw everything into ``plotter``, in the order that composites."""
        plotter.set_background(self.background)
        for mesh, kwargs in self.edge_actors:
            plotter.add_mesh(mesh, reset_camera=False, **kwargs)
        for mesh, kwargs in self.node_actors:
            plotter.add_mesh(mesh, reset_camera=False, **kwargs)
        # Last: see the module docstring.
        for mesh, kwargs in self.brain_actors:
            plotter.add_mesh(mesh, reset_camera=False, **kwargs)
        if self.label_points is not None and len(self.label_text):
            try:
                plotter.add_point_labels(
                    self.label_points, list(self.label_text), **self.label_kwargs
                )
            except Exception as exc:  # pragma: no cover - renderer dependent
                log.debug("could not add node labels (%s)", exc)
        if reset_camera:
            self.apply_view(plotter, self.view)

    def apply_view(self, plotter, view: str) -> None:
        direction, up = _VIEW_VECTORS[resolve_view(view)]
        try:
            plotter.view_vector(direction, viewup=up)
            plotter.reset_camera()
        except Exception as exc:  # pragma: no cover - renderer dependent
            log.debug("could not set the camera (%s)", exc)


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #


def _hemisphere_of(coordinates: np.ndarray) -> np.ndarray:
    """-1 for the left hemisphere, +1 for the right, by the sign of x."""
    return np.where(coordinates[:, 0] < 0, -1.0, 1.0)


def _split(points: np.ndarray, side: float, split_mm: float) -> np.ndarray:
    if not split_mm:
        return points
    out = np.array(points, dtype=float, copy=True)
    out[:, 0] += side * split_mm / 2.0
    return out


def _tube_along(pv, start, end, radius, resolution, *, taper: float = 0.0):
    """A tube from ``start`` to ``end``, with each point's position along it.

    Returns ``(mesh, t)`` where ``t`` is 0 at the source end and 1 at the
    target end for every point of the tube, computed by projecting onto the
    axis rather than trusting the tube filter's point order.
    """
    line = pv.Line(start, end, resolution=resolution)
    if taper > 0:
        # VTK varies the radius by the *ratio* of the scalar to the smallest
        # scalar, so the ramp has to start at 1, not at 0: a ramp from zero
        # asks it to divide by zero and it silently draws a uniform tube.
        line.point_data["taper"] = np.linspace(1.0, 1.0 + taper, line.n_points)
        mesh = line.tube(
            radius=radius, scalars="taper", radius_factor=1.0 + taper,
            n_sides=16,
        )
    else:
        mesh = line.tube(radius=radius, n_sides=16)
    axis = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    length = float(np.linalg.norm(axis))
    if length == 0:
        return mesh, np.zeros(mesh.n_points)
    unit = axis / length
    t = (mesh.points - np.asarray(start, dtype=float)) @ unit / length
    return mesh, np.clip(t, 0.0, 1.0)


def _ramp_rgba(t, rgb_start, rgb_end, alpha_start, alpha_end) -> np.ndarray:
    t = np.asarray(t, dtype=float)[:, None]
    rgb = np.asarray(rgb_start)[None, :] * (1 - t) + np.asarray(rgb_end)[None, :] * t
    alpha = alpha_start * (1 - t[:, 0]) + alpha_end * t[:, 0]
    out = np.empty((t.shape[0], 4), dtype=np.uint8)
    out[:, :3] = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    out[:, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    return out


def build_network_scene(
    data: NetworkData,
    settings: Settings,
    style: Optional[NetworkStyle] = None,
    *,
    edges: Optional[EdgeSet] = None,
    edge_count: Optional[int] = None,
    threshold: Optional[float] = None,
    top: Optional[int] = None,
    absolute: bool = False,
    drop_negative: bool = False,
    view: str = "left",
    mesh: Optional[str] = None,
) -> NetworkScene:
    """Turn a dataset and a style into a drawable scene.

    ``edges`` lets you pass a selection you made yourself; otherwise one of
    ``edge_count`` / ``threshold`` / ``top`` picks it, defaulting to
    ``style.edge_count`` and then to every edge.
    """
    pv = _require_pyvista()
    style = style or NetworkStyle()

    if edges is None:
        if edge_count is None and threshold is None and top is None:
            edge_count = style.edge_count
        edges = select_edges(
            data.matrix,
            edge_count=edge_count, threshold=threshold, top=top,
            absolute=absolute, drop_negative=drop_negative,
        )

    side = _hemisphere_of(data.coordinates)
    points = np.array(data.coordinates, dtype=float, copy=True)
    if style.brain.split_mm:
        points[:, 0] += side * style.brain.split_mm / 2.0

    radii = node_radii(data, style.node)
    rgb = node_colors(data, style.node)

    scene = NetworkScene(
        background=style.background,
        view=resolve_view(view),
        title=style.title,
        edges=edges,
        node_points=points,
    )

    # -- edges: opaque, drawn first ---------------------------------------- #
    widths = edge_radii(edges.weight, style.edge)
    start_rgb, end_rgb = edge_colors(
        edges.weight, edges.source, edges.target, rgb, style.edge
    )
    arrow = style.edge.direction_mode == "arrow"
    # An arrowhead's job is to show direction, and strength is already carried
    # by the tube's width -- so when width follows the weight, the head is
    # sized from the middle of the width range and only grows past it for the
    # thick edges.  Sizing it from the tube alone makes the weakest edges'
    # arrows too small to read the direction off at all.
    head_reference = (
        float(np.mean(style.edge.width_range)) if style.edge.width_by_weight
        else 0.0
    )
    fading = style.edge.direction_mode == "opacity"
    taper = 2.0 if style.edge.direction_mode == "taper" else 0.0
    translucent_edges = fading or style.edge.opacity < 1.0

    for index in range(len(edges)):
        a_i, b_i = int(edges.source[index]), int(edges.target[index])
        a, b = points[a_i], points[b_i]
        axis = b - a
        length = float(np.linalg.norm(axis))
        if length < 1e-6:
            continue
        unit = axis / length
        radius = float(widths[index])

        tip = b - unit * float(radii[b_i]) * 0.85
        head = min(float(style.edge.arrow_size), length * 0.45) if arrow else 0.0
        tube_end = tip - unit * head
        if float(np.dot(tube_end - a, unit)) <= radius:
            # Nodes so close together that there is no room for a tube; the
            # arrowhead alone still carries the direction.
            tube_end = a + unit * max(radius, length * 0.1)

        tube, t = _tube_along(
            pv, a, tube_end, radius, style.edge.resolution, taper=taper,
        )
        alpha_lo = style.edge.fade_opacity if fading else style.edge.opacity
        rgba = _ramp_rgba(
            t, start_rgb[index], end_rgb[index], alpha_lo, style.edge.opacity
        )
        tube.point_data["rgba"] = rgba
        scene.edge_actors.append((
            tube,
            dict(scalars="rgba", rgba=True, smooth_shading=True,
                 specular=0.18, specular_power=14, ambient=0.34, diffuse=0.70,
                 show_scalar_bar=False),
        ))

        if arrow and head > 0:
            cone = pv.Cone(
                center=tip - unit * head / 2.0,
                direction=unit,
                height=head,
                radius=max(radius, head_reference) * float(style.edge.arrow_width),
                resolution=20,
            )
            scene.edge_actors.append((
                cone,
                dict(color=tuple(end_rgb[index]), smooth_shading=True,
                     specular=0.18, ambient=0.34, diffuse=0.70,
                     opacity=style.edge.opacity, show_scalar_bar=False),
            ))

    if translucent_edges:
        log.info(
            "edge direction mode %r makes the tubes translucent; on a software "
            "OpenGL renderer they may composite against the background rather "
            "than against each other. 'arrow' or 'taper' avoid that.",
            style.edge.direction_mode,
        )

    # -- nodes -------------------------------------------------------------- #
    for index in range(data.n_nodes):
        sphere = pv.Sphere(
            radius=float(radii[index]),
            center=points[index],
            theta_resolution=style.node.resolution,
            phi_resolution=style.node.resolution,
        )
        scene.node_actors.append((
            sphere,
            dict(color=tuple(rgb[index]), smooth_shading=True,
                 opacity=style.node.opacity, specular=0.22, specular_power=18,
                 ambient=0.30, diffuse=0.72, show_scalar_bar=False),
        ))

    if style.node.labels and data.labels:
        # Lift each label clear of its own sphere.  Nine nodes in one
        # hemisphere sit close together, so the offset is by radius rather
        # than a constant: the big nodes, which crowd their neighbours most,
        # push their labels furthest.
        offset = points + np.column_stack(
            [np.zeros(data.n_nodes), np.zeros(data.n_nodes),
             radii + style.node.label_offset]
        )
        scene.label_points = offset
        scene.label_text = tuple(data.label(i) for i in range(data.n_nodes))
        scene.label_kwargs = dict(
            font_size=style.node.label_size,
            text_color=style.node.label_color,
            shape=None,
            show_points=False,
            always_visible=True,
            bold=False,
        )

    # -- the shell, last ---------------------------------------------------- #
    if style.brain.show:
        for hemi in style.brain.hemispheres:
            path = settings.surface_for(hemi, style.brain.surface, mesh=mesh)
            if path is None:
                log.warning(
                    "no %s surface configured for the %s hemisphere; the "
                    "network will be drawn without that side of the shell",
                    style.brain.surface, hemi,
                )
                continue
            surface = load_surface(path, hemisphere=hemi,
                                   kind=style.brain.surface)
            polydata = surface_to_polydata(surface)
            if style.brain.split_mm:
                shift = -1.0 if hemi == "left" else 1.0
                polydata.points = _split(
                    polydata.points, shift, style.brain.split_mm
                )
            scene.brain_actors.append((
                polydata,
                dict(color=style.brain.color, opacity=style.brain.opacity,
                     smooth_shading=True, specular=style.brain.specular,
                     ambient=style.brain.ambient, diffuse=0.62,
                     culling="back" if style.brain.cull_backfaces else None,
                     show_scalar_bar=False, lighting=True),
            ))

    return scene


def check_coverage(data: NetworkData, settings: Settings, *,
                   surface: str = "midthickness",
                   mesh: Optional[str] = None) -> dict[str, Any]:
    """Are the node coordinates in the same space as the surfaces?

    Cheap sanity check worth running once on a new dataset: it reports the
    surface's bounding box, how many nodes fall inside it, and how far the
    worst one is outside.  Nodes outside almost always mean the coordinates
    are in a different space from the mesh -- voxel indices rather than
    millimetres, or a left-right flip.
    """
    bounds: list[np.ndarray] = []
    for hemi in ("left", "right"):
        path = settings.surface_for(hemi, surface, mesh=mesh)
        if path is None:
            continue
        bounds.append(load_surface(path, hemisphere=hemi).coords)
    if not bounds:
        raise NetworkError(f"no {surface} surface is configured")
    allcoords = np.vstack(bounds)
    low, high = allcoords.min(axis=0), allcoords.max(axis=0)
    points = np.asarray(data.coordinates, dtype=float)
    inside = np.all((points >= low) & (points <= high), axis=1)
    outside = np.maximum(low - points, points - high).max(axis=1)
    return {
        "surface_min": low,
        "surface_max": high,
        "n_inside": int(inside.sum()),
        "n_nodes": int(points.shape[0]),
        "worst_outside_mm": float(np.max(outside)),
        "outside_nodes": [
            data.label(i) for i in np.flatnonzero(~inside)
        ],
    }
