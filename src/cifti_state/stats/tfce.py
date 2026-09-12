"""Threshold-free cluster enhancement, as PALM computes it.

Cluster inference makes you pick a cluster-forming threshold before you look at
the data, and the answer moves when you change it: a low threshold favours large
diffuse effects, a high one favours focal peaks, and nothing tells you which is
right for an effect you have not seen yet.  TFCE removes the choice by
integrating over all of them.  Each vertex is scored by

    TFCE(v) = ∫ e(h)^E · h^H dh

where ``e(h)`` is the extent of the supra-threshold cluster containing *v* at
height *h*.  A vertex is rewarded for being high (``h^H``) and for belonging to
something big (``e(h)^E``), and the integral means a broad low bump and a
narrow tall spike can both win.  The score has no null distribution of its own,
so it is only useful inside a permutation test -- which is why this module sits
next to :mod:`cifti_state.stats.permutation` and is driven from there.

**Extent is area, not vertex count.**  On a surface the vertices are not
evenly spaced, so counting them would let a densely-tessellated region beat a
sparsely-tessellated one of the same real size.  The extent used here is the
summed vertex area in mm², exactly as PALM does it.

Agreement with PALM
-------------------
This is a reimplementation of ``palm_tfce.m`` (vertexwise branch) and is tested
against it directly: the real PALM function is run in Octave over a range of
meshes, masks, per-vertex areas and H/E settings, and the two agree to about
1e-15 relative -- machine precision.  The details that have to match, and that
are easy to get wrong:

* the step ladder is ``h = dh, 2dh, …, max`` with ``dh = max/100`` -- one
  hundred steps scaled to *this* map, so the ladder differs between
  permutations, and the result is multiplied by ``dh`` at the end;
* the comparison is ``value >= h``, not ``>``;
* clusters are formed on the mesh's edge adjacency, and their extent is the
  sum of the member vertices' areas;
* PALM's defaults are ``H=2, E=0.5``; its ``-tfce2D`` shortcut, which is what
  its documentation suggests for surfaces, is ``H=2, E=1``;
* for a two-tailed test the score is computed on ``|t|``, as PALM does before
  it calls this.

Both hemispheres are treated as one search region -- one ``dh`` over the pair,
and clusters that cannot cross the midline -- which is the same search region
the random field correction uses.

One deliberate departure: PALM also accepts a fixed ``-tfce_dh``, but its
surface branch never assigns the ``dh`` it multiplies by at the end, so that
path raises ``'dh' undefined`` before returning.  A fixed step is supported
here, implemented the way PALM's *volume* branch does it -- there is simply no
PALM surface output to agree with.

Reference: Smith SM, Nichols TE (2009). *Threshold-free cluster enhancement:
addressing problems of smoothing, threshold dependence and localisation in
cluster inference.* NeuroImage 44:83-98.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = ["TFCESettings", "TFCEEngine", "tfce", "PALM_DEFAULT", "PALM_2D"]


@dataclass(frozen=True)
class TFCESettings:
    """The four numbers that define a TFCE score.

    *height* and *extent* are PALM's ``H`` and ``E``.  *dh* of 0 means the
    automatic ladder -- ``max/steps``, rescaled to every map it sees, which is
    what PALM does by default and therefore what has to be done inside a
    permutation loop for the numbers to be comparable.
    """

    height: float = 2.0        #: H -- the exponent on the threshold
    extent: float = 0.5        #: E -- the exponent on the cluster area
    dh: float = 0.0            #: 0 = automatic (max / steps)
    steps: int = 100           #: how many steps the automatic ladder uses

    def describe(self) -> str:
        step = "auto (max/%d)" % self.steps if self.dh == 0 else f"{self.dh:g}"
        return f"H={self.height:g}, E={self.extent:g}, dh={step}"

    def to_dict(self) -> dict:
        return {
            "H": float(self.height),
            "E": float(self.extent),
            "dh": float(self.dh),
            "steps": int(self.steps),
        }


#: PALM's defaults (``H=2, E=0.5``).
PALM_DEFAULT = TFCESettings()

#: PALM's ``-tfce2D`` shortcut (``H=2, E=1``), suggested for surface data.
PALM_2D = TFCESettings(height=2.0, extent=1.0)


class TFCEEngine:
    """TFCE over one fixed mesh, set up once and reused.

    The mesh, the vertex areas and the analysis mask do not change between
    permutations; only the statistic does.  Everything that depends on the mesh
    alone is therefore computed here, at construction, and the per-permutation
    call is left with nothing but the thresholding and the labelling.
    """

    def __init__(
        self,
        adjacency: csr_matrix,
        area: np.ndarray,
        *,
        mask: Optional[np.ndarray] = None,
        settings: TFCESettings = PALM_DEFAULT,
    ):
        adjacency = adjacency.tocoo()
        self.n_vertices = int(adjacency.shape[0])
        self.area = np.asarray(area, dtype=np.float64)
        if self.area.size != self.n_vertices:
            raise ValueError(
                f"the adjacency covers {self.n_vertices} vertices but "
                f"{self.area.size} areas were given"
            )
        self.mask = (
            np.ones(self.n_vertices, dtype=bool) if mask is None
            else np.asarray(mask, dtype=bool)
        )
        self.settings = settings

        # Keep the edge list rather than the matrix: selecting the edges whose
        # two ends are both above threshold is one boolean AND, where slicing a
        # sparse matrix down to the active vertices would cost far more.
        rows, cols = adjacency.row, adjacency.col
        upper = rows < cols
        self._edge_a = rows[upper].astype(np.int32)
        self._edge_b = cols[upper].astype(np.int32)
        # Scratch, reused every step: writing compact indices into a
        # full-length array beats allocating one per threshold.
        self._position = np.zeros(self.n_vertices, dtype=np.int32)

    # ---------------------------------------------------------------- #

    def __call__(self, values: np.ndarray) -> np.ndarray:
        """The TFCE score of one statistic map."""
        values = np.asarray(values, dtype=np.float64)
        if values.size != self.n_vertices:
            raise ValueError(
                f"the map has {values.size} vertices but the mesh has "
                f"{self.n_vertices}"
            )

        # PALM injects the statistic into a zeroed array and thresholds that,
        # so anything outside the mask is 0 and can never be above a positive
        # step. Reproduced exactly, rather than by masking the comparison.
        injected = np.zeros(self.n_vertices, dtype=np.float64)
        injected[self.mask] = values[self.mask]

        top = float(injected.max())
        out = np.zeros(self.n_vertices, dtype=np.float64)
        if not np.isfinite(top) or top <= 0.0:
            # Nothing is above any positive height: PALM's loop body never
            # runs and the result is all zeros.
            return out

        settings = self.settings
        if settings.dh > 0:
            dh = float(settings.dh)
            ladder = np.arange(1, int(np.floor(top / dh)) + 1) * dh
        else:
            dh = top / settings.steps
            # linspace, not arange: the last step has to land exactly on the
            # maximum, which is what MATLAB's colon operator guarantees and
            # what decides whether the peak vertex gets its final contribution.
            ladder = np.linspace(dh, top, settings.steps)

        # As the threshold rises the supra-threshold set only shrinks, so if
        # both vertices and edges are sorted by height once, the set that
        # survives any given step is a *prefix* of that order. Each step then
        # costs what its own cluster costs, instead of a scan over the whole
        # cortex a hundred times over.
        vertex_order = np.argsort(-injected, kind="stable")
        vertex_heights = -injected[vertex_order]

        # An edge is present exactly when its lower end is above the threshold.
        edge_height = np.minimum(injected[self._edge_a], injected[self._edge_b])
        edge_order = np.argsort(-edge_height, kind="stable")
        edge_heights = -edge_height[edge_order]
        edge_a = self._edge_a[edge_order]
        edge_b = self._edge_b[edge_order]

        height = settings.height
        extent = settings.extent
        position = self._position

        for h in ladder:
            n_active = int(np.searchsorted(vertex_heights, -h, side="right"))
            if n_active == 0:
                # The ladder only rises from here.
                break
            index = vertex_order[:n_active]
            n_edges = int(np.searchsorted(edge_heights, -h, side="right"))

            position[index] = np.arange(n_active, dtype=np.int32)
            graph = csr_matrix(
                (
                    np.ones(n_edges, dtype=np.int8),
                    (position[edge_a[:n_edges]], position[edge_b[:n_edges]]),
                ),
                shape=(n_active, n_active),
            )
            _, labels = connected_components(graph, directed=False)
            areas = np.bincount(labels, weights=self.area[index])
            out[index] += areas[labels] ** extent * h ** height

        return out * dh


def tfce(
    values: np.ndarray,
    adjacency: csr_matrix,
    area: np.ndarray,
    *,
    mask: Optional[np.ndarray] = None,
    settings: TFCESettings = PALM_DEFAULT,
) -> np.ndarray:
    """One-shot TFCE. Build a :class:`TFCEEngine` instead if you need many."""
    engine = TFCEEngine(adjacency, area, mask=mask, settings=settings)
    return engine(values)
