"""View layout presets for surfplot.

A layout fixes which hemispheres appear, which views are drawn, and how they
are arranged.  Names are stable so they can be offered directly in a GUI
drop-down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["Layout", "LAYOUTS", "get_layout", "layout_names"]


@dataclass(frozen=True)
class Layout:
    name: str
    description: str
    hemispheres: tuple[str, ...]        #: subset of ("left", "right")
    views: tuple[str, ...]              #: lateral | medial | dorsal | ventral | anterior | posterior
    mirror_views: bool = True           #: surfplot's mirror_views
    flip: bool = False                  #: surfplot's flip
    layout_style: str = "grid"          #: "grid" | "row" | "column"
    size: Optional[tuple[int, int]] = None
    zoom: Optional[float] = None


LAYOUTS: dict[str, Layout] = {
    "grid_4": Layout(
        name="grid_4",
        description="Lateral and medial, both hemispheres, 2x2",
        hemispheres=("left", "right"),
        views=("lateral", "medial"),
        layout_style="grid",
        size=(1000, 700),
        zoom=1.25,
    ),
    "row_4": Layout(
        name="row_4",
        description="Lateral and medial, both hemispheres, single row",
        hemispheres=("left", "right"),
        views=("lateral", "medial"),
        layout_style="row",
        size=(1600, 350),
        zoom=1.3,
    ),
    "grid_8": Layout(
        name="grid_8",
        description="Lateral, medial, dorsal and ventral, both hemispheres",
        hemispheres=("left", "right"),
        views=("lateral", "medial", "dorsal", "ventral"),
        layout_style="grid",
        size=(1100, 1200),
        zoom=1.2,
    ),
    "dorsal_ventral": Layout(
        name="dorsal_ventral",
        description="Dorsal and ventral views only",
        hemispheres=("left", "right"),
        views=("dorsal", "ventral"),
        layout_style="grid",
        size=(900, 700),
        zoom=1.2,
    ),
    "left_only": Layout(
        name="left_only",
        description="Left hemisphere, lateral and medial",
        hemispheres=("left",),
        views=("lateral", "medial"),
        layout_style="row",
        size=(900, 350),
        zoom=1.3,
    ),
    "right_only": Layout(
        name="right_only",
        description="Right hemisphere, lateral and medial",
        hemispheres=("right",),
        views=("lateral", "medial"),
        layout_style="row",
        size=(900, 350),
        zoom=1.3,
    ),
    "flat": Layout(
        name="flat",
        description="Flattened surfaces (requires the flat template surfaces)",
        hemispheres=("left", "right"),
        views=("dorsal",),
        mirror_views=False,
        layout_style="row",
        size=(1400, 600),
        zoom=1.0,
    ),
}


def get_layout(name: str) -> Layout:
    try:
        return LAYOUTS[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown layout {name!r}; available: {', '.join(sorted(LAYOUTS))}"
        ) from exc


def layout_names() -> list[str]:
    return sorted(LAYOUTS)
