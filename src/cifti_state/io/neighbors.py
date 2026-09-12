"""Vertex adjacency for surface clustering.

Two interchangeable back-ends:

``txt``
    Read the pre-computed ``*.neighbors_IndexStart0.txt`` tables that the
    original MATLAB pipeline used.  Column 0 is the (0-based) vertex index,
    the remaining columns are its neighbours.  Rows may be ragged.

``surface``
    Derive adjacency from the triangles of a ``*.surf.gii`` mesh.

The two agree exactly on the fs_LR 32k mesh -- verified vertex by vertex
against ``lh/rh.neighbors_IndexStart0.txt`` (32480 vertices with 6 neighbours,
12 with 5).  :func:`check_agreement` re-runs that comparison.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.sparse import csr_matrix, coo_matrix

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "load_neighbors",
    "adjacency_from_txt",
    "adjacency_from_surface",
    "neighbor_lists",
    "check_agreement",
]


def load_neighbors(
    settings: Settings,
    hemisphere: str,
    *,
    mesh: Optional[str] = None,
    source: Optional[str] = None,
    n_vertices: Optional[int] = None,
) -> csr_matrix:
    """Return the symmetric adjacency matrix for one hemisphere."""
    mesh = mesh or settings.defaults.mesh
    source = source or settings.defaults.neighbor_source

    if source == "txt":
        path = settings.resources.neighbor_path(mesh, hemisphere)
        return _cached_txt(str(path), n_vertices)
    if source == "surface":
        path = settings.surface_for(hemisphere, "midthickness", mesh=mesh)
        return _cached_surface(str(path))
    raise ValueError(f"unknown neighbor source {source!r}; use 'txt' or 'surface'")


def adjacency_from_txt(
    path: Path | str, n_vertices: Optional[int] = None
) -> csr_matrix:
    """Build adjacency from a ``*.neighbors_IndexStart0.txt`` file."""
    path = Path(path)
    rows: list[int] = []
    cols: list[int] = []
    max_index = -1
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh):
            parts = line.split()
            if not parts:
                continue
            try:
                vertex = int(float(parts[0]))
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{line_no + 1}: cannot parse vertex index {parts[0]!r}"
                ) from exc
            max_index = max(max_index, vertex)
            for token in parts[1:]:
                try:
                    value = float(token)
                except ValueError:
                    continue
                if not np.isfinite(value):      # the MATLAB tables pad with NaN
                    continue
                neighbor = int(value)
                if neighbor < 0:
                    continue
                max_index = max(max_index, neighbor)
                rows.append(vertex)
                cols.append(neighbor)

    size = int(n_vertices) if n_vertices is not None else max_index + 1
    adj = _symmetrise(rows, cols, size)
    log.debug("adjacency from %s: %d vertices, %d edges", path.name, size, adj.nnz // 2)
    return adj


def adjacency_from_surface(faces_or_path, n_vertices: Optional[int] = None) -> csr_matrix:
    """Build adjacency from surface triangles (or from a ``*.surf.gii`` path)."""
    if isinstance(faces_or_path, (str, Path)):
        from .surface import load_surface

        surface = load_surface(faces_or_path)
        faces = surface.faces
        n_vertices = n_vertices or surface.n_vertices
    else:
        faces = np.asarray(faces_or_path, dtype=np.int64)
        n_vertices = n_vertices or int(faces.max()) + 1

    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [0, 2]]])
    return _symmetrise(edges[:, 0], edges[:, 1], int(n_vertices))


def neighbor_lists(adjacency: csr_matrix) -> list[np.ndarray]:
    """Convert an adjacency matrix to per-vertex neighbour index arrays."""
    adjacency = adjacency.tocsr()
    indptr, indices = adjacency.indptr, adjacency.indices
    return [indices[indptr[i]:indptr[i + 1]] for i in range(adjacency.shape[0])]


def check_agreement(
    txt_path: Path | str, surface_path: Path | str
) -> tuple[bool, int]:
    """Compare the two back-ends. Returns ``(identical, n_differing_vertices)``."""
    from .surface import load_surface

    surface = load_surface(surface_path)
    a = adjacency_from_txt(txt_path, surface.n_vertices)
    b = adjacency_from_surface(surface.faces, surface.n_vertices)
    diff = (a != b)
    n_bad = int(np.unique(diff.tocoo().row).size) if diff.nnz else 0
    return n_bad == 0, n_bad


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _symmetrise(rows, cols, size: int) -> csr_matrix:
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    if rows.size == 0:
        return csr_matrix((size, size), dtype=np.int8)
    both_r = np.concatenate([rows, cols])
    both_c = np.concatenate([cols, rows])
    keep = both_r != both_c                      # drop self loops
    both_r, both_c = both_r[keep], both_c[keep]
    data = np.ones(both_r.size, dtype=np.int8)
    adj = coo_matrix((data, (both_r, both_c)), shape=(size, size)).tocsr()
    adj.data[:] = 1                              # deduplicate to a 0/1 matrix
    adj.sum_duplicates()
    adj.data[:] = 1
    return adj


@lru_cache(maxsize=8)
def _cached_txt(path: str, n_vertices: Optional[int]) -> csr_matrix:
    return adjacency_from_txt(path, n_vertices)


@lru_cache(maxsize=8)
def _cached_surface(path: str) -> csr_matrix:
    return adjacency_from_surface(path)
