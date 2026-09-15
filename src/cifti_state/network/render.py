"""Turning a scene into a file: cameras, panels, titles, export.

One camera view is one panel.  Panels are rendered one at a time offscreen and
tiled with matplotlib, which is what lets the figure carry a title and a
caption, and what makes PDF and SVG work: PyVista can write those itself
through gl2ps, but only for a single view and with the shading flattened, so
the default here is to rasterise each panel at ``dpi`` and let matplotlib own
the page.  ``vector=True`` takes the gl2ps route when you need real vectors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ..config import Settings
from ..logging_setup import get_logger
from .data import NetworkData, NetworkError
from .scene import NetworkScene, build_network_scene, resolve_view
from .style import NetworkStyle
from .threshold import EdgeSet

log = get_logger(__name__)

__all__ = ["NetworkFigure", "render_network", "render_network_batch"]

#: What to call each view under its panel.
_PANEL_NAME = {
    "left": "left lateral", "right": "right lateral",
    "anterior": "anterior", "posterior": "posterior",
    "dorsal": "dorsal", "ventral": "ventral",
}


@dataclass
class NetworkFigure:
    """What was drawn, and where it went."""

    path: Optional[Path] = None
    figure: Any = None                      #: the matplotlib Figure
    edges: Optional[EdgeSet] = None
    views: tuple[str, ...] = ()
    panels: list[np.ndarray] = field(default_factory=list)
    caption: str = ""

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"{self.path or '<unsaved>'}: {self.caption}"


def _offscreen_plotter(pv, size: tuple[int, int], background: str):
    plotter = pv.Plotter(off_screen=True, window_size=list(size))
    plotter.set_background(background)
    return plotter


#: ``reset_camera`` fits the scene's bounding sphere, which on a brain-shaped
#: scene leaves roughly a third of each axis empty.  The output is cropped
#: anyway, so a loose camera costs resolution rather than layout: at this zoom
#: the silhouette fills most of the frame and the pixels are spent on the
#: brain.  Anything the user passes multiplies it.
_FIT_ZOOM = 1.5


def _render_panel(
    scene: NetworkScene,
    view: str,
    *,
    size: tuple[int, int],
    zoom: float,
) -> np.ndarray:
    from ..viz.interactive import _require_pyvista

    pv = _require_pyvista()

    def shoot(factor: float) -> np.ndarray:
        plotter = _offscreen_plotter(pv, size, scene.background)
        try:
            scene.add_to(plotter, reset_camera=False)
            scene.apply_view(plotter, view)
            if factor != 1.0:
                plotter.camera.zoom(factor)
            return np.asarray(plotter.screenshot(return_img=True))
        finally:
            plotter.close()

    factor = _FIT_ZOOM * zoom
    image = shoot(factor)
    # Zooming to fill risks zooming past the edge.  Content touching the frame
    # means something was cut off, which a crop cannot undo, so back off once
    # and take the looser framing rather than ship a clipped brain.
    if factor > 1.0 and _touches_edge(image, scene.background):
        image = shoot(max(1.0, factor / 1.45))
    return image


def _touches_edge(panel: np.ndarray, background: str) -> bool:
    box = _content_box(panel, background)
    if box is None:
        return False
    r0, r1, c0, c1 = box
    return r0 == 0 or c0 == 0 or r1 >= panel.shape[0] or c1 >= panel.shape[1]


def _content_box(panel: np.ndarray, background: str, tol: int = 6):
    """The bounding box of everything that is not the background colour."""
    from matplotlib.colors import to_rgb

    rgb = np.asarray(panel, dtype=np.int16)[:, :, :3]
    bg = np.asarray([round(c * 255) for c in to_rgb(background)], dtype=np.int16)
    drawn = (np.abs(rgb - bg[None, None, :]).max(axis=2) > tol)
    rows = np.flatnonzero(drawn.any(axis=1))
    cols = np.flatnonzero(drawn.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def _crop_panels(
    panels: Sequence[np.ndarray], background: str, *, margin: float = 0.03,
) -> list[np.ndarray]:
    """Trim the empty border off each panel, then pad them back to one size.

    Cropping each view to its own silhouette and re-padding to the largest of
    them keeps every panel at the *same pixel scale* -- a node that is 20 px
    across in the dorsal view is 20 px across in the lateral view -- while
    throwing away the blank margin that a fitted camera always leaves. Sizing
    each panel to fill its own frame instead would be prettier and would quietly
    rescale the brain between views.
    """
    boxes = [_content_box(p, background) for p in panels]
    if any(b is None for b in boxes):
        return list(panels)
    pad_px = int(round(margin * max(p.shape[0] for p in panels)))
    cropped = []
    for panel, box in zip(panels, boxes):
        r0, r1, c0, c1 = box  # type: ignore[misc]
        r0 = max(0, r0 - pad_px); c0 = max(0, c0 - pad_px)
        r1 = min(panel.shape[0], r1 + pad_px); c1 = min(panel.shape[1], c1 + pad_px)
        cropped.append(panel[r0:r1, c0:c1])

    from matplotlib.colors import to_rgb

    height = max(p.shape[0] for p in cropped)
    width = max(p.shape[1] for p in cropped)
    fill = np.asarray(
        [round(c * 255) for c in to_rgb(background)], dtype=np.uint8
    )
    out = []
    for panel in cropped:
        canvas = np.empty((height, width, panel.shape[2]), dtype=np.uint8)
        canvas[:, :, :3] = fill
        if panel.shape[2] == 4:
            canvas[:, :, 3] = 255
        top = (height - panel.shape[0]) // 2
        left = (width - panel.shape[1]) // 2
        canvas[top:top + panel.shape[0], left:left + panel.shape[1]] = panel
        out.append(canvas)
    return out


def _grid(n: int, columns: Optional[int]) -> tuple[int, int]:
    if columns:
        return math.ceil(n / columns), int(columns)
    if n <= 3:
        return 1, n
    if n == 4:
        return 2, 2
    return math.ceil(n / 3), 3


def render_network(
    data: NetworkData,
    settings: Settings,
    *,
    style: Optional[NetworkStyle] = None,
    out: Optional[Path | str] = None,
    views: Optional[Sequence[str]] = None,
    edge_count: Optional[int] = None,
    threshold: Optional[float] = None,
    top: Optional[int] = None,
    absolute: bool = False,
    drop_negative: bool = False,
    size: tuple[int, int] = (1100, 900),
    zoom: float = 1.0,
    columns: Optional[int] = None,
    dpi: int = 200,
    title: Optional[str] = None,
    caption: Optional[str] = None,
    show_caption: bool = True,
    vector: bool = False,
    mesh: Optional[str] = None,
    panel_labels: bool = True,
    crop: bool = True,
) -> NetworkFigure:
    """Draw ``data`` and write a figure.

    Every panel shows the same network from a different camera, so the edge
    selection is made once and shared -- the panels cannot disagree about which
    connections exist.

    The caption is generated from the selection rule rather than typed by hand,
    because the rule is the part of a network figure a reader cannot recover by
    looking at it.  Pass ``caption=""`` to suppress it.
    """
    style = style or NetworkStyle()
    view_names = tuple(
        resolve_view(v) for v in (views if views is not None else style.views)
    )
    if not view_names:
        raise NetworkError("no views to draw")

    scene = build_network_scene(
        data, settings, style,
        edge_count=edge_count, threshold=threshold, top=top,
        absolute=absolute, drop_negative=drop_negative,
        view=view_names[0], mesh=mesh,
    )

    if vector:
        return _render_vector(scene, view_names, out, size, zoom, dpi)

    panels = [
        _render_panel(scene, view, size=size, zoom=zoom) for view in view_names
    ]
    if crop:
        panels = _crop_panels(panels, style.background)

    text = caption if caption is not None else _caption(data, scene.edges, style)
    figure = _tile(
        panels, view_names,
        title=title if title is not None else style.title,
        caption=text if show_caption else "",
        dpi=dpi, columns=columns, background=style.background,
        panel_labels=panel_labels,
    )

    path: Optional[Path] = None
    if out is not None:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, dpi=dpi, facecolor=style.background,
                       bbox_inches="tight", pad_inches=0.08)
        log.info("wrote %s", path)

    return NetworkFigure(
        path=path, figure=figure, edges=scene.edges, views=view_names,
        panels=panels, caption=text,
    )


def _caption(data: NetworkData, edges: Optional[EdgeSet],
             style: NetworkStyle) -> str:
    if edges is None:
        return ""
    bits = [f"{data.n_nodes} nodes", edges.describe()]
    if data.is_directed:
        bits.append("directed (row -> column)")
    if style.node.size_by:
        bits.append(f"node size: {style.node.size_by}")
    if style.node.color_by:
        bits.append(f"node colour: {style.node.color_by}")
    if style.edge.width_by_weight:
        bits.append("edge width: |weight|")
    if style.edge.color_mode == "signed":
        bits.append(
            f"edge colour: weight, {style.edge.negative_color} negative to "
            f"{style.edge.positive_color} positive"
        )
    elif style.edge.color_mode == "weight":
        bits.append("edge colour: |weight|")
    if edges.n_negative:
        # Worth saying out loud, because a negative connection drawn like a
        # positive one is the one way a network figure can be read backwards
        # without looking wrong.  Unless the colouring distinguishes them, in
        # which case the figure already says it.
        if style.edge.color_mode in {"sign", "signed"}:
            bits.append(
                f"{edges.n_negative} negative edges, shown in "
                f"{style.edge.negative_color}"
            )
        else:
            bits.append(
                f"{edges.n_negative} of the drawn edges are negative and are "
                "not marked as such"
            )
    return "; ".join(bits) + "."


def _tile(
    panels: Sequence[np.ndarray],
    views: Sequence[str],
    *,
    title: str,
    caption: str,
    dpi: int,
    columns: Optional[int],
    background: str,
    panel_labels: bool,
):
    from matplotlib.figure import Figure

    rows, cols = _grid(len(panels), columns)
    height, width = panels[0].shape[:2]
    fig_w = cols * width / dpi
    fig_h = rows * height / dpi

    title_in = 0.42 if title else 0.0
    # The caption is the one piece of text whose height is not known in
    # advance: it wraps, and a two-line caption that lands on top of the panel
    # labels is how a perfectly good figure becomes unusable. Estimate the
    # wrapped height from the width instead of hoping one line is enough.
    caption_size = 10.0
    per_line_in = caption_size * 1.45 / 72.0
    chars_per_line = max(20, int(fig_w * 72.0 / (caption_size * 0.52)))
    n_lines = math.ceil(len(caption) / chars_per_line) if caption else 0
    caption_in = (0.22 + n_lines * per_line_in) if caption else 0.0

    extra = title_in + caption_in
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    figure = Figure(figsize=(fig_w, fig_h + extra), dpi=dpi,
                    facecolor=background)
    # A bare Figure carries a canvas that cannot produce a renderer, so any
    # attempt to measure text silently does nothing.  Attaching Agg here is
    # what makes _fit_title able to see how wide the title actually is.
    FigureCanvasAgg(figure)
    top = 1.0 - (title_in / (fig_h + extra) if title else 0.0)
    bottom = caption_in / (fig_h + extra) if caption else 0.0
    axes = figure.subplots(rows, cols, squeeze=False)
    figure.subplots_adjust(left=0, right=1, top=top, bottom=bottom,
                           wspace=0.0, hspace=0.0)
    for index, ax in enumerate(axes.ravel()):
        ax.set_axis_off()
        ax.set_facecolor(background)
        if index >= len(panels):
            continue
        ax.imshow(panels[index])
        if panel_labels and len(panels) > 1:
            ax.text(
                0.5, 0.012, _PANEL_NAME.get(views[index], views[index]),
                transform=ax.transAxes,
                ha="center", va="bottom", fontsize=10.5, color="#4a5860",
            )
    if title:
        _fit_title(figure, title, fig_w, top_inches=title_in)
    if caption:
        figure.text(
            0.5, 0.10 * caption_in / (fig_h + extra), caption,
            ha="center", va="bottom", fontsize=caption_size,
            color="#54646d", wrap=True,
        )
    return figure


def _fit_title(figure, title: str, fig_w: float, *, top_inches: float,
               size: float = 16.0, floor: float = 9.5) -> None:
    """Add the title at the largest size that fits the figure's width.

    Worth the trouble because of how it fails otherwise: the panels are cropped
    to their content, so the figure is only as wide as the brain, and a title
    wider than that makes ``bbox_inches="tight"`` grow the canvas sideways --
    leaving the brain stranded in the middle of a band of white that looks like
    a bug in the renderer rather than a long title.
    """
    text = figure.suptitle(title, fontsize=size, color="#16242c", y=0.988)
    try:
        renderer = figure.canvas.get_renderer()
    except AttributeError:  # pragma: no cover - backend without a renderer
        return
    available = fig_w * figure.dpi * 0.96
    width = text.get_window_extent(renderer).width
    if width <= available:
        return
    shrunk = max(floor, size * available / width)
    text.set_fontsize(shrunk)
    if text.get_window_extent(renderer).width > available:
        # Still too long at the smallest size worth reading: wrap it, and take
        # the second line out of the space reserved above the panels.
        text.set_wrap(True)
        text.set_y(0.988 - 0.35 * (size / 72.0) / (figure.get_figheight()))


def _render_vector(scene, views, out, size, zoom, dpi) -> NetworkFigure:
    """Single-panel vector export through PyVista's gl2ps writer."""
    from ..viz.interactive import _require_pyvista

    if out is None:
        raise NetworkError("vector export needs an output path")
    path = Path(out)
    if path.suffix.lower() not in {".svg", ".pdf", ".eps", ".ps", ".tex"}:
        raise NetworkError(
            f"vector export cannot write {path.suffix!r}; use .svg, .pdf or .eps"
        )
    if len(views) > 1:
        log.warning(
            "vector export writes a single view; drawing %s and ignoring %s",
            views[0], ", ".join(views[1:]),
        )
    pv = _require_pyvista()
    path.parent.mkdir(parents=True, exist_ok=True)
    plotter = _offscreen_plotter(pv, size, scene.background)
    try:
        scene.add_to(plotter, reset_camera=False)
        scene.apply_view(plotter, views[0])
        if zoom != 1.0:
            plotter.camera.zoom(zoom)
        plotter.save_graphic(str(path))
    finally:
        plotter.close()
    log.info("wrote %s", path)
    return NetworkFigure(path=path, edges=scene.edges, views=(views[0],))


def render_network_batch(
    datasets: Sequence[NetworkData],
    settings: Settings,
    out_dir: Path | str,
    *,
    style: Optional[NetworkStyle] = None,
    prefix: str = "",
    suffix: str = ".png",
    **kwargs: Any,
) -> list[NetworkFigure]:
    """Draw several datasets with one style, the way the browser's Batch does.

    The point of a batch is comparability, so anything that would differ
    between panels by accident is held fixed: the same style, the same views,
    the same selection rule.  What legitimately differs -- the matrices and
    their attribute tables -- comes from the datasets.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    figures: list[NetworkFigure] = []
    for data in datasets:
        name = f"{prefix}{data.name or 'network'}{suffix}"
        figures.append(
            render_network(data, settings, style=style,
                           out=out_dir / name, **kwargs)
        )
    return figures
