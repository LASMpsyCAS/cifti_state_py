"""CIFTI reading and writing.

The central object is :class:`SurfaceStatMap`: a statistic map expanded onto the
*full* surface mesh (32492 vertices per hemisphere for fs_LR 32k) together with
a mask of the vertices that were actually stored in the file.

Three CIFTI layouts all describe the same left+right 32k cortex and are handled
transparently:

===========  ==================================================================
 91282       Greyordinates: cortex 29696 (L) + 29716 (R), medial wall dropped,
             plus 19 subcortical/cerebellar volume structures.
 59412       Cortex only, same 29696 + 29716 vertices, medial wall dropped.
 64984       Dense surface: 32492 + 32492, medial wall *included*.
===========  ==================================================================

The expansion is driven entirely by each surface brain model's own
``vertex`` index list and ``nvertices`` count, so other mesh densities
(10k, 59k, 164k) and unusual structure orderings work without special cases.
Vertices absent from the file are filled with ``fill`` (0.0 by default, which
is what ``cifti_struct_dense_extract_surface_data`` in cifti-matlab does).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Sequence

import nibabel as nib
import numpy as np

from ..logging_setup import get_logger
from ..types import Statistic

log = get_logger(__name__)

__all__ = [
    "HemiSurfaceData",
    "SurfaceStatMap",
    "CiftiTemplate",
    "load_surface_stat_map",
    "load_surface_stat_map_from_gifti",
    "save_like",
    "make_dense_surface_template",
    "describe_layout",
    "STRUCTURE_LEFT",
    "STRUCTURE_RIGHT",
    "KNOWN_LAYOUTS",
]

STRUCTURE_LEFT = "CIFTI_STRUCTURE_CORTEX_LEFT"
STRUCTURE_RIGHT = "CIFTI_STRUCTURE_CORTEX_RIGHT"

_HEMI_STRUCTURE = {"left": STRUCTURE_LEFT, "right": STRUCTURE_RIGHT}

#: Recognised greyordinate counts, for friendly logging only. Nothing depends
#: on a map being one of these.
KNOWN_LAYOUTS: dict[int, str] = {
    91282: "91k greyordinates (32k cortex without medial wall + subcortex)",
    59412: "59k cortex only (32k cortex without medial wall)",
    64984: "64k dense surface (32k cortex including medial wall)",
    96854: "96k greyordinates (59k cortex + subcortex)",
    170494: "170k greyordinates (164k cortex + subcortex)",
}


@dataclass
class HemiSurfaceData:
    """One hemisphere expanded onto the full surface mesh."""

    values: np.ndarray            #: (n_vertices,) float, absent vertices filled
    present: np.ndarray           #: (n_vertices,) bool, True where the file had data
    vertex_index: np.ndarray      #: mesh vertex ids stored in the file, in file order
    n_vertices: int               #: full mesh size, e.g. 32492
    hemisphere: str

    def __post_init__(self) -> None:
        self.values = np.asarray(self.values, dtype=float)
        self.present = np.asarray(self.present, dtype=bool)
        self.vertex_index = np.asarray(self.vertex_index, dtype=np.int64)
        if self.values.shape != (self.n_vertices,):
            raise ValueError(
                f"{self.hemisphere}: values has shape {self.values.shape}, "
                f"expected ({self.n_vertices},)"
            )

    @property
    def n_present(self) -> int:
        return int(self.present.sum())

    @property
    def has_medial_wall(self) -> bool:
        """True when the file omitted some vertices (i.e. the medial wall)."""
        return self.n_present < self.n_vertices

    def compress(self, full: np.ndarray) -> np.ndarray:
        """Take a full-mesh array back down to the vertices stored in the file."""
        return np.asarray(full)[self.vertex_index]


@dataclass
class CiftiTemplate:
    """Everything needed to write an array back into a source file's layout.

    Positions are stored as explicit integer index arrays rather than slices, so
    a file whose structures are interleaved or out of order round-trips
    correctly.
    """

    path: Optional[Path]
    axes: list[Any]
    nifti_header: Any
    n_columns: int
    structure_positions: dict[str, np.ndarray] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_image(
        cls, img: "nib.cifti2.Cifti2Image", path: Optional[Path] = None
    ) -> "CiftiTemplate":
        axes = [img.header.get_axis(i) for i in range(img.ndim)]
        brain_axis = axes[-1]
        positions: dict[str, np.ndarray] = {}
        models: dict[str, Any] = {}
        for name, sl, model in brain_axis.iter_structures():
            positions[str(name)] = _as_positions(sl, brain_axis.size)
            models[str(name)] = model
        return cls(
            path=Path(path) if path else None,
            axes=axes,
            nifti_header=img.nifti_header,
            n_columns=int(brain_axis.size),
            structure_positions=positions,
            models=models,
        )

    def has_structure(self, structure: str) -> bool:
        return structure in self.structure_positions

    def surface_structures(self) -> list[str]:
        return [
            name
            for name, model in self.models.items()
            if getattr(model, "volume_shape", None) is None
            and getattr(model, "vertex", None) is not None
        ]

    def n_vertices(self, structure: str) -> int:
        return int(self.models[structure].nvertices[structure])

    @property
    def layout(self) -> str:
        return describe_layout(self.n_columns)


@dataclass
class SurfaceStatMap:
    """A cortical statistic map on the surface, both hemispheres."""

    left: HemiSurfaceData
    right: HemiSurfaceData
    statistic: Statistic = "z"
    df: Optional[float] = None
    template: Optional[CiftiTemplate] = None
    name: str = ""
    source_path: Optional[Path] = None
    fill: float = 0.0

    def __post_init__(self) -> None:
        if self.statistic == "t" and self.df is None:
            raise ValueError(
                "statistic='t' requires df (degrees of freedom); pass df=... "
                "or declare the map as 'z'/'other'"
            )

    # -- convenience -------------------------------------------------------- #

    @property
    def n_vertices(self) -> tuple[int, int]:
        return self.left.n_vertices, self.right.n_vertices

    @property
    def mesh_matches(self) -> bool:
        return self.left.n_vertices == self.right.n_vertices

    def hemi(self, which: str) -> HemiSurfaceData:
        which = which.lower()
        if which in ("l", "lh", "left"):
            return self.left
        if which in ("r", "rh", "right"):
            return self.right
        raise KeyError(f"unknown hemisphere {which!r}")

    def concatenated(self) -> np.ndarray:
        """Left then right on the full mesh (64984 values for fs_LR 32k)."""
        return np.concatenate([self.left.values, self.right.values])

    def present_mask(self) -> np.ndarray:
        return np.concatenate([self.left.present, self.right.present])

    def finite_values(self) -> np.ndarray:
        """Every value that was stored in the file and is finite."""
        out = []
        for h in (self.left, self.right):
            v = h.values[h.present]
            out.append(v[np.isfinite(v)])
        return np.concatenate(out) if out else np.empty(0)

    def with_values(
        self, left: np.ndarray, right: np.ndarray, *, name: Optional[str] = None
    ) -> "SurfaceStatMap":
        """Return a copy carrying new per-hemisphere full-mesh arrays."""
        return replace(
            self,
            left=replace(self.left, values=np.asarray(left, float)),
            right=replace(self.right, values=np.asarray(right, float)),
            name=self.name if name is None else name,
        )

    def describe(self) -> dict[str, Any]:
        vals = self.finite_values()
        return {
            "name": self.name,
            "source": str(self.source_path) if self.source_path else None,
            "layout": self.template.layout if self.template else "gifti pair",
            "statistic": self.statistic,
            "df": self.df,
            "n_vertices_left": self.left.n_vertices,
            "n_vertices_right": self.right.n_vertices,
            "n_present_left": self.left.n_present,
            "n_present_right": self.right.n_present,
            "medial_wall_excluded": self.left.has_medial_wall
            or self.right.has_medial_wall,
            "n_finite": int(vals.size),
            "min": float(vals.min()) if vals.size else None,
            "max": float(vals.max()) if vals.size else None,
        }


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def load_surface_stat_map(
    path: Path | str,
    *,
    statistic: Statistic = "z",
    df: Optional[float] = None,
    column: int = 0,
    fill: float = 0.0,
    name: Optional[str] = None,
    expected_vertices: Optional[int] = None,
) -> SurfaceStatMap:
    """Load one column of a CIFTI file as a cortical surface map.

    Works for 91k greyordinate, 59k cortex-only and 64k dense-surface files
    (and any other mesh density), because the vertex mapping is read from the
    file's own brain models.

    Parameters
    ----------
    column
        Which map/row to take when the file holds several (dscalar with
        multiple maps, dtseries, dlabel).
    fill
        Value written into mesh vertices the file does not cover -- the medial
        wall in 91k/59k files.  ``0.0`` matches cifti-matlab.
    expected_vertices
        If given, raise when a hemisphere's mesh size differs (e.g. pass
        ``32492`` to reject a 164k file early).
    """
    path = Path(path)
    img = nib.load(str(path))
    if not isinstance(img, nib.cifti2.Cifti2Image):
        raise TypeError(
            f"{path} is not a CIFTI file (got {type(img).__name__}); "
            "for GIFTI pairs use load_surface_stat_map_from_gifti"
        )

    data = np.asarray(img.get_fdata())
    if data.ndim == 1:
        data = data[None, :]
    data = data.reshape(-1, data.shape[-1])
    if column >= data.shape[0]:
        raise IndexError(
            f"{path} has {data.shape[0]} map(s); column {column} is out of range"
        )
    row = data[column]

    template = CiftiTemplate.from_image(img, path)

    missing = [s for s in _HEMI_STRUCTURE.values() if not template.has_structure(s)]
    if missing:
        raise ValueError(
            f"{path} has no {', '.join(missing)}. This tool works on cortical "
            f"surface data; the file contains: {sorted(template.structure_positions)}"
        )

    hemis: dict[str, HemiSurfaceData] = {}
    for hemi, structure in _HEMI_STRUCTURE.items():
        model = template.models[structure]
        n_vertices = int(model.nvertices[structure])
        vertex_index = np.asarray(model.vertex, dtype=np.int64)
        if vertex_index.size and (
            vertex_index.min() < 0 or vertex_index.max() >= n_vertices
        ):
            raise ValueError(
                f"{path}: {structure} vertex ids fall outside "
                f"[0, {n_vertices}); the file's brain model is inconsistent"
            )
        if expected_vertices is not None and n_vertices != expected_vertices:
            raise ValueError(
                f"{path}: {structure} has {n_vertices} vertices, expected "
                f"{expected_vertices}"
            )
        values = np.full(n_vertices, float(fill), dtype=float)
        values[vertex_index] = row[template.structure_positions[structure]]
        present = np.zeros(n_vertices, dtype=bool)
        present[vertex_index] = True
        hemis[hemi] = HemiSurfaceData(
            values=values,
            present=present,
            vertex_index=vertex_index,
            n_vertices=n_vertices,
            hemisphere=hemi,
        )

    if hemis["left"].n_vertices != hemis["right"].n_vertices:
        log.warning(
            "%s: hemispheres have different mesh sizes (%d vs %d)",
            path.name, hemis["left"].n_vertices, hemis["right"].n_vertices,
        )

    stat_map = SurfaceStatMap(
        left=hemis["left"],
        right=hemis["right"],
        statistic=statistic,
        df=df,
        template=template,
        name=name or path.stem,
        source_path=path,
        fill=float(fill),
    )
    log.info(
        "loaded %s [%s]: L %d/%d, R %d/%d vertices present",
        path.name,
        template.layout,
        stat_map.left.n_present,
        stat_map.left.n_vertices,
        stat_map.right.n_present,
        stat_map.right.n_vertices,
    )
    return stat_map


def load_surface_stat_map_from_gifti(
    left_path: Path | str,
    right_path: Path | str,
    *,
    statistic: Statistic = "z",
    df: Optional[float] = None,
    column: int = 0,
    name: Optional[str] = None,
) -> SurfaceStatMap:
    """Load a pair of ``*.func.gii`` / ``*.shape.gii`` files as a surface map.

    GIFTI metric files always carry every mesh vertex, so all vertices are
    marked present.  The result has no CIFTI template; use
    :func:`make_dense_surface_template` if you need to write CIFTI back out.
    """
    hemis: dict[str, HemiSurfaceData] = {}
    for hemi, path in (("left", Path(left_path)), ("right", Path(right_path))):
        img = nib.load(str(path))
        if not img.darrays:
            raise ValueError(f"{path}: no data arrays")
        stack = np.column_stack([np.asarray(d.data).ravel() for d in img.darrays])
        if column >= stack.shape[1]:
            raise IndexError(
                f"{path} has {stack.shape[1]} column(s); column {column} is out of range"
            )
        values = stack[:, column].astype(float)
        n_vertices = int(values.size)
        hemis[hemi] = HemiSurfaceData(
            values=values,
            present=np.ones(n_vertices, dtype=bool),
            vertex_index=np.arange(n_vertices, dtype=np.int64),
            n_vertices=n_vertices,
            hemisphere=hemi,
        )

    stat_map = SurfaceStatMap(
        left=hemis["left"],
        right=hemis["right"],
        statistic=statistic,
        df=df,
        template=None,
        name=name or Path(left_path).stem,
        source_path=Path(left_path),
    )
    log.info(
        "loaded GIFTI pair: L %d, R %d vertices",
        stat_map.left.n_vertices,
        stat_map.right.n_vertices,
    )
    return stat_map


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


def save_like(
    out_path: Path | str,
    left: np.ndarray,
    right: np.ndarray,
    template: CiftiTemplate,
    *,
    map_name: Optional[str] = None,
    other_structures_fill: float = 0.0,
    dtype: type = np.float32,
) -> Path:
    """Write full-mesh left/right arrays into the layout of *template*.

    Structures other than the two cortices (subcortical volumes in a 91k file)
    are filled with *other_structures_fill*.  That reproduces what the MATLAB
    pipeline wrote: a 91282-greyordinate dscalar whose subcortical entries are
    zero.  For a 59k or 64k template there are no such structures.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row = np.full(template.n_columns, float(other_structures_fill), dtype=float)
    for structure, values in (
        (STRUCTURE_LEFT, np.asarray(left, dtype=float)),
        (STRUCTURE_RIGHT, np.asarray(right, dtype=float)),
    ):
        if not template.has_structure(structure):
            raise ValueError(f"template has no {structure}")
        model = template.models[structure]
        vertex_index = np.asarray(model.vertex, dtype=np.int64)
        n_vertices = int(model.nvertices[structure])
        if values.shape != (n_vertices,):
            raise ValueError(
                f"{structure}: got shape {values.shape}, expected ({n_vertices},)"
            )
        row[template.structure_positions[structure]] = values[vertex_index]

    brain_axis = template.axes[-1]
    scalar_axis = nib.cifti2.ScalarAxis([map_name or out_path.stem])
    img = nib.cifti2.Cifti2Image(
        row[None, :].astype(dtype),
        header=(scalar_axis, brain_axis),
        nifti_header=template.nifti_header,
    )
    nib.save(img, str(out_path))
    log.info("wrote %s [%s]", out_path, template.layout)
    return out_path


def make_dense_surface_template(
    n_left: int = 32492,
    n_right: int = 32492,
    *,
    left_vertices: Optional[Sequence[int]] = None,
    right_vertices: Optional[Sequence[int]] = None,
) -> CiftiTemplate:
    """Build a dense-surface CIFTI template from scratch.

    With the defaults this is the 64984-greyordinate layout (medial wall
    included).  Pass explicit vertex lists to reproduce a 59412-style
    cortex-only layout instead.
    """
    left_vertices = (
        np.arange(n_left) if left_vertices is None
        else np.asarray(left_vertices, dtype=np.int64)
    )
    right_vertices = (
        np.arange(n_right) if right_vertices is None
        else np.asarray(right_vertices, dtype=np.int64)
    )

    left_mask = np.zeros(n_left, dtype=bool)
    left_mask[left_vertices] = True
    right_mask = np.zeros(n_right, dtype=bool)
    right_mask[right_vertices] = True
    brain_axis = nib.cifti2.BrainModelAxis.from_mask(
        left_mask, name="cortex_left"
    ) + nib.cifti2.BrainModelAxis.from_mask(right_mask, name="cortex_right")

    positions: dict[str, np.ndarray] = {}
    models: dict[str, Any] = {}
    for name, sl, model in brain_axis.iter_structures():
        positions[str(name)] = _as_positions(sl, brain_axis.size)
        models[str(name)] = model

    return CiftiTemplate(
        path=None,
        axes=[nib.cifti2.ScalarAxis(["map"]), brain_axis],
        nifti_header=None,
        n_columns=int(brain_axis.size),
        structure_positions=positions,
        models=models,
    )


def describe_layout(n_columns: int) -> str:
    return KNOWN_LAYOUTS.get(int(n_columns), f"{int(n_columns)} greyordinates")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _as_positions(sl: Any, size: int) -> np.ndarray:
    """Normalise whatever ``iter_structures`` hands back into an index array."""
    if isinstance(sl, slice):
        return np.arange(*sl.indices(size), dtype=np.int64)
    idx = np.asarray(sl)
    if idx.dtype == bool:
        return np.flatnonzero(idx).astype(np.int64)
    return idx.astype(np.int64)
