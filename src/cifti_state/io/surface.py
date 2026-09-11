"""Surface (GIFTI) loading, geometry helpers and a small cache."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import nibabel as nib
import numpy as np

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = ["Surface", "load_surface", "load_hemisphere_surfaces", "vertex_areas"]


@dataclass(frozen=True)
class Surface:
    """A triangulated surface mesh."""

    coords: np.ndarray      #: (n_vertices, 3) float32
    faces: np.ndarray       #: (n_faces, 3) int
    hemisphere: str
    kind: str
    path: Optional[Path] = None

    @property
    def n_vertices(self) -> int:
        return int(self.coords.shape[0])

    @property
    def n_faces(self) -> int:
        return int(self.faces.shape[0])

    def vertex_areas(self) -> np.ndarray:
        """Per-vertex area: one third of each incident triangle's area."""
        return vertex_areas(self.coords, self.faces)


def load_surface(
    path: Path | str, *, hemisphere: str = "", kind: str = ""
) -> Surface:
    """Read a ``*.surf.gii`` file."""
    path = Path(path)
    img = nib.load(str(path))
    coords = None
    faces = None
    for darray in img.darrays:
        intent = nib.nifti1.intent_codes.code.get("NIFTI_INTENT_POINTSET")
        tri_intent = nib.nifti1.intent_codes.code.get("NIFTI_INTENT_TRIANGLE")
        if darray.intent == intent:
            coords = np.asarray(darray.data, dtype=np.float64)
        elif darray.intent == tri_intent:
            faces = np.asarray(darray.data, dtype=np.int64)
    if coords is None or faces is None:  # fall back to positional order
        if len(img.darrays) < 2:
            raise ValueError(f"{path} does not look like a surface file")
        coords = np.asarray(img.darrays[0].data, dtype=np.float64)
        faces = np.asarray(img.darrays[1].data, dtype=np.int64)
    return Surface(
        coords=coords, faces=faces, hemisphere=hemisphere, kind=kind, path=path
    )


def load_hemisphere_surfaces(
    settings: Settings, kind: Optional[str] = None
) -> dict[str, Surface]:
    """Load the left and right template surfaces of a given kind."""
    kind = kind or settings.render.surface
    out = {}
    for hemi in ("left", "right"):
        path = settings.resources.surface_path(hemi, kind)
        out[hemi] = _load_cached(str(path), hemi, kind)
    return out


@lru_cache(maxsize=16)
def _load_cached(path: str, hemisphere: str, kind: str) -> Surface:
    return load_surface(path, hemisphere=hemisphere, kind=kind)


def vertex_areas(coords: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-vertex surface area (mm^2), the standard 1/3-of-each-triangle rule."""
    coords = np.asarray(coords, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    v0, v1, v2 = coords[faces[:, 0]], coords[faces[:, 1]], coords[faces[:, 2]]
    tri_area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    out = np.zeros(coords.shape[0], dtype=np.float64)
    np.add.at(out, faces[:, 0], tri_area / 3.0)
    np.add.at(out, faces[:, 1], tri_area / 3.0)
    np.add.at(out, faces[:, 2], tri_area / 3.0)
    return out
