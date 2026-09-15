"""Brain-network figures: a glass brain, nodes, and directed edges.

This package draws a node-and-edge network on the fs_LR 32k cortical surface.
It reads the four plain-text files a NeuroMArVL dataset is made of --
coordinates, labels, a connectivity matrix and a node attribute table -- and
reproduces that tool's edge-selection rule exactly, so a figure you tuned in
the browser can be re-made here, in batch, from a script or the command line.

    from cifti_state.config import load_settings
    from cifti_state.network import load_network, render_network

    data = load_network_dir("Neuromarvl_input/taskaverage")
    render_network(data, load_settings(), out="taskaverage.png",
                   edge_count=16, size_by="in_degree")

The pieces are separate on purpose:

``data``       reading and validating a dataset
``threshold``  which edges to draw
``style``      how nodes and edges look, including NeuroMArVL settings files
``scene``      assembling the 3D scene
``render``     cameras, panels and writing the file
"""

from __future__ import annotations

from .data import (
    NetworkData,
    NetworkError,
    load_network,
    load_network_dir,
    read_attributes,
    read_coordinates,
    read_labels,
    read_matrix,
)
from .interactive import command_line_for, explore
from .qtwindow import QtMissing, open_window
from .render import NetworkFigure, render_network
from .scene import NetworkScene, build_network_scene
from .style import (
    BrainStyle,
    EdgeStyle,
    NodeStyle,
    NetworkStyle,
    load_marvl_settings,
    save_marvl_settings,
)
from .threshold import (
    EdgeSet,
    max_positive_edge_count,
    pair_maxima,
    select_edges,
    threshold_for_edge_count,
)

__all__ = [
    "NetworkData",
    "NetworkError",
    "load_network",
    "load_network_dir",
    "read_attributes",
    "read_coordinates",
    "read_labels",
    "read_matrix",
    "EdgeSet",
    "pair_maxima",
    "threshold_for_edge_count",
    "max_positive_edge_count",
    "select_edges",
    "NodeStyle",
    "EdgeStyle",
    "BrainStyle",
    "NetworkStyle",
    "load_marvl_settings",
    "save_marvl_settings",
    "NetworkScene",
    "build_network_scene",
    "NetworkFigure",
    "render_network",
    "explore",
    "command_line_for",
    "open_window",
    "QtMissing",
]
