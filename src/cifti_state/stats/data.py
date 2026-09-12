"""Reading a whole study into one array.

A group analysis needs the subjects stacked: an ``(n_observations, n_vertices)``
matrix where every row is one scan on the *same* mesh.  :func:`load_dataset`
builds that from a :class:`~cifti_state.stats.design.Design`, and enforces the
two things that silently ruin a group analysis if they are not checked:

* **Every file must be on the same mesh.**  A 91k file and a 64k file expand to
  the same 32492 vertices per hemisphere, so mixing them is fine and is handled
  transparently -- but a 10k file among 32k files is not, and is refused.
* **Only vertices present in every file are analysed.**  A vertex that one
  subject's file does not carry is filled (with 0.0 by default), and a column
  of mostly-real numbers with one artificial zero in it produces a confident,
  entirely fictional t value.  The intersection of the present masks is the
  analysis mask, and everything downstream respects it.

The medial wall is excluded by exactly this rule when the inputs are 91k or
59k files, without anything having to know what a medial wall is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..io.cifti import CiftiTemplate, SurfaceStatMap, load_surface_stat_map
from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from .design import Design

log = get_logger(__name__)

__all__ = ["SurfaceDataset", "load_dataset"]


@dataclass
class SurfaceDataset:
    """Every observation on the full mesh, plus the mask they all share."""

    data: np.ndarray          #: (n_observations, n_left + n_right) float
    mask: np.ndarray          #: (n_left + n_right,) bool -- analysable vertices
    n_left: int
    n_right: int
    labels: list[str]
    template: CiftiTemplate
    paths: list[Path]
    name: str = "dataset"

    @property
    def n_observations(self) -> int:
        return int(self.data.shape[0])

    @property
    def n_vertices(self) -> int:
        return int(self.data.shape[1])

    def hemi(self, which: str) -> np.ndarray:
        """The columns belonging to one hemisphere."""
        if which in ("left", "l", "lh"):
            return self.data[:, : self.n_left]
        if which in ("right", "r", "rh"):
            return self.data[:, self.n_left:]
        raise KeyError(f"unknown hemisphere {which!r}")

    def split(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Split a per-vertex array back into (left, right)."""
        values = np.asarray(values)
        return values[..., : self.n_left], values[..., self.n_left:]

    def as_stat_map(
        self, values: np.ndarray, *, statistic: str = "t",
        df: Optional[float] = None, name: Optional[str] = None,
    ) -> SurfaceStatMap:
        """Wrap a per-vertex result so the rest of the package can use it."""
        from ..io.cifti import HemiSurfaceData

        left, right = self.split(np.asarray(values, dtype=float))
        mask_left, mask_right = self.split(self.mask)

        def hemisphere(vals, present, hemisphere_name):
            return HemiSurfaceData(
                values=np.asarray(vals, dtype=float),
                present=np.asarray(present, dtype=bool),
                vertex_index=np.flatnonzero(present),
                n_vertices=int(vals.size),
                hemisphere=hemisphere_name,
            )

        return SurfaceStatMap(
            left=hemisphere(left, mask_left, "left"),
            right=hemisphere(right, mask_right, "right"),
            statistic=statistic,
            df=df,
            template=self.template,
            name=name or self.name,
            source_path=None,
            fill=0.0,
        )

    def describe(self) -> dict:
        return {
            "observations": self.n_observations,
            "vertices": self.n_vertices,
            "analysed": int(self.mask.sum()),
            "left": f"{int(self.split(self.mask)[0].sum())}/{self.n_left}",
            "right": f"{int(self.split(self.mask)[1].sum())}/{self.n_right}",
        }


def load_dataset(
    design: Design,
    *,
    statistic: str = "other",
    column: int = 0,
    fill: float = 0.0,
    dtype: type = np.float32,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> SurfaceDataset:
    """Read every file the design names into one matrix.

    For a paired design the rows are the *differences*, computed file by file
    so only two maps are ever in memory at once.

    ``statistic="other"`` is deliberate: the input maps are measurements
    (thickness, connectivity, a contrast estimate), not test statistics, and
    labelling them ``z`` would invite a p-value conversion that means nothing
    here.  The statistic is what comes *out* of the model.
    """
    paths = design.paths
    subtract = design.subtract_paths
    n = len(paths)
    if n == 0:
        raise ValueError("the design names no files")

    data: Optional[np.ndarray] = None
    mask: Optional[np.ndarray] = None
    template: Optional[CiftiTemplate] = None
    n_left = n_right = 0

    for index, path in enumerate(paths):
        check_cancelled(cancel)
        report_progress(
            progress, 0.05 + 0.9 * index / n,
            f"reading {index + 1}/{n}: {Path(path).name}",
        )
        current = load_surface_stat_map(
            path, statistic=statistic, column=column, fill=fill
        )
        values = current.concatenated()
        present = current.present_mask()

        if subtract is not None:
            baseline = load_surface_stat_map(
                subtract[index], statistic=statistic, column=column, fill=fill
            )
            if baseline.concatenated().size != values.size:
                raise ValueError(
                    f"{Path(path).name} and {Path(subtract[index]).name} are on "
                    f"different meshes ({values.size} vs "
                    f"{baseline.concatenated().size} vertices)"
                )
            values = values - baseline.concatenated()
            present = present & baseline.present_mask()

        if data is None:
            n_left = current.left.n_vertices
            n_right = current.right.n_vertices
            template = current.template
            data = np.empty((n, values.size), dtype=dtype)
            mask = np.ones(values.size, dtype=bool)
        elif values.size != data.shape[1]:
            raise ValueError(
                f"{Path(path).name} has {values.size} vertices but the first "
                f"file had {data.shape[1]}. Every map must be on the same mesh; "
                f"resample them before running a group test."
            )

        data[index] = values.astype(dtype, copy=False)
        mask &= present
        mask &= np.isfinite(values)

    assert data is not None and mask is not None and template is not None

    dataset = SurfaceDataset(
        data=data,
        mask=mask,
        n_left=n_left,
        n_right=n_right,
        labels=list(design.labels),
        template=template,
        paths=[Path(p) for p in paths],
        name=design.kind,
    )
    report_progress(progress, 1.0, "data loaded")
    log.info(
        "loaded %d observations on %d vertices (%d analysable: L %s, R %s)%s",
        dataset.n_observations, dataset.n_vertices, int(mask.sum()),
        dataset.describe()["left"], dataset.describe()["right"],
        " [paired differences]" if subtract is not None else "",
    )
    return dataset
