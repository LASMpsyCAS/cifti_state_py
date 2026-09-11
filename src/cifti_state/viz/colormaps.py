"""Colour maps, including Workbench-alike palettes.

Registered under ``cifti_state.*`` names in matplotlib so they can be referred
to by string anywhere a colormap is accepted.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, ListedColormap, Normalize

__all__ = [
    "WORKBENCH_HOT",
    "WORKBENCH_COOL",
    "WORKBENCH_HOT_COOL",
    "register",
    "get_colormap",
    "cluster_colormap",
    "solid_colormap",
    "symmetric_range",
]

#: Workbench's ROY-BIG-BL positive half (dark red -> red -> orange -> yellow).
WORKBENCH_HOT = LinearSegmentedColormap.from_list(
    "cifti_state.workbench_hot",
    [
        (0.00, "#600000"),
        (0.25, "#b40000"),
        (0.50, "#ff2800"),
        (0.75, "#ff9000"),
        (1.00, "#ffff00"),
    ],
)

#: The matching negative half (dark blue -> blue -> cyan -> pale cyan).
WORKBENCH_COOL = LinearSegmentedColormap.from_list(
    "cifti_state.workbench_cool",
    [
        (0.00, "#00ffff"),
        (0.25, "#00b4ff"),
        (0.50, "#0050ff"),
        (0.75, "#0000c8"),
        (1.00, "#000060"),
    ],
)

#: Full diverging map: cool for negatives, hot for positives.
WORKBENCH_HOT_COOL = LinearSegmentedColormap.from_list(
    "cifti_state.workbench_hot_cool",
    [
        (0.000, "#00ffff"),
        (0.125, "#00b4ff"),
        (0.250, "#0050ff"),
        (0.375, "#0000c8"),
        (0.500, "#000000"),
        (0.625, "#b40000"),
        (0.750, "#ff2800"),
        (0.875, "#ff9000"),
        (1.000, "#ffff00"),
    ],
)

_BUILTIN = {
    "workbench_hot": WORKBENCH_HOT,
    "workbench_cool": WORKBENCH_COOL,
    "workbench_hot_cool": WORKBENCH_HOT_COOL,
}


def register() -> None:
    """Register the package colormaps with matplotlib (idempotent)."""
    for name, cmap in _BUILTIN.items():
        for key in (name, f"cifti_state.{name}"):
            if key not in colormaps:
                try:
                    colormaps.register(cmap, name=key)
                except ValueError:  # pragma: no cover - already registered
                    pass


def get_colormap(name: str):
    """Look up a colormap by package name or by any matplotlib name."""
    register()
    if name in _BUILTIN:
        return _BUILTIN[name]
    return colormaps[name]


def solid_colormap(color: str, name: str = "cifti_state.solid"):
    """A colormap where every non-zero value maps to a single colour.

    Used for cluster outlines, where the layer only needs one ink colour.
    """
    from matplotlib.colors import to_rgba

    rgba = to_rgba(color)
    return ListedColormap([rgba, rgba], name=name)


def cluster_colormap(n_clusters: int, *, seed: int = 0):
    """A qualitative colormap for discrete cluster ids.

    Index 0 is transparent-ish grey so that unassigned vertices stay neutral;
    ids 1..n get distinguishable colours.
    """
    if n_clusters <= 0:
        return ListedColormap([(0.7, 0.7, 0.7, 1.0)], name="cifti_state.clusters")

    base = []
    for source in ("tab20", "tab20b", "tab20c"):
        cmap = colormaps[source]
        base.extend(cmap(np.linspace(0, 1, cmap.N)))
    base = np.asarray(base)

    rng = np.random.default_rng(seed)
    if n_clusters <= len(base):
        idx = np.arange(n_clusters)
        colors = base[idx]
    else:
        reps = int(np.ceil(n_clusters / len(base)))
        colors = np.vstack([base] * reps)[:n_clusters]
        jitter = rng.uniform(-0.08, 0.08, size=(n_clusters, 3))
        colors[:, :3] = np.clip(colors[:, :3] + jitter, 0, 1)

    return ListedColormap(
        np.vstack([[0.72, 0.72, 0.72, 1.0], colors]), name="cifti_state.clusters"
    )


def symmetric_range(
    values: np.ndarray,
    *,
    percentile: float = 99.0,
    minimum: Optional[float] = None,
) -> tuple[float, float]:
    """A symmetric ``(vmin, vmax)`` robust to outliers."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite) & (finite != 0)]
    if finite.size == 0:
        return (-1.0, 1.0)
    limit = float(np.percentile(np.abs(finite), percentile))
    if minimum is not None:
        limit = max(limit, float(minimum))
    if limit <= 0:
        limit = float(np.abs(finite).max()) or 1.0
    return (-limit, limit)


def normalizer(vmin: float, vmax: float) -> Normalize:
    return Normalize(vmin=vmin, vmax=vmax)
