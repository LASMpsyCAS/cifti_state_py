"""Moving a surface map from one mesh to another.

The operation itself is Workbench's, not this package's.  ``wb_command
-metric-resample`` with ``ADAP_BARY_AREA`` is what the field uses and what the
HCP pipelines are built on: barycentric interpolation on the registration
sphere, weighted so that each target vertex is an area-weighted average of the
source vertices it actually covers.  That last part matters whenever the two
meshes differ in density -- plain barycentric interpolation asks only "which
triangle am I in", and downsampling that way throws away most of the data and
biases whatever survives.

So this module does not reimplement any of it.  What it adds is everything
around the call, which is where surface resampling normally goes wrong:

* **the right spheres.** fs_LR to fs_LR uses each mesh's own sphere; fs_LR to
  fsaverage uses the ``fs_LR-deformed_to-fsaverage`` sphere on one side and the
  fsaverage standard sphere on the other.  Getting this pair wrong produces a
  map that is smooth, plausible, and rotated with respect to the anatomy.
* **the area metrics.** ``ADAP_BARY_AREA`` without ``-area-metrics`` quietly
  degrades to something close to plain barycentric.  They are never optional
  here: a mesh without its area metric cannot be an end of a conversion, and
  says so.
* **the medial wall.** A 91k or 59k file has no data there.  Resampled without
  ``-current-roi``, the absent wall bleeds into the vertices beside it; the
  output is then wrong in exactly the ring where cortex is thinnest and
  clusters most often sit.
* **NaN.** A thresholded map stores NaN for the vertices that did not survive.
  Barycentric interpolation spreads each NaN over every target vertex whose
  triangle touches it, so a map that was 10% NaN can come back 40% NaN.
  ``nan="mask"`` treats them as absent data instead of as values.
* **routing.** fs_LR 10k has no fsaverage-deformed sphere, so
  ``fsLR:10k -> fsaverage5`` cannot be done in one step.  It is done in two,
  through fs_LR 32k, and the extra smoothing that costs is reported rather
  than hidden.

The reference for all of it is the shell script in ``example_data/`` -- the
sequence separate, resample each hemisphere, recombine -- which this module
reproduces and then makes safe to run on a thousand subjects.

Why not neuromaps?  It is a good package and it solves the same problem, but
its surface transforms shell out to these same ``wb_command`` calls; what it
adds is fetching the template files from OSF.  Those files are already here.
:func:`resample_cifti` therefore calls Workbench directly -- one dependency
fewer, no download, and a call trace that can be pasted into a terminal.  If
neuromaps is installed it can still be used as the backend, for people who
would rather have its atlas management: pass ``backend="neuromaps"``.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ..logging_setup import get_logger
from ..types import CancelToken, ProgressFn, check_cancelled, report_progress
from ..wb import run_wb
from .spaces import MeshError, MeshSpace, identify_mesh, parse_mesh
from .templates import MeshLibrary, common_registration

log = get_logger(__name__)

__all__ = [
    "ResampleStep",
    "ResampleResult",
    "plan_route",
    "resample_metric",
    "resample_cifti",
    "resample_label_gifti",
    "METHODS",
    "DEFAULT_METHOD",
]

#: Methods ``wb_command`` offers for metric data.
METHODS: tuple[str, ...] = ("ADAP_BARY_AREA", "BARYCENTRIC")

#: What HCP uses, and the only sensible choice when the densities differ.
DEFAULT_METHOD = "ADAP_BARY_AREA"

_STRUCTURE = {"left": "CORTEX_LEFT", "right": "CORTEX_RIGHT"}


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResampleStep:
    """One ``-metric-resample`` hop, and the sphere space it happens in."""

    source: MeshSpace
    target: MeshSpace
    registration: str

    def describe(self) -> str:
        return f"{self.source.name} -> {self.target.name} (on the {self.registration} sphere)"


def plan_route(
    source: "str | MeshSpace",
    target: "str | MeshSpace",
    library: MeshLibrary,
    *,
    via: Optional[Sequence["str | MeshSpace"]] = None,
) -> list[ResampleStep]:
    """The sequence of hops that gets from *source* to *target*.

    Usually one.  Two when the pair has no sphere space in common but each can
    reach an intermediate mesh that does -- fs_LR 10k to fsaverage5 goes
    through fs_LR 32k, because 10k has no fsaverage-deformed sphere.  Every
    hop resamples, and resampling smooths a little, so a two-hop route is
    reported by the caller rather than passed over.
    """
    source = parse_mesh(source)
    target = parse_mesh(target)
    if source == target:
        return []

    if via:
        chain = [source, *[parse_mesh(v) for v in via], target]
        steps = []
        for a, b in zip(chain, chain[1:]):
            registration = common_registration(a, b)
            if registration is None:
                raise MeshError(
                    f"{a.name} and {b.name} have no sphere space in common, so "
                    f"the route you asked for cannot be taken"
                )
            steps.append(ResampleStep(a, b, registration))
        return steps

    registration = common_registration(source, target)
    if registration is not None and _can_meet(source, target, registration, library):
        return [ResampleStep(source, target, registration)]

    # Try one intermediate mesh. Prefer the densest available one, since the
    # intermediate is where information is lost.
    best: Optional[list[ResampleStep]] = None
    for middle in sorted(library.available(), key=lambda m: -m.n_vertices):
        if middle in (source, target):
            continue
        first = common_registration(source, middle)
        second = common_registration(middle, target)
        if first is None or second is None:
            continue
        if not _can_meet(source, middle, first, library):
            continue
        if not _can_meet(middle, target, second, library):
            continue
        best = [
            ResampleStep(source, middle, first),
            ResampleStep(middle, target, second),
        ]
        break
    if best is not None:
        return best

    raise MeshError(
        f"cannot get from {source.name} to {target.name} with the templates "
        f"available.\n{library.report()}"
    )


def _can_meet(a: MeshSpace, b: MeshSpace, registration: str, library: MeshLibrary) -> bool:
    """Does the library hold both spheres in *registration*, plus both areas?"""
    for mesh in (a, b):
        files = library.files_for(mesh)
        if not files.has("area"):
            return False
        role = "sphere" if registration == mesh.registration else f"sphere@{registration}"
        if not files.has(role):
            return False
    return True


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


@dataclass
class ResampleResult:
    """What a conversion did, in enough detail to reproduce or audit it."""

    output: Path
    source: MeshSpace
    target: MeshSpace
    steps: list[ResampleStep]
    method: str
    kind: str                       #: dscalar | dtseries | dlabel
    n_maps: int
    used_roi: bool
    medial_wall: str                #: excluded | included
    commands: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        route = " -> ".join([self.source.name] + [s.target.name for s in self.steps])
        return (
            f"{route} [{self.method}], {self.n_maps} map(s), "
            f"medial wall {self.medial_wall}"
            + (", ROI-masked" if self.used_roi else "")
        )

    def to_dict(self) -> dict:
        return {
            "output": str(self.output),
            "source": self.source.name,
            "target": self.target.name,
            "route": [s.describe() for s in self.steps],
            "method": self.method,
            "kind": self.kind,
            "n_maps": int(self.n_maps),
            "used_roi": bool(self.used_roi),
            "medial_wall": self.medial_wall,
            "warnings": list(self.warnings),
            "commands": list(self.commands),
        }


# --------------------------------------------------------------------------- #
# one hemisphere, one hop
# --------------------------------------------------------------------------- #


def resample_metric(
    metric_in: Path | str,
    metric_out: Path | str,
    step: ResampleStep,
    hemi: str,
    library: MeshLibrary,
    settings,
    *,
    method: str = DEFAULT_METHOD,
    current_roi: Optional[Path | str] = None,
    valid_roi_out: Optional[Path | str] = None,
) -> list[str]:
    """One ``wb_command -metric-resample``. Returns the command line it ran."""
    if method not in METHODS:
        raise MeshError(f"unknown method {method!r}; use one of {', '.join(METHODS)}")

    source_files = library.files_for(step.source)
    target_files = library.files_for(step.target)
    args: list[str] = [
        "-metric-resample",
        str(metric_in),
        str(source_files.sphere(hemi, step.registration)),
        str(target_files.sphere(hemi, step.registration)),
        method,
        str(metric_out),
    ]
    if method == "ADAP_BARY_AREA":
        # Never optional: without these the weighting is not area-corrected and
        # the method is ADAP_BARY_AREA in name only.
        args += [
            "-area-metrics",
            str(source_files.area(hemi)),
            str(target_files.area(hemi)),
        ]
    if current_roi is not None:
        args += ["-current-roi", str(current_roi)]
    if valid_roi_out is not None:
        args += ["-valid-roi-out", str(valid_roi_out)]

    result = run_wb(args, settings, outputs=[Path(metric_out)])
    return [result.command_line]


def resample_label_gifti(
    label_in: Path | str,
    label_out: Path | str,
    step: ResampleStep,
    hemi: str,
    library: MeshLibrary,
    settings,
    *,
    current_roi: Optional[Path | str] = None,
) -> list[str]:
    """``wb_command -label-resample``, for parcellations rather than data.

    Labels are categorical, so an interpolated value would be meaningless:
    ``-largest`` makes each target vertex take the label with the greatest
    weight rather than a weighted average.  This is what lets an atlas defined
    on fs_LR 32k be used on any other mesh.
    """
    source_files = library.files_for(step.source)
    target_files = library.files_for(step.target)
    args: list[str] = [
        "-label-resample",
        str(label_in),
        str(source_files.sphere(hemi, step.registration)),
        str(target_files.sphere(hemi, step.registration)),
        "ADAP_BARY_AREA",
        str(label_out),
        "-area-metrics",
        str(source_files.area(hemi)),
        str(target_files.area(hemi)),
        "-largest",
    ]
    if current_roi is not None:
        args += ["-current-roi", str(current_roi)]
    result = run_wb(args, settings, outputs=[Path(label_out)])
    return [result.command_line]


# --------------------------------------------------------------------------- #
# a whole CIFTI file
# --------------------------------------------------------------------------- #


def resample_cifti(
    in_path: Path | str,
    out_path: Path | str,
    target: "str | MeshSpace",
    settings,
    *,
    source: "Optional[str | MeshSpace]" = None,
    library: Optional[MeshLibrary] = None,
    method: str = DEFAULT_METHOD,
    use_roi: bool = True,
    medial_wall: str = "auto",
    nan: str = "propagate",
    coverage: float = 0.5,
    discrete: "str | bool" = "auto",
    via: Optional[Sequence["str | MeshSpace"]] = None,
    backend: str = "wb",
    workdir: Optional[Path | str] = None,
    keep_intermediates: bool = False,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[CancelToken] = None,
) -> ResampleResult:
    """Resample a whole CIFTI file onto another mesh.

    Parameters
    ----------
    target, source
        Mesh names -- ``"fsLR:10k"``, ``"fsaverage5"``, or a bare density like
        ``"32k"`` meaning fs_LR.  *source* is read from the file when it can be
        told apart; it has to be given when the vertex count is ambiguous
        (10242 is both fs_LR 10k and fsaverage5).
    use_roi
        Pass the source's medial-wall mask as ``-current-roi``, so absent
        vertices do not bleed into their neighbours.  Turn it off only to
        reproduce a pipeline that did not do this.
    medial_wall
        ``"auto"`` gives the output the same shape as the input -- a file that
        excluded the wall still excludes it, a dense file stays dense.
        ``"exclude"`` and ``"include"`` force it either way.
    nan
        ``"propagate"`` is what Workbench does: a NaN spreads to every target
        vertex whose triangle touches it, so a map that was 10% NaN can come
        back 40% NaN.  ``"mask"`` treats NaN as absent data instead -- which
        is what a thresholded map means by it -- and puts the holes back
        afterwards, so the map keeps roughly the extent it had.
    coverage
        How much of a target vertex's catchment has to be real data before it
        keeps a value, between 0 and 1.  The default 0.5 is the natural cut:
        below it the vertex was mostly built from absent data.  Lower dilates
        the data, higher erodes it.
    discrete
        A network mask or a parcellation stored as a plain dscalar holds label
        *numbers*, and averaging those is meaningless -- interpolating a map of
        networks 3 and 5 invents a network 4 along the border.  ``"auto"``
        notices an integer-valued map with few distinct values and gives every
        target vertex the label covering most of it instead, which is what
        ``wb_command``'s ``-largest`` does for a real dlabel.  ``False`` forces
        the ordinary continuous path; ``True`` forces the discrete one.
    backend
        ``"wb"`` (the default) or ``"neuromaps"``.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    if backend == "neuromaps":
        return _resample_via_neuromaps(
            in_path, out_path, target, settings, source=source, method=method
        )
    if backend != "wb":
        raise MeshError(f"unknown backend {backend!r}; use 'wb' or 'neuromaps'")
    if medial_wall not in ("auto", "exclude", "include"):
        raise MeshError(
            f"medial_wall must be auto, exclude or include, not {medial_wall!r}"
        )
    if nan not in ("propagate", "mask"):
        raise MeshError(f"nan must be 'propagate' or 'mask', not {nan!r}")
    if discrete not in ("auto", True, False):
        raise MeshError(f"discrete must be 'auto', True or False, not {discrete!r}")

    library = library or MeshLibrary.from_settings(settings)
    info = _inspect_cifti(in_path)
    source_mesh = (
        parse_mesh(source) if source is not None
        else identify_mesh(
            info["n_vertices_left"], prefer=getattr(settings.defaults, "mesh", None)
        )
    )
    target_mesh = parse_mesh(target)

    if info["n_vertices_left"] != source_mesh.n_vertices:
        raise MeshError(
            f"{in_path.name} has {info['n_vertices_left']} vertices per "
            f"hemisphere but {source_mesh.name} has {source_mesh.n_vertices}"
        )

    steps = plan_route(source_mesh, target_mesh, library, via=via)
    warnings: list[str] = []
    if not steps:
        shutil.copyfile(in_path, out_path)
        log.info("%s is already on %s; copied unchanged", in_path.name, target_mesh.name)
        return ResampleResult(
            output=out_path, source=source_mesh, target=target_mesh, steps=[],
            method=method, kind=info["kind"], n_maps=info["n_maps"],
            used_roi=False, medial_wall="unchanged",
        )
    if len(steps) > 1:
        warnings.append(
            "no direct route, so this goes via "
            + " and ".join(s.target.name for s in steps[:-1])
            + "; each hop interpolates, so the result is slightly smoother than "
            "a single-step conversion would be"
        )

    commands: list[str] = []
    temp_root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="cifti_state_rs_"))
    temp_root.mkdir(parents=True, exist_ok=True)

    try:
        report_progress(progress, 0.05, "separating hemispheres")
        check_cancelled(cancel)

        is_label = info["kind"] == "dlabel"
        suffix = ".label.gii" if is_label else ".func.gii"
        current: dict[str, Path] = {}
        # Two masks, deliberately kept apart. *wall* is where the file has
        # vertices at all -- it decides the output's layout. *rois* is what the
        # interpolation is allowed to draw on, which is narrower whenever NaN
        # is being masked. Conflating them is what turns a thresholded map into
        # a CIFTI file with 1234 greyordinates in it.
        wall: dict[str, Optional[Path]] = {"left": None, "right": None}
        rois: dict[str, Optional[Path]] = {"left": None, "right": None}

        for hemi in ("left", "right"):
            metric = temp_root / f"{hemi}_{source_mesh.density}{suffix}"
            roi = temp_root / f"{hemi}_{source_mesh.density}.roi.shape.gii"
            args: list[str] = ["-cifti-separate", str(in_path), "COLUMN"]
            args += ["-label" if is_label else "-metric", _STRUCTURE[hemi], str(metric)]
            outputs = [metric]
            if info["has_medial_wall"]:
                args += ["-roi", str(roi)]
                outputs.append(roi)
            commands.append(run_wb(args, settings, outputs=outputs).command_line)
            current[hemi] = metric
            if info["has_medial_wall"]:
                wall[hemi] = roi
                rois[hemi] = roi

        # Is this a map of label numbers rather than measurements? Averaging
        # those would invent labels that are not in the data, so it is worth
        # noticing before the first interpolation rather than after.
        use_largest = bool(discrete) and not is_label
        if discrete == "auto" and not is_label:
            use_largest = info["kind"] != "dtseries" and all(
                _looks_discrete(current[hemi]) for hemi in ("left", "right")
            )
            if use_largest:
                warnings.append(
                    "the values are whole numbers with few distinct levels, so "
                    "this was treated as a parcellation: every target vertex "
                    "takes the label covering most of it, rather than an "
                    "average of its neighbours. Pass discrete=False "
                    "(--continuous) if it really is measured data"
                )
        if use_largest:
            nan = "propagate"      # a label map has no NaN to mask

        if nan == "mask" and not is_label:
            for hemi in ("left", "right"):
                masked = _fold_nan_into_roi(
                    current[hemi], wall[hemi],
                    temp_root / f"{hemi}_{source_mesh.density}.roi_nan.shape.gii",
                )
                if masked is not None and masked != wall[hemi]:
                    rois[hemi] = masked
                    warnings.append(
                        f"{hemi}: NaN vertices were treated as absent data rather "
                        f"than interpolated"
                    )
        elif not is_label:
            for hemi in ("left", "right"):
                if _has_nan(current[hemi]):
                    warnings.append(
                        f"{hemi} hemisphere contains NaN; barycentric interpolation "
                        f"spreads each one over its neighbourhood, so the output "
                        f"will have more NaN than the input. Pass nan='mask' "
                        f"(--nan mask) to treat them as absent data instead"
                    )
                    break

        used_roi = use_roi and any(r is not None for r in rois.values())

        for index, step in enumerate(steps):
            report_progress(
                progress, 0.15 + 0.6 * index / len(steps), step.describe()
            )
            check_cancelled(cancel)
            log.info("resampling %s", step.describe())
            for hemi in ("left", "right"):
                out_metric = temp_root / f"{hemi}_{step.target.density}_{index}{suffix}"
                out_roi = temp_root / f"{hemi}_{step.target.density}_{index}.roi.shape.gii"
                if is_label:
                    commands += resample_label_gifti(
                        current[hemi], out_metric, step, hemi, library, settings,
                        current_roi=rois[hemi] if use_roi else None,
                    )
                elif use_largest:
                    commands += _resample_discrete(
                        current[hemi], out_metric, step, hemi, library, settings,
                        method=method,
                        current_roi=rois[hemi] if use_roi else None,
                        work=temp_root / f"{hemi}_{step.target.density}_{index}_lab",
                    )
                else:
                    commands += resample_metric(
                        current[hemi], out_metric, step, hemi, library, settings,
                        method=method,
                        current_roi=rois[hemi] if use_roi else None,
                        valid_roi_out=out_roi if (use_roi and rois[hemi]) else None,
                    )
                current[hemi] = out_metric
                if use_roi and rois[hemi] is not None and not is_label:
                    # How much of each target vertex's catchment was real data?
                    # Resampling the 0/1 mask with the same weights answers
                    # that directly, and it is the only honest way to decide
                    # which target vertices deserve a value: -valid-roi-out
                    # keeps a vertex that caught a single valid source, which
                    # dilates a thresholded blob, and no mask at all lets NaN
                    # erode it. A half-covered vertex is the natural cut.
                    stem = f"{hemi}_{step.target.density}_{index}"
                    covered, cmds = _coverage_mask(
                        rois[hemi], step, hemi, library, settings, method,
                        temp_root / f"{stem}.cov.shape.gii", threshold=coverage,
                    )
                    commands += cmds
                    if nan == "mask":
                        # Put the holes back where they were: the vertices that
                        # were not covered are the ones the NaN occupied.
                        _write_nan_outside(out_metric, covered)
                    if wall[hemi] is not None and wall[hemi] != rois[hemi]:
                        # The wall moved too, and it moves differently -- it is
                        # a different mask. Carry it forward on its own.
                        wall[hemi], cmds = _coverage_mask(
                            wall[hemi], step, hemi, library, settings, method,
                            temp_root / f"{stem}.wall.shape.gii", threshold=coverage,
                        )
                        commands += cmds
                    else:
                        wall[hemi] = covered
                    rois[hemi] = covered
                elif use_roi and rois[hemi] is not None and out_roi.exists():
                    rois[hemi] = out_roi
                    wall[hemi] = out_roi
                elif not is_label:
                    rois[hemi] = None
                    wall[hemi] = None

        # The output's medial wall: the target mesh's own atlasroi when we have
        # it (standard, and identical across subjects), otherwise whatever the
        # resampling reported as valid.
        want_wall_excluded = (
            info["has_medial_wall"] if medial_wall == "auto" else medial_wall == "exclude"
        )
        out_rois: dict[str, Optional[Path]] = {"left": None, "right": None}
        if want_wall_excluded:
            target_files = library.files_for(target_mesh)
            for hemi in ("left", "right"):
                out_rois[hemi] = target_files.roi(hemi) or wall[hemi]
            if any(r is None for r in out_rois.values()):
                warnings.append(
                    f"{target_mesh.name} has no atlasroi and the resampling "
                    f"produced no valid-vertex mask, so the output keeps every "
                    f"vertex including the medial wall"
                )
                out_rois = {"left": None, "right": None}
                want_wall_excluded = False

        report_progress(progress, 0.85, "writing the CIFTI file")
        check_cancelled(cancel)
        commands.append(
            _create_cifti(
                out_path, current, out_rois, info, settings, temp_root, is_label
            )
        )
        report_progress(progress, 1.0, "done")

        result = ResampleResult(
            output=out_path,
            source=source_mesh,
            target=target_mesh,
            steps=steps,
            method=method,
            kind=info["kind"],
            n_maps=info["n_maps"],
            used_roi=used_roi,
            medial_wall="excluded" if want_wall_excluded else "included",
            commands=commands,
            warnings=warnings,
        )
        log.info("resampled: %s", result.describe())
        for message in warnings:
            log.warning("%s", message)
        return result
    finally:
        if not keep_intermediates and workdir is None:
            shutil.rmtree(temp_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _inspect_cifti(path: Path) -> dict[str, Any]:
    """What kind of CIFTI is this, how many maps, and does it drop the wall?"""
    import nibabel as nib

    img = nib.load(str(path))
    if not isinstance(img, nib.cifti2.Cifti2Image):
        raise MeshError(f"{path} is not a CIFTI file")
    axes = [img.header.get_axis(i) for i in range(img.ndim)]
    brain = axes[-1]
    row = axes[0] if len(axes) > 1 else None

    counts: dict[str, int] = {}
    present: dict[str, int] = {}
    for name, sl, model in brain.iter_structures():
        key = str(name)
        if key.endswith("CORTEX_LEFT"):
            side = "left"
        elif key.endswith("CORTEX_RIGHT"):
            side = "right"
        else:
            continue
        counts[side] = int(model.nvertices[key])
        present[side] = int(np.asarray(model.vertex).size)
    if "left" not in counts or "right" not in counts:
        raise MeshError(
            f"{path.name} has no cortical surface structures; this converts "
            f"cortical surface data only"
        )
    if counts["left"] != counts["right"]:
        raise MeshError(
            f"{path.name} has {counts['left']} left and {counts['right']} right "
            f"vertices; the two hemispheres must be on the same mesh"
        )

    kind = "dscalar"
    map_names: list[str] = []
    series: Optional[dict] = None
    if row is not None:
        if isinstance(row, nib.cifti2.SeriesAxis):
            kind = "dtseries"
            series = {
                "step": float(row.step),
                "start": float(row.start),
                "unit": str(row.unit),
            }
        elif isinstance(row, nib.cifti2.LabelAxis):
            kind = "dlabel"
            map_names = [str(n) for n in row.name]
        elif isinstance(row, nib.cifti2.ScalarAxis):
            map_names = [str(n) for n in row.name]
    if path.name.endswith(".dlabel.nii"):
        kind = "dlabel"

    return {
        "kind": kind,
        "n_maps": int(img.shape[0]) if img.ndim > 1 else 1,
        "n_vertices_left": counts["left"],
        "n_vertices_right": counts["right"],
        "has_medial_wall": present["left"] < counts["left"]
        or present["right"] < counts["right"],
        "map_names": map_names,
        "series": series,
        "n_columns": int(brain.size),
        "has_volume": any(
            getattr(model, "volume_shape", None) is not None
            for _, _, model in brain.iter_structures()
        ),
    }


def _create_cifti(
    out_path: Path,
    metrics: dict[str, Path],
    rois: dict[str, Optional[Path]],
    info: dict[str, Any],
    settings,
    temp_root: Path,
    is_label: bool,
) -> str:
    """Reassemble the two hemispheres into a CIFTI of the input's own kind."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kind = info["kind"]
    if is_label:
        args: list[str] = ["-cifti-create-label", str(out_path)]
        left_flag, right_flag = "-left-label", "-right-label"
    elif kind == "dtseries":
        args = ["-cifti-create-dense-timeseries", str(out_path)]
        left_flag, right_flag = "-left-metric", "-right-metric"
    else:
        args = ["-cifti-create-dense-scalar", str(out_path)]
        left_flag, right_flag = "-left-metric", "-right-metric"

    for hemi, flag in (("left", left_flag), ("right", right_flag)):
        args += [flag, str(metrics[hemi])]
        if rois.get(hemi) is not None:
            args += [f"-roi-{hemi}", str(rois[hemi])]

    if kind == "dtseries" and info["series"]:
        args += [
            "-timestep", str(info["series"]["step"]),
            "-timestart", str(info["series"]["start"]),
        ]
        unit = info["series"]["unit"]
        if unit:
            args += ["-unit", str(unit).upper()]
    elif kind == "dscalar" and info["map_names"]:
        # Keep the map names: a dscalar whose maps are called "map 1" and
        # "map 2" after a conversion is a small loss that is annoying to undo.
        name_file = temp_root / "map_names.txt"
        name_file.write_text("\n".join(info["map_names"]) + "\n", encoding="utf-8")
        args += ["-name-file", str(name_file)]

    return run_wb(args, settings, outputs=[out_path]).command_line


def _read_metric(path: Path) -> np.ndarray:
    import nibabel as nib

    img = nib.load(str(path))
    return np.column_stack([np.asarray(d.data, dtype=np.float64) for d in img.darrays])


def _has_nan(path: Path) -> bool:
    try:
        return bool(np.isnan(_read_metric(path)).any())
    except Exception:  # pragma: no cover - unreadable metric
        return False


def _fold_nan_into_roi(
    metric: Path, roi: Optional[Path], out_path: Path
) -> Optional[Path]:
    """Shrink the ROI to the vertices that carry a finite value everywhere.

    A thresholded map uses NaN to mean "nothing here", not "unknown value", and
    interpolating it spreads the hole.  Folding those vertices into the ROI
    tells Workbench to leave them out of the weighting entirely, so the target
    vertices near them are built from the finite data instead.
    """
    import nibabel as nib

    values = _read_metric(metric)
    finite = np.isfinite(values).all(axis=1)
    if finite.all():
        return roi
    if roi is not None:
        existing = _read_metric(roi).ravel() > 0.5
        finite &= existing
    darray = nib.gifti.GiftiDataArray(
        finite.astype(np.float32),
        intent=nib.nifti1.intent_codes.code["NIFTI_INTENT_SHAPE"],
        datatype=nib.nifti1.data_type_codes.code[np.float32],
    )
    nib.save(nib.gifti.GiftiImage(darrays=[darray]), str(out_path))
    log.info(
        "%s: %d of %d vertices are NaN and were marked as absent data",
        metric.name, int((~finite).sum()), finite.size,
    )
    return out_path


def _looks_discrete(metric: Path, *, max_levels: int = 64) -> bool:
    """Does this metric hold label numbers rather than measurements?

    Whole numbers and few of them.  The cap matters: a parcellation has tens of
    levels, a thresholded statistic map has thousands, and a map that happens
    to be integer-valued because it counts something is still counting -- so
    the caller can always say ``discrete=False``.
    """
    try:
        values = _read_metric(metric)
    except Exception:  # pragma: no cover - unreadable metric
        return False
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return False
    if not np.array_equal(finite, np.round(finite)):
        return False
    levels = np.unique(finite)
    # A constant hemisphere -- an all-zero right side under a left-lateralised
    # mask -- is trivially discrete and must not veto the whole file.
    return levels.size <= max_levels


def _resample_discrete(
    metric_in: Path,
    metric_out: Path,
    step: ResampleStep,
    hemi: str,
    library: MeshLibrary,
    settings,
    *,
    method: str,
    current_roi: Optional[Path] = None,
    work: Optional[Path] = None,
) -> list[str]:
    """Resample a label map by area, giving each vertex its largest label.

    ``wb_command`` has ``-largest`` for a real dlabel, but a parcellation
    handed over as a plain dscalar cannot use it.  The same answer comes out of
    resampling each label's 0/1 indicator with the ordinary weights: the
    resampled indicator *is* the fraction of the target vertex's catchment that
    the label covers, so the largest of them is the label ``-largest`` would
    have chosen.  Background wins when no label covers half.
    """
    import nibabel as nib

    work = Path(work or metric_out.parent / (metric_out.stem + "_labels"))
    work.mkdir(parents=True, exist_ok=True)

    values = _read_metric(metric_in)
    columns: list[np.ndarray] = []
    commands: list[str] = []

    for column_index in range(values.shape[1]):
        column = values[:, column_index]
        levels = np.unique(column[np.isfinite(column)])
        levels = levels[levels != 0]

        winner: Optional[np.ndarray] = None
        best = None
        occupied = None
        for level in levels:
            indicator = work / f"c{column_index}_l{int(level)}.shape.gii"
            _write_metric(indicator, (column == level).astype(np.float32))
            fraction_path = work / f"c{column_index}_l{int(level)}.frac.shape.gii"
            commands += resample_metric(
                indicator, fraction_path, step, hemi, library, settings,
                method=method, current_roi=current_roi,
            )
            fraction = np.nan_to_num(_read_metric(fraction_path).ravel(), nan=0.0)
            if winner is None:
                winner = np.full(fraction.size, float(level))
                best = fraction
                occupied = fraction.copy()
            else:
                take = fraction > best
                winner[take] = float(level)
                best = np.maximum(best, fraction)
                occupied += fraction

        if winner is None:                      # nothing but background
            n_target = library.files_for(step.target).mesh.n_vertices
            columns.append(np.zeros(n_target, dtype=np.float32))
            continue

        # Background is whatever weight the labels did not claim.
        background = 1.0 - occupied
        winner[best <= background] = 0.0
        columns.append(winner.astype(np.float32))

    _write_metric(metric_out, np.column_stack(columns))
    log.info(
        "%s: resampled %d label level(s) by largest area",
        metric_out.name, int(np.unique(np.concatenate(columns)).size) - 1,
    )
    return commands


def _write_metric(path: Path, values: np.ndarray) -> None:
    """Write one or more columns as a ``func.gii``-style metric."""
    import nibabel as nib

    values = np.asarray(values, dtype=np.float32)
    if values.ndim == 1:
        values = values[:, None]
    darrays = [
        nib.gifti.GiftiDataArray(
            np.ascontiguousarray(values[:, i], dtype=np.float32),
            intent=nib.nifti1.intent_codes.code["NIFTI_INTENT_SHAPE"],
            datatype=nib.nifti1.data_type_codes.code[np.float32],
        )
        for i in range(values.shape[1])
    ]
    nib.save(nib.gifti.GiftiImage(darrays=darrays), str(path))


def _coverage_mask(
    roi_in: Path,
    step: ResampleStep,
    hemi: str,
    library: MeshLibrary,
    settings,
    method: str,
    out_path: Path,
    *,
    threshold: float = 0.5,
) -> tuple[Path, list[str]]:
    """Which target vertices are mostly built from real data?

    The source ROI is a metric of ones and zeros, so resampling it with the
    same weights as the data gives, at every target vertex, the fraction of
    its catchment that was valid.  Thresholding that fraction is what turns a
    source mask into a target mask without either dilating or eroding it.
    """
    import nibabel as nib

    fraction = out_path.with_suffix(".frac.shape.gii")
    commands = resample_metric(
        roi_in, fraction, step, hemi, library, settings, method=method,
    )
    values = _read_metric(fraction).ravel()
    keep = np.nan_to_num(values, nan=0.0) >= float(threshold)
    darray = nib.gifti.GiftiDataArray(
        keep.astype(np.float32),
        intent=nib.nifti1.intent_codes.code["NIFTI_INTENT_SHAPE"],
        datatype=nib.nifti1.data_type_codes.code[np.float32],
    )
    nib.save(nib.gifti.GiftiImage(darrays=[darray]), str(out_path))
    return out_path, commands


def _write_nan_outside(metric: Path, valid_roi: Path) -> None:
    """Set every vertex outside *valid_roi* to NaN, in place."""
    import nibabel as nib

    valid = _read_metric(valid_roi).ravel() > 0.5
    if valid.all():
        return
    img = nib.load(str(metric))
    for darray in img.darrays:
        values = np.asarray(darray.data, dtype=np.float32).copy()
        values[~valid] = np.nan
        darray.data = values
    nib.save(img, str(metric))


def _resample_via_neuromaps(
    in_path: Path, out_path: Path, target, settings, *, source=None, method=DEFAULT_METHOD
) -> ResampleResult:
    """The neuromaps backend, for people who would rather use its atlases.

    Kept deliberately thin: neuromaps' surface transforms call the same
    ``wb_command -metric-resample`` underneath, so this exists to fit an
    existing neuromaps workflow, not because it computes anything differently.
    """
    try:
        from neuromaps import transforms  # noqa: F401
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise MeshError(
            "backend='neuromaps' needs the neuromaps package "
            "(pip install neuromaps), and it downloads its template files on "
            "first use. The default backend='wb' uses the templates you "
            "already have and needs no download."
        ) from exc
    raise MeshError(  # pragma: no cover - deliberately not implemented yet
        "the neuromaps backend is not wired up: neuromaps works in GIFTI pairs "
        "and its own atlas naming, and mapping this package's CIFTI layouts on "
        "to that adds a translation layer with nothing to gain, since both end "
        "up in the same wb_command call. Use backend='wb'."
    )
