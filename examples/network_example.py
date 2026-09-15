"""Draw a brain network on a glass brain, end to end, with no data needed.

Run it and it writes six figures into ``results/network_example/``.  There is
no dataset to download: the nodes are picked off the bundled fs_LR 32k surface
itself, so the coordinates are real cortical positions in the same space as the
mesh, and the directed matrix is generated with a fixed seed.

    python examples/network_example.py

To draw your own network instead, replace :func:`make_example_dataset` with a
call to ``load_network_dir`` on a directory holding ``coordinates.txt``,
``labels.txt``, ``matrix_*.txt`` and ``attributes_*.txt`` -- everything below
stays the same.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import numpy as np

from cifti_state.config import load_settings
from cifti_state.io.surface import load_surface
from cifti_state.network import (
    BrainStyle,
    EdgeStyle,
    NetworkData,
    NetworkStyle,
    NodeStyle,
    render_network,
    save_marvl_settings,
    select_edges,
)
from cifti_state.network.threshold import max_positive_edge_count

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "results" / "network_example"

LABELS = (
    "IFG", "preSMA", "dlPFC", "M1", "S1",
    "STG", "IPL", "V1", "Fusiform",
)
#: Which of three systems each node belongs to; the colours follow.
GROUPS = np.array([1, 1, 1, 2, 2, 3, 2, 3, 1])


def make_example_dataset(settings) -> NetworkData:
    """Nine nodes on the real left hemisphere, with a made-up directed matrix.

    The positions are taken from the bundled surface rather than typed in, so
    they are guaranteed to be in the mesh's own space -- which is the thing
    that most often goes wrong with a real dataset, and the thing this example
    should not accidentally demonstrate.
    """
    path = settings.surface_for("left", "midthickness")
    if path is None:
        raise SystemExit(
            "No left midthickness surface is configured. Run\n"
            "    cifti-state --config example_data/example.yaml check\n"
            "or set CIFTI_STATE_CONFIG to example_data/example.yaml."
        )
    surface = load_surface(path, hemisphere="left", kind="midthickness")

    # Nine anatomical targets, roughly. Each is snapped to the nearest real
    # vertex, then pulled a little towards the middle of the hemisphere so the
    # spheres sit inside the shell rather than on it.
    targets = np.array(
        [
            [-48.0,  20.0,  10.0],   # IFG
            [ -6.0,  10.0,  60.0],   # preSMA
            [-42.0,  36.0,  28.0],   # dlPFC
            [-38.0, -22.0,  56.0],   # M1
            [-46.0, -28.0,  48.0],   # S1
            [-58.0, -20.0,   2.0],   # STG
            [-44.0, -56.0,  44.0],   # IPL
            [-10.0, -92.0,   4.0],   # V1
            [-38.0, -52.0, -18.0],   # Fusiform
        ]
    )
    coords = np.empty_like(targets)
    centre = surface.coords.mean(axis=0)
    for index, target in enumerate(targets):
        nearest = int(np.argmin(((surface.coords - target) ** 2).sum(axis=1)))
        coords[index] = surface.coords[nearest] * 0.88 + centre * 0.12

    rng = np.random.default_rng(20260915)
    n = len(LABELS)
    # A directed matrix with real asymmetry: a forward "flow" from posterior to
    # anterior that is stronger than the return path, plus noise.
    order = np.argsort(coords[:, 1])            # posterior first
    rank = np.empty(n, dtype=float)
    rank[order] = np.arange(n)
    flow = (rank[None, :] - rank[:, None]) / n
    matrix = 0.25 * np.clip(flow, 0, None) + rng.normal(0, 0.06, (n, n))
    np.fill_diagonal(matrix, 0.0)

    attributes = {
        "group_id": GROUPS.astype(float),
        "in_degree": np.abs(matrix).sum(axis=0),
        "out_degree": np.abs(matrix).sum(axis=1),
    }
    return NetworkData(
        coordinates=coords, matrix=matrix, labels=LABELS,
        attributes=attributes, name="example",
    )


def main() -> int:
    config = os.environ.get("CIFTI_STATE_CONFIG") or str(
        ROOT / "example_data" / "example.yaml"
    )
    settings = load_settings(config)
    data = make_example_dataset(settings)
    OUT.mkdir(parents=True, exist_ok=True)

    print(data.describe())
    print(f"asymmetry {data.asymmetry:.2f}  "
          f"(0 would mean the matrix is really undirected)")
    limit = max_positive_edge_count(data.matrix)
    print(f"largest edge_count with a positive cutoff: {limit}")
    for k in (6, 10, 14, 18):
        print(f"  k={k:>2}  ->  {len(select_edges(data.matrix, edge_count=k)):>2} arrows")

    base = NetworkStyle(
        node=NodeStyle(size_by="out_degree", color_by="group_id",
                       size_range=(1.6, 6.0), labels=True),
        edge=EdgeStyle(direction_mode="arrow"),
        brain=BrainStyle(hemispheres=("left", "right"), opacity=0.5),
    )

    # 1. The default: three views, arrows, one colour.
    render_network(
        data, settings, style=base, out=OUT / "1_three_views.png",
        views=["left", "dorsal", "anterior"], edge_count=12,
        title="Directed network, three views",
    )

    # 2. Edges coloured by where they come from and fading to where they go.
    render_network(
        data, settings, out=OUT / "2_node_transition.png",
        style=replace(base, edge=replace(base.edge,
                                         color_mode="node-transitioning")),
        views=["left"], edge_count=12,
        title="Edge colour carries the direction",
    )

    # 3. Width and colour by weight; the strong connections stand out.
    render_network(
        data, settings, out=OUT / "3_by_weight.png",
        style=replace(base, edge=replace(base.edge, color_mode="weight",
                                         width_by_weight=True,
                                         colormap="viridis")),
        views=["left"], edge_count=12,
        title="Width and colour by weight",
    )

    # 4. Open the threshold past the point where the cutoff turns negative,
    #    and colour by sign so the figure still says what it is showing.
    render_network(
        data, settings, out=OUT / "4_negative_edges.png",
        style=replace(base, edge=replace(base.edge, color_mode="sign")),
        views=["left"], edge_count=min(limit + 6, len(LABELS) * (len(LABELS) - 1) // 2),
        title="Past the positive cutoff, with the sign shown",
    )

    # 5. Hemispheres pulled apart, both drawn.
    render_network(
        data, settings, out=OUT / "5_split.png",
        style=replace(base, brain=replace(base.brain, split_mm=36.0)),
        views=["dorsal"], edge_count=12, title="Split hemispheres",
    )

    # 6. Six views, no labels, a tighter network -- the overview figure.
    render_network(
        data, settings, out=OUT / "6_all_views.png",
        style=replace(base, node=replace(base.node, labels=False)),
        views=["left", "right", "anterior", "posterior", "dorsal", "ventral"],
        edge_count=8, columns=3, title="Every view",
    )

    save_marvl_settings(base, OUT / "style.json")
    print(f"\nwrote six figures and style.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
