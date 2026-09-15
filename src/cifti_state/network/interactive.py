"""A window you can rotate the network in, and turn dials on, before committing.

The figures this package writes are decided by numbers -- an edge count, a
shell opacity, a range of tube radii -- and the only way to know whether those
numbers are right is to look. Guessing them from a static PNG means a render,
a look, an edit, another render; four or five rounds of that is normal, and
each round loses the thread of what you were comparing.

So: :func:`explore` opens the same scene live, with sliders on the three
numbers that actually get fiddled with, and when you close the window it
prints the command line that reproduces exactly what you were looking at.
Tune by eye, paste the command, get the file. Nothing you set here is saved
implicitly -- the printed command is the whole handoff, which keeps a figure
in a paper traceable to a line you can read.

Needs a real window: this is the one part of the package that cannot run
offscreen.
"""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..logging_setup import get_logger
from .data import NetworkData
from .scene import build_network_scene, resolve_view
from .style import NetworkStyle
from .threshold import max_positive_edge_count, pair_maxima

log = get_logger(__name__)

__all__ = ["explore", "command_line_for"]


def command_line_for(
    style: NetworkStyle,
    *,
    source: Path | str,
    edge_count: Optional[int],
    view: str,
    output: str = "figure.png",
) -> str:
    """The ``cifti-state network`` line that reproduces this state."""
    parts = ["cifti-state", "network", str(source)]
    if edge_count:
        parts += ["-k", str(edge_count)]
    parts += ["--views", view]
    if style.node.size_by:
        parts += ["--size-by", style.node.size_by]
    if style.node.color_by:
        parts += ["--color-by", style.node.color_by]
        parts += ["--colors", *list(style.node.discrete_colors)]
    lo, hi = style.node.size_range
    parts += ["--size-range", f"{lo:.2f}", f"{hi:.2f}"]
    parts += ["--labels-on" if style.node.labels else "--no-labels"]
    if style.edge.color_mode != "none":
        parts += ["--edge-color", style.edge.color_mode]
    if style.edge.color_mode in {"sign", "signed"}:
        parts += ["--edge-colors", style.edge.negative_color,
                  style.edge.positive_color]
    if style.edge.direction_mode != "arrow":
        parts += ["--direction", style.edge.direction_mode]
    if style.edge.width_by_weight:
        wlo, whi = style.edge.width_range
        parts += ["--width-range", f"{wlo:.2f}", f"{whi:.2f}"]
    else:
        parts += ["--edge-width", f"{style.edge.width:.2f}"]
    parts += ["--brain-opacity", f"{style.brain.opacity:.2f}"]
    if len(style.brain.hemispheres) == 1:
        parts += ["--hemisphere", style.brain.hemispheres[0]]
    parts += ["-o", output]
    return " ".join(shlex.quote(p) for p in parts)


def explore(
    data: NetworkData,
    settings: Settings,
    style: Optional[NetworkStyle] = None,
    *,
    edge_count: Optional[int] = None,
    view: str = "left",
    source: Path | str = "NETWORK_DIR",
    output: str = "figure.png",
    window_size: tuple[int, int] = (1200, 900),
    show: bool = True,
) -> dict[str, Any]:
    """Open the network in a window with live sliders.

    Returns the state you left it in, and prints the command line for it.
    ``show=False`` builds everything and returns without blocking, which is
    what the tests use.
    """
    from ..viz.interactive import _require_pyvista

    pv = _require_pyvista()
    style = style or NetworkStyle()
    view = resolve_view(view)

    n_pairs = int(pair_maxima(data.matrix).size)
    limit = max_positive_edge_count(data.matrix)
    current = {
        "edge_count": int(edge_count or style.edge_count or min(16, n_pairs)),
        "opacity": float(style.brain.opacity),
        "width_scale": 1.0,
    }
    base_width_range = tuple(style.edge.width_range)
    base_width = float(style.edge.width)

    plotter = pv.Plotter(window_size=list(window_size))
    plotter.set_background(style.background)

    def current_style() -> NetworkStyle:
        scale = current["width_scale"]
        edge = replace(
            style.edge,
            width=base_width * scale,
            width_range=(base_width_range[0] * scale,
                         base_width_range[1] * scale),
        )
        brain = replace(style.brain, opacity=current["opacity"],
                        show=current["opacity"] > 0.01)
        return replace(style, edge=edge, brain=brain)

    state: dict[str, Any] = {}

    def rebuild(*, keep_camera: bool = True) -> None:
        camera = plotter.camera_position if keep_camera else None
        plotter.clear_actors()
        active = current_style()
        scene = build_network_scene(
            data, settings, active, edge_count=current["edge_count"],
            view=view,
        )
        scene.add_to(plotter, reset_camera=False)
        if camera is not None:
            plotter.camera_position = camera
        else:
            scene.apply_view(plotter, view)
        edges = scene.edges
        state["scene"] = scene
        state["edges"] = edges
        cutoff = edges.threshold if edges is not None else float("nan")
        warn = "  NEGATIVE CUTOFF" if current["edge_count"] > limit else ""
        plotter.add_text(
            f"k = {current['edge_count']}  ->  "
            f"{0 if edges is None else len(edges)} arrows   "
            f"cutoff {cutoff:.5f}{warn}\n"
            f"shell opacity {current['opacity']:.2f}   "
            f"edge width x{current['width_scale']:.2f}",
            position="upper_left", font_size=10,
            color="#a03030" if warn else "#2a3a44",
            name="readout",
        )

    rebuild(keep_camera=False)

    # Sliders.  Three, because these are the three numbers that get changed;
    # anything else is a decision rather than a dial and belongs on the command
    # line where it is written down.
    def on_k(value: float) -> None:
        current["edge_count"] = int(max(1, min(n_pairs, round(value))))
        rebuild()

    def on_opacity(value: float) -> None:
        current["opacity"] = float(value)
        rebuild()

    def on_width(value: float) -> None:
        current["width_scale"] = float(value)
        rebuild()

    try:
        plotter.add_slider_widget(
            on_k, [1, n_pairs], value=current["edge_count"],
            title=f"edge count k  (positive cutoff up to {limit})",
            pointa=(0.025, 0.14), pointb=(0.31, 0.14),
            style="modern", fmt="%.0f",
        )
        plotter.add_slider_widget(
            on_opacity, [0.0, 1.0], value=current["opacity"],
            title="shell opacity",
            pointa=(0.36, 0.14), pointb=(0.645, 0.14),
            style="modern", fmt="%.2f",
        )
        plotter.add_slider_widget(
            on_width, [0.25, 3.0], value=current["width_scale"],
            title="edge width",
            pointa=(0.69, 0.14), pointb=(0.975, 0.14),
            style="modern", fmt="%.2f",
        )
    except Exception as exc:  # pragma: no cover - renderer dependent
        log.warning("could not add the sliders (%s); the view is still live", exc)

    command = ""
    if show:
        plotter.show()
        command = command_line_for(
            current_style(), source=source,
            edge_count=current["edge_count"], view=view, output=output,
        )
        print("\nThe figure you were looking at:\n")
        print("  " + command + "\n")
    state.update(
        edge_count=current["edge_count"],
        opacity=current["opacity"],
        width_scale=current["width_scale"],
        style=current_style(),
        plotter=plotter,
        command=command,
        rebuild=rebuild,
        # The slider callbacks, so a test can drive them without a window.
        set_edge_count=on_k,
        set_opacity=on_opacity,
        set_width_scale=on_width,
    )
    return state
