"""How smooth the field is, measured on the residuals.

Random field theory needs to know how much *independent* information a search
region holds.  A map smoothed to a 10 mm kernel has far fewer effectively
independent locations than its vertex count suggests, and a correction that
ignores this is wildly conservative; a map that is rougher than assumed gets a
correction that is too lenient.  The currency is the **resel** -- a patch of
surface one FWHM across -- and this module counts them.

The measurement is made on the model's residuals, following SurfStat.  For each
edge of the mesh::

    u_i = residual_i / ||residual||        (per vertex, unit length)
    resl(edge) = sum_i (u_i[a] - u_i[b])^2

which is an estimate of the variance of the field's increment along that edge:
small where neighbouring vertices are highly correlated (a smooth field), large
where they are not.  Note that the vertex *coordinates* never enter -- only
which vertices are neighbours.  The metric comes entirely from the data, so a
field that is smoother in one region than another is described correctly rather
than being forced to a single global FWHM.

From the per-edge roughness come the Lipschitz-Killing curvatures of the masked
mesh, and from those the resel counts:

===========  ================================================================
``R[0]``     the Euler characteristic of the search region
``R[1]``     half its boundary length, in resels
``R[2]``     its area, in resels -- the one that dominates the correction
===========  ================================================================

Reference: Worsley KJ, Andermann M, Koulis T, MacDonald D, Evans AC (1999).
*Detecting changes in nonisotropic images.* Human Brain Mapping 8:98-101.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "ReselEstimate",
    "SurfaceTopology",
    "build_topology",
    "edge_roughness",
    "compute_resels",
    "FOUR_LOG_2",
]

#: 4 log 2 -- the constant that turns a variance of increments into an FWHM.
FOUR_LOG_2 = 4.0 * np.log(2.0)


@dataclass
class SurfaceTopology:
    """The triangles and edges of the analysis mesh, and nothing else.

    Both hemispheres live in one object with the right hemisphere's indices
    offset, so the search region is the whole cortex: the resel counts add and
    the Euler characteristic adds, which is exactly what a two-component search
    region means.
    """

    triangles: np.ndarray        #: (n_triangles, 3) int, sorted within each row
    edges: np.ndarray            #: (n_edges, 2) int, sorted and unique
    n_vertices: int
    triangle_edges: np.ndarray   #: (n_triangles, 3) index into edges: 01, 02, 12

    @property
    def n_edges(self) -> int:
        return int(self.edges.shape[0])

    @property
    def n_triangles(self) -> int:
        return int(self.triangles.shape[0])


def build_topology(
    faces_left: np.ndarray,
    faces_right: Optional[np.ndarray] = None,
    *,
    n_left: Optional[int] = None,
    n_vertices: Optional[int] = None,
) -> SurfaceTopology:
    """Combine the hemisphere face lists into one indexed mesh."""
    left = np.asarray(faces_left, dtype=np.int64)
    if faces_right is None:
        triangles = left
        total = int(n_vertices or (left.max() + 1))
    else:
        right = np.asarray(faces_right, dtype=np.int64)
        if n_left is None:
            raise ValueError("n_left is required when both hemispheres are given")
        triangles = np.vstack([left, right + int(n_left)])
        total = int(n_vertices or (int(n_left) + int(right.max()) + 1))

    triangles = np.sort(triangles, axis=1)
    pairs = np.vstack([
        triangles[:, [0, 1]], triangles[:, [0, 2]], triangles[:, [1, 2]],
    ])
    edges = np.unique(pairs, axis=0)

    # Look each triangle's three edges up in one pass: encode (a, b) as a
    # single integer key so the lookup is a sorted search rather than a dict
    # with millions of tuple keys.
    keys = edges[:, 0].astype(np.int64) * total + edges[:, 1]
    order = np.argsort(keys)
    sorted_keys = keys[order]

    def locate(pair: np.ndarray) -> np.ndarray:
        query = pair[:, 0].astype(np.int64) * total + pair[:, 1]
        position = np.searchsorted(sorted_keys, query)
        return order[position]

    triangle_edges = np.column_stack([
        locate(triangles[:, [0, 1]]),
        locate(triangles[:, [0, 2]]),
        locate(triangles[:, [1, 2]]),
    ])

    return SurfaceTopology(
        triangles=triangles,
        edges=edges,
        n_vertices=total,
        triangle_edges=triangle_edges,
    )


def edge_roughness(residuals: np.ndarray, topology: SurfaceTopology) -> np.ndarray:
    """``resl``: the squared increment of the normalised residual field per edge.

    Residuals are normalised to unit length *at each vertex first*, so what is
    measured is the correlation between neighbours and not the size of the
    residuals -- a region with larger variance is not mistaken for a rougher one.
    """
    residuals = np.asarray(residuals, dtype=np.float64)
    if residuals.shape[1] != topology.n_vertices:
        raise ValueError(
            f"residuals have {residuals.shape[1]} vertices but the mesh has "
            f"{topology.n_vertices}"
        )

    norm = np.sqrt(np.einsum("ij,ij->j", residuals, residuals))
    unit = np.zeros_like(residuals)
    usable = norm > 0
    np.divide(residuals, norm, out=unit, where=usable)

    a = topology.edges[:, 0]
    b = topology.edges[:, 1]
    difference = unit[:, a] - unit[:, b]
    return np.einsum("ij,ij->j", difference, difference)


@dataclass
class ReselEstimate:
    """Resel counts for a search region, and the FWHM they imply."""

    resels: np.ndarray            #: (3,) R0 (Euler char), R1, R2
    resels_per_vertex: np.ndarray #: (n_vertices,) 2-D resel density
    n_vertices: int               #: vertices in the search region
    n_edges: int
    n_triangles: int
    fwhm: float                   #: in vertex spacings -- see :attr:`fwhm_mm`
    fwhm_mm: Optional[float] = None

    @property
    def euler_characteristic(self) -> float:
        return float(self.resels[0])

    @property
    def area_resels(self) -> float:
        return float(self.resels[2])

    def describe(self) -> str:
        area = f"{self.area_resels:.1f} resels"
        fwhm = (
            f"FWHM {self.fwhm_mm:.2f} mm" if self.fwhm_mm is not None
            else f"FWHM {self.fwhm:.2f} vertices"
        )
        return (
            f"{self.n_vertices} vertices -> {area} "
            f"(EC {self.euler_characteristic:.0f}, {fwhm})"
        )

    def to_dict(self) -> dict:
        return {
            "resels": [float(r) for r in self.resels],
            "n_vertices": int(self.n_vertices),
            "fwhm_vertices": float(self.fwhm),
            "fwhm_mm": None if self.fwhm_mm is None else float(self.fwhm_mm),
            "euler_characteristic": float(self.euler_characteristic),
        }


def compute_resels(
    resl: np.ndarray,
    topology: SurfaceTopology,
    mask: np.ndarray,
    *,
    vertex_areas: Optional[np.ndarray] = None,
) -> ReselEstimate:
    """Resel counts of the masked search region.

    *resl* is the per-edge roughness from :func:`edge_roughness`; *mask* selects
    the vertices in the analysis.  An edge or triangle counts only if **all** of
    its vertices are in the mask, which is what makes the medial wall drop out
    of the search region cleanly instead of contributing a fictional boundary.

    Passing *vertex_areas* (mm² per vertex, from the geometry) additionally
    reports the FWHM in millimetres, which is the number people can judge.
    """
    resl = np.asarray(resl, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    if mask.size != topology.n_vertices:
        raise ValueError(
            f"the mask has {mask.size} entries but the mesh has "
            f"{topology.n_vertices} vertices"
        )

    edges = topology.edges
    triangles = topology.triangles

    edge_inside = mask[edges].all(axis=1)
    triangle_inside = mask[triangles].all(axis=1)

    n_vertices = int(mask.sum())
    n_edges = int(edge_inside.sum())
    n_triangles = int(triangle_inside.sum())

    if n_triangles == 0:
        raise ValueError(
            "the analysis mask contains no complete triangle -- there is no "
            "surface left to search"
        )

    # Lipschitz-Killing curvatures. lkc[d, k] is the d-dimensional curvature
    # contributed by the k-dimensional faces.
    length = np.sqrt(np.maximum(resl, 0.0))

    lkc_0 = np.array([n_vertices, n_edges, n_triangles], dtype=float)

    lkc_1_edges = float(length[edge_inside].sum())

    inside = topology.triangle_edges[triangle_inside]
    l12 = resl[inside[:, 0]]      # edge (v0, v1)
    l13 = resl[inside[:, 1]]      # edge (v0, v2)
    l23 = resl[inside[:, 2]]      # edge (v1, v2)

    lkc_1_triangles = float(
        (np.sqrt(l12) + np.sqrt(l13) + np.sqrt(l23)).sum() / 2.0
    )

    # Area of the triangle in the field's own metric, from its three squared
    # side lengths. Clipped at zero: an estimated metric need not satisfy the
    # triangle inequality when the residuals are noisy.
    squared_area = np.maximum(4.0 * l12 * l13 - (l12 + l13 - l23) ** 2, 0.0)
    triangle_resels = np.sqrt(squared_area) / 4.0
    lkc_2 = float(np.nansum(triangle_resels))

    resels = np.array([
        lkc_0[0] - lkc_0[1] + lkc_0[2],                       # Euler characteristic
        (lkc_1_edges - lkc_1_triangles) / np.sqrt(FOUR_LOG_2),
        lkc_2 / FOUR_LOG_2,
    ])

    # Spread each triangle's resels over its three corners, so a cluster's
    # extent in resels is just the sum over the vertices it contains.
    per_vertex = np.zeros(topology.n_vertices)
    for corner in range(3):
        np.add.at(per_vertex, triangles[triangle_inside, corner], triangle_resels)
    per_vertex /= 3.0 * FOUR_LOG_2
    per_vertex[~mask] = 0.0

    area_resels = float(resels[2])
    fwhm = float(np.sqrt(n_vertices / area_resels)) if area_resels > 0 else np.inf

    fwhm_mm = None
    if vertex_areas is not None:
        areas = np.asarray(vertex_areas, dtype=float)
        if areas.size != topology.n_vertices:
            raise ValueError(
                f"vertex_areas has {areas.size} entries but the mesh has "
                f"{topology.n_vertices} vertices"
            )
        total_mm2 = float(areas[mask].sum())
        if area_resels > 0:
            fwhm_mm = float(np.sqrt(total_mm2 / area_resels))

    estimate = ReselEstimate(
        resels=resels,
        resels_per_vertex=per_vertex,
        n_vertices=n_vertices,
        n_edges=n_edges,
        n_triangles=n_triangles,
        fwhm=fwhm,
        fwhm_mm=fwhm_mm,
    )
    log.info("smoothness: %s", estimate.describe())
    return estimate
