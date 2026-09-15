"""How the network looks: node size and colour, edge colour and direction.

Three dataclasses hold the appearance -- :class:`NodeStyle`,
:class:`EdgeStyle`, :class:`BrainStyle` -- and :class:`NetworkStyle` carries
all three plus the camera.  They are plain data: the renderer reads them, and
:func:`load_marvl_settings` builds them from a NeuroMArVL ``settings/*.json``
so a figure tuned in the browser can be re-made here.

Sizes are in millimetres, because the scene is.  NeuroMArVL's size range is in
its own renderer's units, where the whole brain is about 2 units wide; loading
a settings file scales that range into millimetres with
:data:`MARVL_SIZE_TO_MM`, chosen so its default 0.4-1.8 range lands on nodes of
1.4-6.3 mm radius -- the same relative sizes, at a size that reads on a brain
drawn in millimetres.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ..logging_setup import get_logger
from .data import NetworkData, NetworkError

log = get_logger(__name__)

__all__ = [
    "NodeStyle",
    "EdgeStyle",
    "BrainStyle",
    "NetworkStyle",
    "MARVL_SIZE_TO_MM",
    "DEFAULT_GROUP_COLORS",
    "EDGE_COLOR_MODES",
    "EDGE_DIRECTION_MODES",
    "load_marvl_settings",
    "save_marvl_settings",
    "signed_colormap",
    "node_radii",
    "node_colors",
    "edge_colors",
    "edge_radii",
]

#: Millimetres per unit of NeuroMArVL's node-size range.  See the module note.
MARVL_SIZE_TO_MM: float = 3.5

#: The palette the bundled example uses, in ``group_id`` order.
DEFAULT_GROUP_COLORS: tuple[str, ...] = ("#ff69b4", "#add8e6", "#4caf50")

#: ``none`` one colour; ``weight`` a colour map over ``|weight|``; ``signed`` a
#: diverging map over the signed weight, so hue carries the sign and depth
#: carries the strength; ``node`` the source node's colour;
#: ``node-transitioning`` source colour fading to target colour along the edge;
#: ``sign`` two flat colours, one per sign.
EDGE_COLOR_MODES: tuple[str, ...] = (
    "none", "weight", "signed", "node", "node-transitioning", "sign",
)

#: ``none`` a plain tube; ``arrow`` a cone at the target; ``gradient`` a colour
#: ramp from the start colour at the source to the end colour at the target;
#: ``opacity`` the tube fading in towards the target; ``taper`` thin at the
#: source, thick at the target.
EDGE_DIRECTION_MODES: tuple[str, ...] = (
    "none", "arrow", "gradient", "opacity", "taper",
)

#: An attribute with no more than this many distinct values is treated as a
#: grouping variable rather than a measurement.  NeuroMArVL's own cutoff.
DISCRETE_MAX_LEVELS: int = 20


def _as_rgb(color: str) -> tuple[float, float, float]:
    """``'#rrggbb'`` or a matplotlib colour name to a 0-1 RGB triple."""
    from matplotlib.colors import to_rgb

    return tuple(float(c) for c in to_rgb(color))  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# the styles
# --------------------------------------------------------------------------- #


@dataclass
class NodeStyle:
    """Spheres at the node coordinates."""

    #: Attribute column driving sphere radius; ``None`` for a fixed radius.
    size_by: Optional[str] = None
    #: Radius in millimetres for the smallest and largest value of ``size_by``.
    size_range: tuple[float, float] = (1.4, 6.3)
    #: Radius when ``size_by`` is ``None``.
    size: float = 3.0
    #: Map the attribute onto ``size_range`` from zero, not from its own
    #: minimum.  ``False`` (the default, and NeuroMArVL's behaviour) spreads
    #: the observed range across the whole size range, which exaggerates small
    #: differences; ``True`` keeps radii proportional to the values.
    size_from_zero: bool = False

    #: Attribute column driving colour; ``None`` for ``color``.
    color_by: Optional[str] = None
    #: ``auto`` picks discrete or continuous by the number of distinct values.
    color_mode: str = "auto"          # auto | discrete | continuous
    #: Colours for a discrete attribute, in order of increasing value.
    discrete_colors: Sequence[str] = DEFAULT_GROUP_COLORS
    #: Colour map, or the two ends of one, for a continuous attribute.
    colormap: str = "viridis"
    continuous_range: Optional[tuple[float, float]] = None
    #: Colour when ``color_by`` is ``None``.
    color: str = "#d1495b"

    opacity: float = 1.0
    #: Draw node names beside the spheres.
    labels: bool = False
    label_size: int = 17
    label_color: str = "#16242c"
    #: Extra millimetres between a sphere's top and its label.
    label_offset: float = 2.5
    #: Sphere tessellation.  24 is smooth at figure resolution.
    resolution: int = 24

    def resolved_color_mode(self, values: Optional[np.ndarray]) -> str:
        if self.color_mode != "auto":
            return self.color_mode
        if values is None:
            return "discrete"
        levels = np.unique(values[np.isfinite(values)])
        return "discrete" if levels.size <= DISCRETE_MAX_LEVELS else "continuous"


@dataclass
class EdgeStyle:
    """Tubes between nodes, optionally with an arrowhead at the target."""

    color_mode: str = "none"          # see EDGE_COLOR_MODES
    #: Dark rather than mid-grey on purpose: an edge is seen *through* the
    #: translucent shell, which lightens it towards the shell's colour, so a
    #: tube that looks right on its own disappears once the brain is in front
    #: of it.
    color: str = "#39474f"
    colormap: str = "magma"
    #: Colour limits for ``color_mode="weight"``; ``None`` takes the data's.
    #: For ``"signed"`` the limits are made symmetric about zero, so that the
    #: middle of the ramp really is a weight of zero and the two signs are
    #: directly comparable.
    color_range: Optional[tuple[float, float]] = None
    #: The two ends for ``color_mode="sign"`` (flat) and ``"signed"`` (the dark
    #: end of each sign's ramp).  Dark red and dark purple by default.
    positive_color: str = "#8e1616"
    negative_color: str = "#3f2071"
    #: How far towards white the weakest edge of each sign is drawn, for
    #: ``color_mode="signed"``.  0 makes every edge the full dark colour and
    #: leaves strength entirely to the width; 1 makes the weakest edge white
    #: and invisible.  The default keeps the weakest edge unmistakably red or
    #: purple while still reading as weaker.
    signed_fade: float = 0.42

    direction_mode: str = "arrow"     # see EDGE_DIRECTION_MODES
    #: Colours at the source and target ends for ``direction_mode="gradient"``.
    start_color: str = "#ff0000"
    end_color: str = "#0000ff"

    #: Tube radius in millimetres when width is fixed.
    width: float = 0.55
    #: Scale the radius with the weight.
    width_by_weight: bool = False
    width_range: tuple[float, float] = (0.25, 1.4)

    #: Cone length in millimetres; its base radius is ``arrow_width`` times the
    #: tube radius.
    arrow_size: float = 4.2
    arrow_width: float = 3.0

    opacity: float = 1.0
    #: Fade to this at the source end when ``direction_mode="opacity"``.
    fade_opacity: float = 0.12
    #: Points along each tube.  More is smoother where the colour varies.
    resolution: int = 48

    def __post_init__(self) -> None:
        if self.color_mode not in EDGE_COLOR_MODES:
            raise NetworkError(
                f"edge color_mode {self.color_mode!r} is not one of "
                + ", ".join(EDGE_COLOR_MODES)
            )
        if self.direction_mode not in EDGE_DIRECTION_MODES:
            raise NetworkError(
                f"edge direction_mode {self.direction_mode!r} is not one of "
                + ", ".join(EDGE_DIRECTION_MODES)
            )


@dataclass
class BrainStyle:
    """The translucent cortical shell the network sits inside."""

    #: Which anatomical surface, by the name the config knows it under.
    surface: str = "midthickness"
    hemispheres: tuple[str, ...] = ("left", "right")
    color: str = "#d8d8d8"
    opacity: float = 0.55
    #: Draw only the outward-facing triangles.  A cortical surface is folded,
    #: so a translucent one stacks a dozen layers of sulcal wall between the
    #: viewer and the network and turns opaque.  Culling the back faces leaves
    #: one layer of glass, which is what makes the shell read as a shell.
    cull_backfaces: bool = True
    #: Pull the hemispheres apart by this many millimetres along x.
    split_mm: float = 0.0
    show: bool = True
    specular: float = 0.03
    ambient: float = 0.42


@dataclass
class NetworkStyle:
    """Everything about how one figure looks."""

    node: NodeStyle = field(default_factory=NodeStyle)
    edge: EdgeStyle = field(default_factory=EdgeStyle)
    brain: BrainStyle = field(default_factory=BrainStyle)
    background: str = "#ffffff"
    #: Camera views to draw, one panel each.
    views: tuple[str, ...] = ("left", "dorsal", "right")
    #: Which edge-selection rule to use; see :func:`~.threshold.select_edges`.
    edge_count: Optional[int] = None
    title: str = ""

    def with_(self, **changes: Any) -> "NetworkStyle":
        return replace(self, **changes)


# --------------------------------------------------------------------------- #
# NeuroMArVL settings files
# --------------------------------------------------------------------------- #

#: Its display modes to our camera names.
_MARVL_VIEWS = {
    "top": "dorsal", "bottom": "ventral", "left": "left", "right": "right",
    "front": "anterior", "back": "posterior", "anterior": "anterior",
    "posterior": "posterior",
}

_MARVL_DIRECTION = {
    "none": "none", "arrow": "arrow", "animation": "arrow",
    "opacity": "opacity", "transparency": "opacity", "gradient": "gradient",
}

_MARVL_EDGE_COLOR = {
    "none": "none", "weight": "weight", "node": "node",
    "node-transitioning": "node-transitioning",
}


def load_marvl_settings(
    path: Path | str, *, size_to_mm: float = MARVL_SIZE_TO_MM
) -> NetworkStyle:
    """Build a :class:`NetworkStyle` from a NeuroMArVL ``Save settings`` file.

    Everything the format carries is read: the node size attribute and range,
    the colour attribute and its discrete palette, the edge direction and
    colour modes, the surface colour and opacity, the view, whether labels are
    on, and the edge count from the first saved app.  Settings with no
    counterpart here -- 2D layouts, bundling, animation speed -- are ignored,
    and a line is logged for each so nothing disappears silently.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise NetworkError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise NetworkError(f"{path}: expected a JSON object at the top level")

    ns = raw.get("nodeSettings") or {}
    es = raw.get("edgeSettings") or {}
    ss = raw.get("surfaceSettings") or {}
    ds = raw.get("displaySettings") or {}
    apps = raw.get("saveApps") or []
    app = apps[0] if isinstance(apps, list) and apps else {}

    size_by = ns.get("nodeSizeAttribute") or None
    lo = float(ns.get("nodeSizeMin", 0.4))
    hi = float(ns.get("nodeSizeMax", 1.8))
    colors = ns.get("nodeColorDiscrete") or list(DEFAULT_GROUP_COLORS)
    color_mode = str(ns.get("nodeColorMode", "auto")).lower()
    if color_mode not in {"discrete", "continuous"}:
        color_mode = "auto"

    node = NodeStyle(
        size_by=size_by,
        size_range=(lo * size_to_mm, hi * size_to_mm),
        color_by=(ns.get("nodeColorAttribute") or None),
        color_mode=color_mode,
        discrete_colors=tuple(str(c) for c in colors),
        labels=bool(ds.get("labels", False)),
    )
    if ns.get("nodeColorContinuousMin") and ns.get("nodeColorContinuousMax"):
        node.colormap = _ramp_colormap(
            str(ns["nodeColorContinuousMin"]), str(ns["nodeColorContinuousMax"])
        )

    direction = _MARVL_DIRECTION.get(
        str(es.get("directionMode", "arrow")).lower(), "arrow"
    )
    if str(es.get("directionMode", "")).lower() == "animation":
        log.info(
            "%s: direction mode 'animation' has no still-figure equivalent; "
            "drawing arrows instead", path.name,
        )
    color_mode_edge = _MARVL_EDGE_COLOR.get(
        str(es.get("colorBy", "none")).lower(), "none"
    )
    # ``edgeColorByNodeTransitionColor`` is the colour of a *transition*, so it
    # applies only when the transition is switched on.  Reading it
    # unconditionally paints every plain edge that colour, which is how a
    # neutral grey network turns bright red for no stated reason.
    flat_color = "#39474f"
    if es.get("edgeColorByNodeTransition"):
        color_mode_edge = "node-transitioning"
        flat_color = str(es.get("edgeColorByNodeTransitionColor") or flat_color)

    edge = EdgeStyle(
        color_mode=color_mode_edge,
        color=flat_color,
        direction_mode=direction,
        start_color=str(es.get("directionStartColor") or "#ff0000"),
        end_color=str(es.get("directionEndColor") or "#0000ff"),
        width=float(es.get("size", 1) or 1) * 0.55,
        width_by_weight=bool(es.get("thicknessByWeight", False)),
    )

    hemis: tuple[str, ...] = ("left", "right")
    mode = str(app.get("brainSurfaceMode", "both")).lower()
    if mode in {"left", "right"}:
        hemis = (mode,)

    brain = BrainStyle(
        color=str(ss.get("color") or "#d8d8d8"),
        opacity=float(ss.get("opacity", 0.55)),
        hemispheres=hemis,
        split_mm=20.0 if ds.get("split") else 0.0,
    )

    view = _MARVL_VIEWS.get(str(ds.get("mode", "top")).lower(), "dorsal")
    edge_count = app.get("edgeCount")

    for key in ("layout2d", "bundle2d", "scale2d", "showingTopologyNetwork"):
        if app.get(key) not in (None, "", "none", False, 0):
            log.info("%s: ignoring 2D setting %s=%r", path.name, key, app[key])
    if ds.get("rotation"):
        log.info("%s: ignoring 'rotation' (a still figure does not spin)",
                 path.name)

    return NetworkStyle(
        node=node,
        edge=edge,
        brain=brain,
        views=(view,),
        edge_count=int(edge_count) if edge_count else None,
        title="",
    )


def _ramp_colormap(low: str, high: str):
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(
        "cifti_state.network.ramp", [_as_rgb(low), _as_rgb(high)]
    )


def signed_colormap(style: "EdgeStyle"):
    """The ramp ``color_mode="signed"`` uses, as a matplotlib colour map.

    Five stops over ``[-1, 1]``: the dark negative colour, its faded form, a
    near-white centre, the faded positive colour, the dark positive one.  Built
    from the style's own colours so the figure and any legend drawn beside it
    agree by construction.

    It is deliberately *not* a smooth diverging ramp through a neutral hue.
    With a symmetric ramp the weakest positive edge in a set whose weights span
    0.05 to 0.19 lands only a quarter of the way towards red and comes out
    purple -- the opposite of what it means.  Each sign gets its own ramp
    instead, so a positive edge is always red and a negative one always purple,
    and depth within that colour is what carries the strength.
    """
    from matplotlib.colors import LinearSegmentedColormap

    negative = _as_rgb(style.negative_color)
    positive = _as_rgb(style.positive_color)
    fade = float(np.clip(style.signed_fade, 0.0, 1.0))
    towards_white = lambda rgb: tuple(c + (1.0 - c) * fade for c in rgb)
    return LinearSegmentedColormap.from_list(
        "cifti_state.network.signed",
        [
            (0.0, negative),
            (0.4999, towards_white(negative)),
            (0.5, (1.0, 1.0, 1.0)),
            (0.5001, towards_white(positive)),
            (1.0, positive),
        ],
    )


def save_marvl_settings(style: NetworkStyle, path: Path | str, *,
                        size_to_mm: float = MARVL_SIZE_TO_MM) -> Path:
    """Write a style back out in NeuroMArVL's settings format."""
    path = Path(path)
    view = next(
        (k for k, v in _MARVL_VIEWS.items() if v == (style.views[0] if style.views else "dorsal")),
        "top",
    )
    payload = {
        "edgeSettings": {
            "colorBy": style.edge.color_mode
            if style.edge.color_mode in _MARVL_EDGE_COLOR else "none",
            "size": round(style.edge.width / 0.55, 4),
            "thicknessByWeight": bool(style.edge.width_by_weight),
            "directionMode": style.edge.direction_mode,
            "directionStartColor": style.edge.start_color,
            "directionEndColor": style.edge.end_color,
            "edgeColorByNodeTransition":
                style.edge.color_mode == "node-transitioning",
            "edgeColorByNodeTransitionColor": style.edge.color,
        },
        "nodeSettings": {
            "nodeSizeOrColor": "node-size",
            "nodeSizeAttribute": style.node.size_by or "",
            "nodeSizeMin": round(style.node.size_range[0] / size_to_mm, 4),
            "nodeSizeMax": round(style.node.size_range[1] / size_to_mm, 4),
            "nodeColorAttribute": style.node.color_by or "",
            "nodeColorMode": style.node.color_mode,
            "nodeColorDiscrete": list(style.node.discrete_colors),
        },
        "surfaceSettings": {
            "opacity": style.brain.opacity,
            "color": style.brain.color,
        },
        "displaySettings": {
            "mode": view.capitalize(),
            "labels": bool(style.node.labels),
            "split": style.brain.split_mm > 0,
            "rotation": False,
        },
        "saveApps": [
            {
                "brainSurfaceMode":
                    "both" if len(style.brain.hemispheres) > 1
                    else style.brain.hemispheres[0],
                "edgeCount": style.edge_count or 0,
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# turning a style plus data into numbers the renderer draws
# --------------------------------------------------------------------------- #


def node_radii(data: NetworkData, style: NodeStyle) -> np.ndarray:
    """Sphere radius in millimetres for every node."""
    if not style.size_by:
        return np.full(data.n_nodes, float(style.size))
    values = data.attribute(style.size_by)
    lo_mm, hi_mm = (float(v) for v in style.size_range)
    if style.size_from_zero:
        top = float(np.nanmax(np.abs(values))) or 1.0
        return np.clip(np.abs(values) / top, 0.0, 1.0) * hi_mm
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    if not np.isfinite(low) or high <= low:
        return np.full(data.n_nodes, (lo_mm + hi_mm) / 2.0)
    fraction = (values - low) / (high - low)
    return lo_mm + fraction * (hi_mm - lo_mm)


def node_colors(data: NetworkData, style: NodeStyle) -> np.ndarray:
    """An ``(n, 3)`` array of RGB in 0-1 for every node."""
    if not style.color_by:
        return np.tile(_as_rgb(style.color), (data.n_nodes, 1))
    values = data.attribute(style.color_by)
    mode = style.resolved_color_mode(values)
    if mode == "discrete":
        levels = np.unique(values[np.isfinite(values)])
        palette = list(style.discrete_colors) or list(DEFAULT_GROUP_COLORS)
        if len(palette) < levels.size:
            log.warning(
                "attribute %r has %d distinct values but only %d colours were "
                "given; the palette repeats",
                style.color_by, levels.size, len(palette),
            )
        out = np.zeros((data.n_nodes, 3))
        for index, level in enumerate(levels):
            out[values == level] = _as_rgb(palette[index % len(palette)])
        return out

    from matplotlib import colormaps
    from matplotlib.colors import Colormap, Normalize

    cmap = style.colormap
    if isinstance(cmap, str):
        cmap = colormaps[cmap]
    assert isinstance(cmap, Colormap)
    lo, hi = style.continuous_range or (
        float(np.nanmin(values)), float(np.nanmax(values))
    )
    if hi <= lo:
        hi = lo + 1.0
    return np.asarray(cmap(Normalize(lo, hi)(values)))[:, :3]


def edge_radii(weights: np.ndarray, style: EdgeStyle) -> np.ndarray:
    """Tube radius in millimetres for every edge."""
    weights = np.asarray(weights, dtype=float)
    if not style.width_by_weight or weights.size == 0:
        return np.full(weights.shape, float(style.width))
    magnitude = np.abs(weights)
    low, high = float(magnitude.min()), float(magnitude.max())
    lo_mm, hi_mm = (float(v) for v in style.width_range)
    if high <= low:
        return np.full(weights.shape, (lo_mm + hi_mm) / 2.0)
    return lo_mm + (magnitude - low) / (high - low) * (hi_mm - lo_mm)


def edge_colors(
    weights: np.ndarray,
    source: np.ndarray,
    target: np.ndarray,
    node_rgb: np.ndarray,
    style: EdgeStyle,
) -> tuple[np.ndarray, np.ndarray]:
    """The colour at each edge's source and target end.

    Returns two ``(n_edges, 3)`` arrays.  They are equal unless the edge is
    meant to change colour along its length, which is how ``gradient`` shows
    direction and how ``node-transitioning`` shows which node an edge came
    from.  The renderer interpolates between them.
    """
    weights = np.asarray(weights, dtype=float)
    n = weights.size
    if n == 0:
        empty = np.zeros((0, 3))
        return empty, empty

    if style.color_mode == "node":
        start = node_rgb[source]
        end = start.copy()
    elif style.color_mode == "node-transitioning":
        start = node_rgb[source]
        end = node_rgb[target]
    elif style.color_mode == "sign":
        start = np.where(
            (weights >= 0)[:, None],
            np.tile(_as_rgb(style.positive_color), (n, 1)),
            np.tile(_as_rgb(style.negative_color), (n, 1)),
        )
        end = start.copy()
    elif style.color_mode == "weight":
        from matplotlib import colormaps
        from matplotlib.colors import Normalize

        cmap = colormaps[style.colormap] if isinstance(style.colormap, str) \
            else style.colormap
        lo, hi = style.color_range or (
            float(np.abs(weights).min()), float(np.abs(weights).max())
        )
        if hi <= lo:
            hi = lo + 1.0
        start = np.asarray(cmap(Normalize(lo, hi)(np.abs(weights))))[:, :3]
        end = start.copy()
    elif style.color_mode == "signed":
        from matplotlib.colors import Normalize

        cmap = signed_colormap(style)
        limit = float(np.abs(weights).max())
        if style.color_range is not None:
            limit = max(abs(float(v)) for v in style.color_range)
        if limit <= 0:
            limit = 1.0
        # Symmetric about zero on purpose.  A ramp fitted to the observed range
        # would put its midpoint at the mean weight, so the colour that reads
        # as "no connection" would land on some arbitrary positive value and a
        # figure of entirely positive edges would come out half purple.
        start = np.asarray(cmap(Normalize(-limit, limit)(weights)))[:, :3]
        end = start.copy()
    else:
        start = np.tile(_as_rgb(style.color), (n, 1))
        end = start.copy()

    # The direction ramp overrides the colour mode, because that is what the
    # user asked for when they chose it: it is a way of showing direction, and
    # a gradient that also varies by weight shows neither clearly.
    if style.direction_mode == "gradient":
        start = np.tile(_as_rgb(style.start_color), (n, 1))
        end = np.tile(_as_rgb(style.end_color), (n, 1))

    return start, end
