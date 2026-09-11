"""The greyscale sulcal underlay that sits beneath a statistic map.

Without it a thresholded map floats on a featureless grey shell and you cannot
tell a gyral crown from a sulcal fundus.  The fs_LR template pack ships
``fs_LR.32k.LR.sulc.dscalar.nii``; this module turns it into a per-vertex grey
that both renderers draw underneath the statistic.

``binary`` (the default) is the Workbench/FreeSurfer convention: two flat greys
split at zero, so the folding pattern reads clearly without competing with the
overlay.  ``continuous`` keeps the gradient, which looks softer but makes a
faint overlay harder to see.

**Which sign is a sulcus is not a constant.**  FreeSurfer's ``?h.sulc`` is
positive in sulci; the fs_LR template pack's ``fs_LR.32k.LR.sulc.dscalar.nii``
is negative there (measured against the midthickness mesh: the correlation
between its values and vertex concavity is -0.63, and -0.90 for the curvature
file).  Getting this backwards inverts the whole picture -- gyri painted as
sulci -- and it is not obvious from the numbers alone.  So rather than trusting
a convention, :func:`load_underlay` measures it: it computes concavity from the
surface itself and orients the greyscale to match.  ``render.underlay_orient``
can pin the answer if the automatic one is ever wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import ConfigError, Settings
from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "Underlay",
    "load_underlay",
    "greyscale",
    "available_underlays",
    "DEFAULT_DARK",
    "DEFAULT_LIGHT",
]

#: Grey levels for the two sides of the fold. Chosen so a hot colour map still
#: has plenty of contrast against the lighter (gyral) side.
DEFAULT_DARK = 0.38
DEFAULT_LIGHT = 0.72


@dataclass
class Underlay:
    """A per-hemisphere greyscale array in ``[0, 1]``, ready to colour with."""

    left: np.ndarray
    right: np.ndarray
    name: str = "sulc"
    style: str = "binary"
    source: Optional[Path] = None

    def hemi(self, which: str) -> np.ndarray:
        which = which.lower()
        if which in ("l", "lh", "left"):
            return self.left
        if which in ("r", "rh", "right"):
            return self.right
        raise KeyError(f"unknown hemisphere {which!r}")

    def rgb(self, hemisphere: str) -> np.ndarray:
        """``(n_vertices, 3)`` float RGB, for compositing."""
        grey = self.hemi(hemisphere)[:, None]
        return np.repeat(grey, 3, axis=1)


def available_underlays(settings: Settings) -> dict[str, Path]:
    """Configured underlays that actually exist on disk."""
    out: dict[str, Path] = {}
    for name, filename in (settings.resources.underlays or {}).items():
        try:
            path = settings.resources.underlay_path(name)
        except ConfigError:
            continue
        if path.exists():
            out[name] = path
    return out


def load_underlay(
    settings: Settings,
    name: Optional[str] = None,
    *,
    style: Optional[str] = None,
    dark: float = DEFAULT_DARK,
    light: float = DEFAULT_LIGHT,
    orient: Optional[str] = None,
    n_vertices: Optional[tuple[int, int]] = None,
) -> Optional[Underlay]:
    """Load and greyscale an underlay. Returns ``None`` when it is unavailable.

    Deliberately forgiving: a missing underlay should soften the picture, never
    stop a render.  Anything that goes wrong is logged and the caller draws a
    flat surface instead.
    """
    name = name or settings.render.underlay
    if not name or str(name).lower() in ("", "none", "off"):
        return None
    style = style or settings.render.underlay_style

    try:
        path = settings.resources.underlay_path(name)
    except ConfigError as exc:
        log.warning("underlay %r is not configured (%s)", name, exc)
        return None
    if not path.exists():
        log.warning("underlay %r not found at %s", name, path)
        return None

    try:
        values = _load_values(str(path))
    except Exception as exc:
        log.warning("could not read the underlay %s: %s", path.name, exc)
        return None

    orient = orient or getattr(settings.render, "underlay_orient", "auto")
    sign = _resolve_orientation(orient, settings, values["left"], name)

    left = greyscale(values["left"], style=style, dark=dark, light=light,
                     sulcal_sign=sign)
    right = greyscale(values["right"], style=style, dark=dark, light=light,
                      sulcal_sign=sign)

    if n_vertices is not None:
        expected_left, expected_right = n_vertices
        if left.size != expected_left or right.size != expected_right:
            log.warning(
                "underlay %r is on a %d/%d mesh but the data is on %d/%d; "
                "skipping the underlay",
                name, left.size, right.size, expected_left, expected_right,
            )
            return None

    log.info("underlay %s (%s) from %s", name, style, path.name)
    return Underlay(left=left, right=right, name=name, style=style, source=path)


@lru_cache(maxsize=8)
def _load_values(path: str) -> dict[str, np.ndarray]:
    """Read an underlay dscalar onto the full mesh, both hemispheres."""
    from ..io.cifti import load_surface_stat_map

    # statistic="other": an underlay is not a test statistic and must never be
    # fed to a p-value conversion.
    stat_map = load_surface_stat_map(path, statistic="other", fill=0.0)
    return {
        "left": np.asarray(stat_map.left.values, dtype=float),
        "right": np.asarray(stat_map.right.values, dtype=float),
    }


def greyscale(
    values: np.ndarray,
    *,
    style: str = "binary",
    dark: float = DEFAULT_DARK,
    light: float = DEFAULT_LIGHT,
    threshold: float = 0.0,
    sulcal_sign: int = -1,
) -> np.ndarray:
    """Map an underlay to grey levels in ``[0, 1]``.

    ``binary``
        Two flat greys split at *threshold* -- the folding pattern as Workbench
        and FreeSurfer draw it.
    ``continuous``
        A robust 2nd-98th percentile stretch of the raw values.

    *sulcal_sign* says which sign is a sulcus and therefore gets *dark*: ``-1``
    for the fs_LR template files, ``+1`` for FreeSurfer's ``?h.sulc``.  See the
    module docstring -- this is measured, not assumed.
    """
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    oriented = values * (1.0 if sulcal_sign >= 0 else -1.0)

    if style == "binary":
        grey = np.where(oriented > threshold, dark, light)
    elif style == "continuous":
        pool = oriented[finite]
        if pool.size:
            low, high = np.percentile(pool, [2, 98])
        else:
            low, high = 0.0, 1.0
        if high <= low:
            high = low + 1.0
        # oriented is now positive in sulci, so a high value must go dark
        normalised = np.clip((oriented - low) / (high - low), 0.0, 1.0)
        grey = light - normalised * (light - dark)
    else:
        raise ValueError(f"unknown underlay style {style!r}; use binary or continuous")

    grey = np.where(finite, grey, (dark + light) / 2)
    return grey.astype(float)


# --------------------------------------------------------------------------- #
# which sign is a sulcus
# --------------------------------------------------------------------------- #


def _resolve_orientation(orient: str, settings: Settings,
                         left_values: np.ndarray, name: str) -> int:
    """``+1`` if positive values are sulcal, ``-1`` if negative ones are."""
    orient = str(orient or "auto").lower()
    if orient in ("positive", "+", "+1", "freesurfer"):
        return 1
    if orient in ("negative", "-", "-1", "fs_lr", "fslr"):
        return -1
    if orient != "auto":
        log.warning("unknown underlay_orient %r; measuring instead", orient)

    try:
        concavity = _left_concavity(settings)
    except Exception as exc:
        log.debug("could not measure concavity (%s); assuming negative", exc)
        return -1
    if concavity is None or concavity.size != left_values.size:
        return -1

    usable = np.isfinite(left_values) & (left_values != 0.0)
    if usable.sum() < 1000:
        return -1
    correlation = float(np.corrcoef(left_values[usable], concavity[usable])[0, 1])
    if not np.isfinite(correlation) or abs(correlation) < 0.15:
        log.warning(
            "underlay %r does not track the surface's folding (r=%.2f); "
            "drawing it as-is", name, correlation,
        )
        return -1
    sign = 1 if correlation > 0 else -1
    log.debug("underlay %r: sulcal sign %+d (r=%.2f)", name, sign, correlation)
    return sign


@lru_cache(maxsize=2)
def _left_concavity_cached(path: str) -> Optional[np.ndarray]:
    """Per-vertex concavity of a surface: positive inside a sulcus.

    The umbrella (graph) Laplacian points from a vertex towards the average of
    its neighbours.  On a concave patch that direction agrees with the outward
    normal, on a convex one it opposes it -- so the dot product of the two is a
    sign-correct, scale-free curvature proxy, and it needs nothing but the mesh.
    """
    from ..io.surface import load_surface

    surface = load_surface(path)
    coords = np.asarray(surface.coords, dtype=float)
    faces = np.asarray(surface.faces, dtype=np.int64)
    if coords.size == 0 or faces.size == 0:
        return None

    triangles = coords[faces]
    face_normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    normals = np.zeros_like(coords)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12

    neighbour_sum = np.zeros_like(coords)
    neighbour_count = np.zeros(len(coords))
    for a, b in ((0, 1), (1, 2), (2, 0)):
        np.add.at(neighbour_sum, faces[:, a], coords[faces[:, b]])
        np.add.at(neighbour_count, faces[:, a], 1)
        np.add.at(neighbour_sum, faces[:, b], coords[faces[:, a]])
        np.add.at(neighbour_count, faces[:, b], 1)
    laplacian = (
        neighbour_sum / np.maximum(neighbour_count, 1)[:, None] - coords
    )
    return (laplacian * normals).sum(axis=1)


def _left_concavity(settings: Settings) -> Optional[np.ndarray]:
    path = settings.resources.surface_path("left", "midthickness")
    return _left_concavity_cached(str(path))
