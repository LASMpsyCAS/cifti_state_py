"""The three t-tests, end to end.

:func:`one_sample_t`, :func:`two_sample_t` and :func:`paired_t` each take a
participants table and give back a :class:`GroupAnalysis`: the t map, the
smoothness of the field it came from, and whichever null models were asked for.
The t map is an ordinary
:class:`~cifti_state.io.cifti.SurfaceStatMap`, so everything already in the
package -- thresholding, clustering, the anatomical report, the figures, the
interface -- works on it without knowing where it came from.

Cluster correction is a second step on purpose.  Forming clusters needs a
threshold, and that threshold is a scientific choice, not a detail to bury:
:meth:`GroupAnalysis.correct_clusters` takes the clusters you formed and tells
you how surprising each one is, under random field theory, under permutation,
or both.

The two corrections answer the same question by different routes, so comparing
them is free information.  When they agree, the answer is solid.  When RFT is
much more permissive than permutation, its assumptions -- a smooth, roughly
stationary field -- are the first place to look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.sparse import block_diag, csr_matrix

from ..config import Settings
from ..core.cluster import ClusterResult
from ..io.cifti import SurfaceStatMap
from ..io.surface import load_hemisphere_surfaces, vertex_areas
from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from .data import SurfaceDataset, load_dataset
from .design import Design, Participants, one_sample_design, paired_design, two_sample_design
from .glm import GLMFit, fit_glm, t_to_z, welch_two_sample
from .permutation import PermutationResult, permutation_test
from .resels import ReselEstimate, build_topology, compute_resels, edge_roughness
from .rft import RandomField
from .tfce import PALM_DEFAULT, TFCEEngine, TFCESettings

log = get_logger(__name__)

__all__ = [
    "GroupAnalysis",
    "run_test",
    "one_sample_t",
    "two_sample_t",
    "paired_t",
    "CORRECTION_COLUMNS",
]

#: Columns :meth:`GroupAnalysis.correct_clusters` adds to a cluster table.
CORRECTION_COLUMNS: tuple[str, ...] = (
    "cluster_id", "size_vertices", "size_resels", "peak_t",
    "p_rft_cluster", "p_rft_peak", "p_perm_cluster", "p_perm_peak",
    "peak_tfce", "p_tfce",
)


@dataclass
class GroupAnalysis:
    """A fitted group test and the null models that go with it."""

    design: Design
    fit: GLMFit
    stat_map: SurfaceStatMap
    dataset: SurfaceDataset
    smoothness: Optional[ReselEstimate] = None
    random_field: Optional[RandomField] = None
    permutation: Optional[PermutationResult] = None
    adjacency: Optional[csr_matrix] = None
    warnings: list[str] = field(default_factory=list)
    #: (n_vertices,) the observed TFCE score, when TFCE was asked for.
    tfce: Optional[np.ndarray] = None
    tfce_settings: Optional[TFCESettings] = None

    @property
    def dof(self) -> float:
        return self.fit.dof

    @property
    def statistic(self) -> str:
        return self.fit.statistic

    # -- TFCE --------------------------------------------------------------- #

    @property
    def tfce_p(self) -> Optional[np.ndarray]:
        """(n_vertices,) FWE-corrected P per vertex, from the max-TFCE null.

        ``None`` unless the test was run with both ``tfce=True`` and
        permutations: the TFCE score has no distribution of its own, so
        without a permutation null there is nothing to compare it against.
        """
        if self.tfce is None or self.permutation is None:
            return None
        if self.permutation.max_tfce is None:
            return None
        return np.asarray(self.permutation.tfce_pvalue(self.tfce), dtype=float)

    def tfce_stat_map(self) -> SurfaceStatMap:
        """The TFCE score as a surface map, ready for the rest of the package."""
        if self.tfce is None:
            raise ValueError("this analysis was not run with TFCE")
        return self.dataset.as_stat_map(
            self.tfce, statistic="tfce", df=None,
            name=f"{self.design.kind}_tfce",
        )

    def tfce_pmap(self) -> SurfaceStatMap:
        """The corrected P values as a surface map (small = significant)."""
        p = self.tfce_p
        if p is None:
            raise ValueError(
                "corrected TFCE P values need a permutation null; re-run with "
                "tfce=True and permutations > 0"
            )
        return self.dataset.as_stat_map(
            p, statistic="p", df=None, name=f"{self.design.kind}_tfce_p",
        )

    def summary(self) -> str:
        parts = [
            self.design.describe(),
            f"{int(self.fit.mask.sum())} vertices tested",
            f"|{self.statistic}| max {np.abs(self.fit.t[self.fit.mask]).max():.3f}",
        ]
        if self.smoothness is not None:
            parts.append(self.smoothness.describe())
        if self.random_field is not None:
            parts.append(
                f"peak FWE 0.05 at {self.random_field.peak_threshold(0.05):.3f}"
            )
        if self.permutation is not None:
            parts.append(
                f"permutation peak FWE 0.05 at "
                f"{self.permutation.statistic_threshold(0.05):.3f}"
            )
        if self.tfce is not None:
            line = f"TFCE ({(self.tfce_settings or PALM_DEFAULT).describe()})"
            p = self.tfce_p
            if p is not None:
                line += f": {int((p <= 0.05).sum())} vertices at corrected P<=0.05"
            parts.append(line)
        return "\n  ".join(parts)

    # -- correction --------------------------------------------------------- #

    def correct_clusters(
        self, clusters: ClusterResult, *, cluster_forming: Optional[float] = None,
    ) -> pd.DataFrame:
        """FWE-corrected P values for every cluster in *clusters*.

        The cluster-forming threshold is taken from the clusters themselves,
        because using a different one here than the one that made them would
        silently answer a question about a different analysis.
        """
        if cluster_forming is None:
            cluster_forming = _forming_threshold(clusters)

        permutation = self.permutation
        if permutation is not None and not np.isclose(
            permutation.cluster_forming, cluster_forming, rtol=1e-6, atol=1e-9
        ):
            # The permutation null was built by counting clusters above one
            # particular height. Comparing it against clusters formed at a
            # different height compares two different experiments.
            raise ValueError(
                f"the permutation null was built at a cluster-forming threshold "
                f"of {permutation.cluster_forming:g} but these clusters were "
                f"formed at {cluster_forming:g}; re-run the test with "
                f"cluster_forming={cluster_forming:g} so the two agree"
            )

        rows = []
        per_vertex = (
            self.smoothness.resels_per_vertex if self.smoothness is not None else None
        )
        values = np.asarray(self.fit.t, dtype=float)
        tfce_values = None if self.tfce is None else np.asarray(self.tfce, dtype=float)
        tfce_p = self.tfce_p

        for info in clusters.clusters:
            hemisphere, vertices = clusters.vertices(info.cluster_id)
            offset = 0 if hemisphere == "left" else self.dataset.n_left
            flat = np.asarray(vertices, dtype=int) + offset
            peak = float(np.abs(values[flat]).max())
            size_resels = (
                float(per_vertex[flat].sum()) if per_vertex is not None else np.nan
            )
            row: dict[str, Any] = {
                "cluster_id": int(info.cluster_id),
                "hemi": "L" if hemisphere == "left" else "R",
                "size_vertices": int(info.size_vertices),
                "size_resels": size_resels,
                "peak_t": peak,
            }
            if self.random_field is not None and per_vertex is not None:
                row["p_rft_cluster"] = float(
                    self.random_field.cluster_pvalue(size_resels, u=cluster_forming)
                )
                row["p_rft_peak"] = float(self.random_field.peak_pvalue(peak))
            if self.permutation is not None:
                row["p_perm_cluster"] = float(
                    self.permutation.extent_pvalue(info.size_vertices)
                )
                row["p_perm_peak"] = float(self.permutation.statistic_pvalue(peak))
            if tfce_values is not None:
                # The cluster's TFCE entry is its best vertex: TFCE is a
                # vertex-wise statistic, so a cluster "survives" exactly when
                # one of its vertices does.
                best = int(flat[int(np.argmax(tfce_values[flat]))])
                row["peak_tfce"] = float(tfce_values[best])
                if tfce_p is not None:
                    row["p_tfce"] = float(tfce_p[best])
            rows.append(row)

        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame = frame.sort_values("size_vertices", ascending=False).reset_index(drop=True)
        log.info(
            "corrected %d clusters at a cluster-forming threshold of %.4g",
            len(frame), cluster_forming,
        )
        return frame

    # -- serialisation ------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "design": self.design.to_dict(),
            "fit": self.fit.describe(),
            "data": self.dataset.describe(),
            "smoothness": None if self.smoothness is None else self.smoothness.to_dict(),
            "random_field": None if self.random_field is None
            else self.random_field.to_dict(),
            "permutation": None if self.permutation is None else self.permutation.to_dict(),
            "tfce": None if self.tfce is None else {
                **(self.tfce_settings or PALM_DEFAULT).to_dict(),
                "max": float(np.max(self.tfce)),
                "significant_vertices": (
                    None if self.tfce_p is None else int((self.tfce_p <= 0.05).sum())
                ),
            },
            "warnings": list(self.warnings),
        }

    def to_json(self, path: Optional[Path | str] = None, *, indent: int = 2) -> str:
        import json

        text = json.dumps(self.to_dict(), indent=indent, default=str)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding="utf-8")
        return text


# --------------------------------------------------------------------------- #
# the shared machinery
# --------------------------------------------------------------------------- #


def run_test(
    design: Design,
    settings: Settings,
    *,
    variance: str = "pooled",
    surface: str = "midthickness",
    smoothness: bool = True,
    permutations: int = 0,
    cluster_forming: float = 3.0,
    extent: int = 1,
    direction: str = "two_sided",
    tfce: bool = False,
    tfce_settings: Optional[TFCESettings] = None,
    column: int = 0,
    seed: Optional[int] = 0,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> GroupAnalysis:
    """Fit *design*, and build whichever null models were asked for.

    ``permutations=0`` skips the permutation test, which is the slow part;
    ``smoothness=False`` skips the resel estimate and with it the RFT
    correction.  Both are on the honour system: whichever you skip simply has
    no P values in the result, rather than being quietly substituted.

    ``tfce=True`` adds the threshold-free score, computed exactly as PALM
    computes it.  It only becomes inference alongside ``permutations``: the
    observed map is always returned, but the corrected P values need a null.
    """
    report_progress(progress, 0.02, "reading the data")
    dataset = load_dataset(
        design, column=column,
        progress=_scaled(progress, 0.02, 0.45), cancel=cancel,
    )
    warnings: list[str] = []

    report_progress(progress, 0.45, "fitting the model")
    check_cancelled(cancel)
    if variance == "welch":
        if design.kind != "two_sample":
            raise ValueError("the Welch variance model only applies to a two-sample test")
        group = design.matrix[:, 1].astype(bool)
        fit = welch_two_sample(dataset.data, group, mask=dataset.mask, cancel=cancel)
        fit = t_to_z(fit)
        warnings.append(
            "Welch: the degrees of freedom vary by vertex, so the map was "
            "converted to z before correction"
        )
    else:
        fit = fit_glm(
            dataset.data, design.matrix, design.contrast,
            mask=dataset.mask, cancel=cancel,
        )

    stat_map = dataset.as_stat_map(
        fit.t, statistic=fit.statistic,
        df=None if not np.isfinite(fit.dof) else fit.dof,
        name=f"{design.kind}_{design.contrast_name.replace(' ', '')}",
    )

    estimate = None
    random_field = None
    topology = None
    areas: Optional[np.ndarray] = None
    if smoothness:
        report_progress(progress, 0.55, "estimating the smoothness")
        check_cancelled(cancel)
        topology, areas = _surface_context(settings, dataset, surface)
        resl = edge_roughness(fit.residuals, topology)
        estimate = compute_resels(resl, topology, fit.mask, vertex_areas=areas)
        random_field = RandomField(
            resels=estimate.resels,
            n_vertices=estimate.n_vertices,
            dof=fit.dof,
            statistic=fit.statistic,
            direction=direction,
        )
        if estimate.fwhm < 1.5:
            warnings.append(
                f"the estimated smoothness is very low (FWHM "
                f"{estimate.fwhm:.2f} vertices); random field theory assumes a "
                f"field that is smooth relative to the mesh, so prefer the "
                f"permutation result here"
            )
        if not permutations:
            warnings.append(
                "RFT cluster-extent P values are liberal -- in this package's "
                "own null simulations the realised family-wise error rate was "
                "0.10-0.15 against a nominal 0.05. Peak-level RFT is well "
                "calibrated; for cluster-level inference run with permutations "
                "and use those P values"
            )
        elif cluster_forming < 2.8:
            warnings.append(
                f"a cluster-forming threshold of {cluster_forming:g} is low; "
                f"cluster-extent inference gets more liberal and less spatially "
                f"specific as it falls, under both corrections"
            )

    adjacency = None
    engine = None
    tfce_map = None
    if tfce or permutations:
        adjacency = _combined_adjacency(settings, dataset, surface)

    if tfce:
        report_progress(progress, 0.60, "scoring TFCE")
        check_cancelled(cancel)
        if areas is None:
            _, areas = _surface_context(settings, dataset, surface)
        settings_tfce = tfce_settings or PALM_DEFAULT
        engine = TFCEEngine(
            adjacency, areas, mask=dataset.mask, settings=settings_tfce
        )
        tfce_map = engine(_directed(fit.t, direction))
        if not permutations:
            warnings.append(
                "the TFCE score has no null distribution of its own, so "
                "without permutations it is a map to look at rather than a "
                "test; re-run with permutations to get corrected P values"
            )
    else:
        settings_tfce = None

    permutation = None
    if permutations:
        report_progress(progress, 0.65, "building the permutation null")
        check_cancelled(cancel)
        permutation = permutation_test(
            dataset.data, design.matrix, design.contrast, adjacency,
            n_permutations=permutations,
            cluster_forming=cluster_forming,
            extent=extent,
            direction=direction,
            mask=dataset.mask,
            tfce_engine=engine,
            seed=seed,
            progress=_scaled(progress, 0.65, 0.99),
            cancel=cancel,
        )

    report_progress(progress, 1.0, "done")
    analysis = GroupAnalysis(
        design=design,
        fit=fit,
        stat_map=stat_map,
        dataset=dataset,
        smoothness=estimate,
        random_field=random_field,
        permutation=permutation,
        adjacency=adjacency,
        warnings=warnings,
        tfce=tfce_map,
        tfce_settings=settings_tfce,
    )
    log.info("group analysis\n  %s", analysis.summary())
    for message in warnings:
        log.warning("%s", message)
    return analysis


# --------------------------------------------------------------------------- #
# the three tests
# --------------------------------------------------------------------------- #


def one_sample_t(
    participants: Participants,
    settings: Settings,
    *,
    covariates: Sequence[str] = (),
    **kwargs: Any,
) -> GroupAnalysis:
    """Is the mean map different from zero?"""
    design = one_sample_design(participants, covariates=covariates)
    return run_test(design, settings, **kwargs)


def two_sample_t(
    participants: Participants,
    settings: Settings,
    group_column: str,
    *,
    groups: Optional[Sequence[str]] = None,
    covariates: Sequence[str] = (),
    variance: str = "pooled",
    **kwargs: Any,
) -> GroupAnalysis:
    """Do two independent groups differ?"""
    design = two_sample_design(
        participants, group_column, groups=groups, covariates=covariates
    )
    return run_test(design, settings, variance=variance, **kwargs)


def paired_t(
    participants: Participants,
    settings: Settings,
    condition_column: str,
    subject_column: str,
    *,
    conditions: Optional[Sequence[str]] = None,
    covariates: Sequence[str] = (),
    **kwargs: Any,
) -> GroupAnalysis:
    """Do two measurements of the same subjects differ?"""
    design = paired_design(
        participants, condition_column, subject_column,
        conditions=conditions, covariates=covariates,
    )
    return run_test(design, settings, **kwargs)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _directed(values: np.ndarray, direction: str) -> np.ndarray:
    """The map the correction actually scores, given the tail being tested.

    The same expression the permutation loop applies to every rearrangement --
    ``|t|`` two-tailed, as PALM does it -- so that the observed score and its
    null are two values of one function.
    """
    values = np.asarray(values, dtype=float)
    if direction == "two_sided":
        return np.abs(values)
    if direction == "negative":
        return -values
    return values


def _surface_context(
    settings: Settings, dataset: SurfaceDataset, surface: str
):
    """The mesh topology of both hemispheres, and per-vertex area in mm²."""
    surfaces = load_hemisphere_surfaces(settings, surface)
    left, right = surfaces["left"], surfaces["right"]
    if left.n_vertices != dataset.n_left or right.n_vertices != dataset.n_right:
        raise ValueError(
            f"the {surface} surfaces have {left.n_vertices}/{right.n_vertices} "
            f"vertices but the data has {dataset.n_left}/{dataset.n_right}; the "
            f"templates and the data are on different meshes"
        )
    topology = build_topology(
        left.faces, right.faces,
        n_left=dataset.n_left, n_vertices=dataset.n_vertices,
    )
    areas = np.concatenate([
        vertex_areas(left.coords, left.faces),
        vertex_areas(right.coords, right.faces),
    ])
    return topology, areas


def _combined_adjacency(
    settings: Settings, dataset: SurfaceDataset, surface: str
) -> csr_matrix:
    """One block-diagonal graph for both hemispheres.

    Block diagonal, not merged: a cluster must not be able to cross the midline
    just because two vertices happen to be adjacent in the concatenated index.
    """
    from ..io.neighbors import adjacency_from_surface

    surfaces = load_hemisphere_surfaces(settings, surface)
    left = adjacency_from_surface(surfaces["left"].faces, dataset.n_left)
    right = adjacency_from_surface(surfaces["right"].faces, dataset.n_right)
    return csr_matrix(block_diag((left, right), format="csr"))


def _forming_threshold(clusters: ClusterResult) -> float:
    """The height the clusters were formed at, from their own parameters."""
    params = clusters.params
    candidates = [
        abs(v) for v in (params.threshold_positive, params.threshold_negative)
        if v is not None
    ]
    if not candidates:
        raise ValueError(
            "the clusters carry no threshold, so the cluster-forming height "
            "cannot be recovered -- pass cluster_forming= explicitly"
        )
    return float(max(candidates))


def _scaled(progress: Optional[ProgressFn], low: float, high: float):
    """Map a sub-task's 0-1 progress onto a slice of the whole job."""
    if progress is None:
        return None

    def inner(fraction: float, message: str) -> None:
        progress(low + (high - low) * fraction, message)

    return inner
