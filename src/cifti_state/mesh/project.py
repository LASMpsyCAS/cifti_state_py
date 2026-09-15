"""Putting a result onto the mesh the report is written on.

Clusters can be found at any density, but the anatomy they get named against
lives at one: the parcellations this package reads -- Glasser, Schaefer,
Desikan and the rest -- are distributed on fs_LR 32k.  Resampling an atlas
*down* to meet coarser data works, and is what the pipeline used to do, but it
throws away the thing being asked for: a 180-region parcellation carried onto a
10k mesh has whole areas represented by a handful of vertices, and the region
percentages in the report get correspondingly coarse.

So the report goes the other way.  The clusters are projected **up** onto
fs_LR 32k and named there, against the atlas as distributed.  A report is then
a report in one space no matter what density the analysis ran at, and two
studies at different densities produce tables that can be read side by side.

What is measured where
----------------------
The split is not arbitrary -- it follows what each number *is*.

* **Statistic values** -- peak, mean, SD, min, max -- come from the analysis
  density, because those are the data.  An interpolated peak height is a
  number that appears nowhere in the file the user analysed, and reporting one
  would be a small lie.
* **Geometry and anatomy** -- surface area, coordinates, centroid, region
  names and percentages -- come from fs_LR 32k, because that is where the
  atlas is defined and where the finer mesh measures area more faithfully.
* **The peak's address** is mapped rather than recomputed: the fs_LR 32k
  vertex closest to the true peak on the shared registration sphere.  So
  ``peak_value`` is the real value and ``peak_vertex`` is where to find it on
  the report mesh, with no daylight between them.
* **Cluster extent** is reported at both: ``size_vertices`` on the report mesh
  and ``size_vertices_native`` at the analysis density, since the latter is
  what the extent threshold was applied to.

Cluster labels are moved with ``wb_command -label-resample … -largest`` -- one
call per hemisphere for all clusters at once, with each target vertex taking
the cluster covering most of it.  Nothing is averaged, so no vertex is ever
assigned to a cluster that does not exist.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..logging_setup import get_logger
from .resample import DEFAULT_METHOD, ResampleStep, plan_route, resample_metric
from .spaces import MeshError, MeshSpace, parse_mesh
from .templates import MeshLibrary

log = get_logger(__name__)

__all__ = [
    "ReportProjection",
    "project_for_report",
    "project_stat_map",
    "project_clusters",
    "nearest_target_vertices",
]


@dataclass
class ReportProjection:
    """Everything a report needs, expressed on the report mesh."""

    source: MeshSpace
    target: MeshSpace
    stat_map: "object"                     #: SurfaceStatMap on the target mesh
    clusters: "object"                     #: ClusterResult on the target mesh
    surfaces: dict                         #: target midthickness, per hemisphere
    #: native cluster id -> vertex count at the analysis density
    native_sizes: dict[int, int] = field(default_factory=dict)
    #: native cluster id -> peak vertex on the target mesh
    peak_vertices: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"report projected {self.source.name} -> {self.target.name}: "
            f"{self.clusters.n_clusters} clusters"
        )


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #


def project_stat_map(
    stat_map,
    target: "str | MeshSpace",
    settings,
    *,
    source: "Optional[str | MeshSpace]" = None,
    library: Optional[MeshLibrary] = None,
    method: str = DEFAULT_METHOD,
    workdir: Optional[Path] = None,
):
    """Resample a :class:`~cifti_state.io.cifti.SurfaceStatMap` onto *target*.

    The map's own ``present`` mask is passed as ``-current-roi`` so the medial
    wall does not bleed outward, and the target's ``atlasroi`` becomes the new
    present mask when the templates provide one.
    """
    from ..io.cifti import HemiSurfaceData, SurfaceStatMap, make_dense_surface_template

    library = library or settings.mesh_library()
    source_mesh = _resolve_source(source, stat_map.left.n_vertices, settings)
    target_mesh = parse_mesh(target)
    steps = plan_route(source_mesh, target_mesh, library)
    if not steps:
        return stat_map

    temp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="cifti_state_proj_"))
    temp.mkdir(parents=True, exist_ok=True)
    try:
        hemis: dict[str, HemiSurfaceData] = {}
        for hemi in ("left", "right"):
            side = stat_map.hemi(hemi)
            values = np.asarray(side.values, dtype=np.float64)
            present = np.asarray(side.present, dtype=bool)

            current = temp / f"{hemi}_values.func.gii"
            _write_metric(current, values.astype(np.float32))
            roi_path = None
            if not present.all():
                roi_path = temp / f"{hemi}_roi.shape.gii"
                _write_metric(roi_path, present.astype(np.float32))

            for index, step in enumerate(steps):
                out_values = temp / f"{hemi}_values_{index}.func.gii"
                out_roi = temp / f"{hemi}_roi_{index}.shape.gii"
                resample_metric(
                    current, out_values, step, hemi, library, settings,
                    method=method, current_roi=roi_path,
                )
                if roi_path is not None:
                    resample_metric(
                        roi_path, out_roi, step, hemi, library, settings, method=method,
                    )
                    fraction = np.nan_to_num(_read_metric(out_roi).ravel(), nan=0.0)
                    _write_metric(out_roi, (fraction >= 0.5).astype(np.float32))
                    roi_path = out_roi
                current = out_values

            projected = _read_metric(current).ravel()
            target_files = library.files_for(target_mesh)
            atlasroi = target_files.roi(hemi)
            if atlasroi is not None:
                new_present = _read_metric(atlasroi).ravel() > 0.5
            elif roi_path is not None:
                new_present = _read_metric(roi_path).ravel() > 0.5
            else:
                new_present = np.ones(projected.size, dtype=bool)

            filled = np.where(new_present, projected, stat_map.fill)
            hemis[hemi] = HemiSurfaceData(
                values=filled,
                present=new_present,
                vertex_index=np.flatnonzero(new_present),
                n_vertices=int(projected.size),
                hemisphere=hemi,
            )

        template = make_dense_surface_template(
            hemis["left"].n_vertices, hemis["right"].n_vertices,
            left_vertices=hemis["left"].vertex_index,
            right_vertices=hemis["right"].vertex_index,
        )
        return SurfaceStatMap(
            left=hemis["left"], right=hemis["right"],
            statistic=stat_map.statistic, df=stat_map.df,
            template=template,
            name=stat_map.name, source_path=stat_map.source_path, fill=stat_map.fill,
        )
    finally:
        if workdir is None:
            shutil.rmtree(temp, ignore_errors=True)


def project_clusters(
    clusters,
    target: "str | MeshSpace",
    settings,
    *,
    source: "Optional[str | MeshSpace]" = None,
    library: Optional[MeshLibrary] = None,
    workdir: Optional[Path] = None,
):
    """Move a :class:`~cifti_state.core.cluster.ClusterResult` onto *target*.

    One ``-label-resample … -largest`` per hemisphere carries every cluster at
    once.  Cluster ids, hemispheres and signs are preserved; the vertex counts
    are recomputed on the target mesh.  A cluster that no target vertex claims
    -- possible only when projecting downward -- is dropped, and named in the
    warnings rather than silently disappearing.
    """
    from ..core.cluster import ClusterInfo, ClusterResult

    library = library or settings.mesh_library()
    source_mesh = _resolve_source(source, clusters.labels_left.size, settings)
    target_mesh = parse_mesh(target)
    steps = plan_route(source_mesh, target_mesh, library)
    if not steps:
        return clusters, []

    by_id = {info.cluster_id: info for info in clusters.clusters}
    temp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="cifti_state_lab_"))
    temp.mkdir(parents=True, exist_ok=True)
    try:
        projected: dict[str, np.ndarray] = {}
        for hemi in ("left", "right"):
            labels = np.asarray(clusters.labels(hemi), dtype=np.int32)
            current = temp / f"{hemi}_labels.label.gii"
            _write_label(current, labels)
            for index, step in enumerate(steps):
                out_path = temp / f"{hemi}_labels_{index}.label.gii"
                _label_resample(current, out_path, step, hemi, library, settings)
                current = out_path
            projected[hemi] = _read_label(current)

        infos: list[ClusterInfo] = []
        lost: list[int] = []
        for cluster_id in sorted(by_id):
            info = by_id[cluster_id]
            vertices = np.flatnonzero(projected[info.hemisphere] == cluster_id)
            if vertices.size == 0:
                lost.append(cluster_id)
                continue
            infos.append(ClusterInfo(
                cluster_id=cluster_id,
                hemisphere=info.hemisphere,
                sign=info.sign,
                size_vertices=int(vertices.size),
                min_vertex=int(vertices.min()),
            ))

        warnings: list[str] = []
        if lost:
            warnings.append(
                f"cluster(s) {', '.join(str(c) for c in lost)} have no vertices "
                f"on {target_mesh.name} and were left out of the report; they "
                f"are smaller than that mesh's vertex spacing"
            )

        return ClusterResult(
            labels_left=projected["left"],
            labels_right=projected["right"],
            clusters=infos,
            params=clusters.params,
            source_name=clusters.source_name,
        ), warnings
    finally:
        if workdir is None:
            shutil.rmtree(temp, ignore_errors=True)


def nearest_target_vertices(
    hemi: str,
    vertices,
    source: "str | MeshSpace",
    target: "str | MeshSpace",
    settings,
    *,
    library: Optional[MeshLibrary] = None,
) -> np.ndarray:
    """The target-mesh vertices closest to *vertices* on the shared sphere.

    Used for the peak: its value is the one measured in the data, so the
    honest thing to report alongside is the target vertex that sits where that
    peak actually is -- not the argmax of an interpolated map, which can land
    one vertex over and carry a different value.
    """
    from scipy.spatial import cKDTree

    from ..io.surface import load_surface

    library = library or settings.mesh_library()
    source_mesh = parse_mesh(source)
    target_mesh = parse_mesh(target)
    steps = plan_route(source_mesh, target_mesh, library)
    if not steps:
        return np.asarray(vertices, dtype=int)
    if len(steps) > 1:
        # Chain through the intermediate meshes the route uses.
        current = np.asarray(vertices, dtype=int)
        for step in steps:
            current = nearest_target_vertices(
                hemi, current, step.source, step.target, settings, library=library
            )
        return current

    step = steps[0]
    source_sphere = library.files_for(source_mesh).sphere(hemi, step.registration)
    target_sphere = library.files_for(target_mesh).sphere(hemi, step.registration)
    source_xyz = load_surface(source_sphere).coords
    target_xyz = load_surface(target_sphere).coords
    tree = cKDTree(target_xyz)
    _, index = tree.query(source_xyz[np.asarray(vertices, dtype=int)])
    return np.asarray(index, dtype=int)


# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #


def project_for_report(
    stat_map,
    clusters,
    target: "str | MeshSpace",
    settings,
    *,
    source: "Optional[str | MeshSpace]" = None,
    library: Optional[MeshLibrary] = None,
    surface_kind: str = "midthickness",
) -> Optional[ReportProjection]:
    """Project a finished analysis onto the report mesh.

    ``None`` when the analysis is already there, which is the common case and
    costs nothing.
    """
    library = library or settings.mesh_library()
    source_mesh = _resolve_source(source, stat_map.left.n_vertices, settings)
    target_mesh = parse_mesh(target)
    if source_mesh == target_mesh:
        return None

    from ..io.surface import load_surface

    log.info(
        "projecting the report from %s to %s", source_mesh.name, target_mesh.name
    )
    projected_map = project_stat_map(
        stat_map, target_mesh, settings, source=source_mesh, library=library
    )
    projected_clusters, warnings = project_clusters(
        clusters, target_mesh, settings, source=source_mesh, library=library
    )

    surfaces = {
        hemi: load_surface(
            settings.surface_for(hemi, surface_kind, mesh=target_mesh.name),
            hemisphere=hemi, kind=surface_kind,
        )
        for hemi in ("left", "right")
    }

    native_sizes = {info.cluster_id: info.size_vertices for info in clusters.clusters}

    # Map each cluster's peak to the report mesh, one query per hemisphere.
    peak_vertices: dict[int, int] = {}
    for hemi in ("left", "right"):
        wanted = [
            (info.cluster_id, _peak_vertex(stat_map, clusters, info))
            for info in clusters.clusters if info.hemisphere == hemi
        ]
        if not wanted:
            continue
        mapped = nearest_target_vertices(
            hemi, [v for _, v in wanted], source_mesh, target_mesh, settings,
            library=library,
        )
        for (cluster_id, _), target_vertex in zip(wanted, mapped):
            peak_vertices[cluster_id] = int(target_vertex)

    return ReportProjection(
        source=source_mesh,
        target=target_mesh,
        stat_map=projected_map,
        clusters=projected_clusters,
        surfaces=surfaces,
        native_sizes=native_sizes,
        peak_vertices=peak_vertices,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _peak_vertex(stat_map, clusters, info) -> int:
    """The vertex carrying the cluster's largest |value| at the analysis density."""
    values = np.asarray(stat_map.hemi(info.hemisphere).values, dtype=float)
    members = np.flatnonzero(clusters.labels(info.hemisphere) == info.cluster_id)
    magnitude = np.abs(np.nan_to_num(values[members], nan=0.0))
    return int(members[int(np.argmax(magnitude))])


def _resolve_source(source, n_vertices: int, settings) -> MeshSpace:
    if source is not None:
        return parse_mesh(source)
    return settings.mesh_of(n_vertices)


def _label_resample(in_path, out_path, step: ResampleStep, hemi, library, settings):
    from .resample import resample_label_gifti

    resample_label_gifti(in_path, out_path, step, hemi, library, settings)


def _write_metric(path: Path, values: np.ndarray) -> None:
    from .resample import _write_metric as write

    write(path, values)


def _read_metric(path) -> np.ndarray:
    from .resample import _read_metric as read

    return read(path)


def _write_label(path: Path, labels: np.ndarray) -> None:
    """Write a label GIFTI with one entry per cluster, plus the background."""
    import nibabel as nib

    labels = np.asarray(labels, dtype=np.int32)
    table = nib.gifti.GiftiLabelTable()
    background = nib.gifti.GiftiLabel(key=0, red=1.0, green=1.0, blue=1.0, alpha=0.0)
    background.label = "???"
    table.labels.append(background)
    rng = np.random.default_rng(0)
    for value in np.unique(labels):
        if value == 0:
            continue
        colour = rng.random(3)
        entry = nib.gifti.GiftiLabel(
            key=int(value), red=float(colour[0]), green=float(colour[1]),
            blue=float(colour[2]), alpha=1.0,
        )
        entry.label = f"cluster_{int(value)}"
        table.labels.append(entry)

    darray = nib.gifti.GiftiDataArray(
        labels,
        intent=nib.nifti1.intent_codes.code["NIFTI_INTENT_LABEL"],
        datatype=nib.nifti1.data_type_codes.code[np.int32],
    )
    nib.save(nib.gifti.GiftiImage(darrays=[darray], labeltable=table), str(path))


def _read_label(path) -> np.ndarray:
    import nibabel as nib

    return np.asarray(nib.load(str(path)).darrays[0].data, dtype=np.int32)
