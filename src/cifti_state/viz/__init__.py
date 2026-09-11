"""Visualisation: surfplot figures, interactive PyVista scenes, colours, layouts.

Importing this package pulls in neither surfplot nor PyVista; both are imported
lazily inside their own modules, so the analysis side works on machines without
a rendering stack.
"""

from .colormaps import cluster_colormap, get_colormap, register, solid_colormap, symmetric_range
from .layouts import LAYOUTS, Layout, get_layout, layout_names

__all__ = [
    "LAYOUTS",
    "Layout",
    "cluster_colormap",
    "get_colormap",
    "get_layout",
    "layout_names",
    "register",
    "solid_colormap",
    "symmetric_range",
]
