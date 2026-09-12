"""Command line interface.

    cifti-state check                      # environment / configuration report
    cifti-state atlases                    # what atlases are available
    cifti-state run MAP.dscalar.nii ...    # one analysis
    cifti-state run --spec spec.yaml       # the same, from a file
    cifti-state batch 'maps/*.dscalar.nii' # many maps, one parameter set
    cifti-state stats participants.csv ... # a group t-test, corrected
    cifti-state meshes                     # what surface meshes are available
    cifti-state resample IN --to fsLR:10k  # move a map to another mesh

Everything goes through :func:`cifti_state.pipeline.run_analysis`, the same
entry point a GUI uses.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Optional, Sequence

import yaml

from .config import (
    ConfigError,
    current_machine,
    current_platform,
    init_machine_config,
    load_settings,
    machine_config_path,
    resolution_chain,
    user_config_path,
)
from .logging_setup import configure_logging, get_logger
from .pipeline import run_analysis
from .results import AnalysisSpec

log = get_logger(__name__)

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cifti-state",
        description="fsLR surface cluster analysis, reporting and visualisation",
    )
    parser.add_argument("--config", type=Path, help="configuration YAML to use")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument("--log-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    # -- check -------------------------------------------------------------- #
    check = sub.add_parser("check", help="report configuration and environment")
    check.add_argument("--workbench", action="store_true",
                       help="also require wb_command to be present")
    check.add_argument("--render", action="store_true",
                       help="also test the surfplot / VTK / PyVista backends")
    check.add_argument("--fonts", action="store_true",
                       help="show which fonts and encodings were resolved")
    check.add_argument("--sample", type=Path, metavar="PNG",
                       help="render a font specimen PNG: the same numbers in "
                            "every candidate family, plus the real spin boxes")

    # -- atlases ------------------------------------------------------------ #
    atlases = sub.add_parser("atlases", help="list available atlases")
    atlases.add_argument("--all", action="store_true",
                         help="include atlases not in the registry")

    # -- meshes -------------------------------------------------------------- #
    meshes = sub.add_parser(
        "meshes",
        help="which surface meshes are known, and which have their templates",
    )
    meshes.add_argument("--routes", action="store_true",
                        help="also show which conversions are possible")

    # -- resample ------------------------------------------------------------ #
    resample = sub.add_parser(
        "resample",
        help="move a CIFTI file onto another surface mesh",
    )
    resample.add_argument("input", type=Path, help="dscalar/dtseries/dlabel file")
    resample.add_argument("--to", dest="target", required=True,
                          help="target mesh: fsLR:10k, fsaverage5, 32k, ...")
    resample.add_argument("--from", dest="source",
                          help="source mesh; read from the file unless the "
                               "vertex count is ambiguous (10242 is both "
                               "fsLR:10k and fsaverage5)")
    resample.add_argument("-o", "--output", type=Path,
                          help="output file (default: alongside the input)")
    resample.add_argument("--via", action="append", default=[],
                          help="force an intermediate mesh; may be repeated")
    resample.add_argument("--method", default="ADAP_BARY_AREA",
                          choices=["ADAP_BARY_AREA", "BARYCENTRIC"])
    resample.add_argument("--nan", default="propagate",
                          choices=["propagate", "mask"],
                          help="'mask' treats NaN as absent data instead of "
                               "letting it spread; use it for thresholded maps")
    resample.add_argument("--coverage", type=float, default=0.5,
                          help="fraction of a target vertex that must be real "
                               "data for it to keep a value (default 0.5)")
    resample.add_argument("--continuous", action="store_true",
                          help="treat integer-valued data as measurements; by "
                               "default a map of whole numbers with few levels "
                               "is resampled as a parcellation (largest label "
                               "wins) rather than averaged")
    resample.add_argument("--discrete", action="store_true",
                          help="force the parcellation path even when the "
                               "values do not look like labels")
    resample.add_argument("--medial-wall", default="auto",
                          choices=["auto", "exclude", "include"])
    resample.add_argument("--no-roi", action="store_true",
                          help="do not pass the medial wall as -current-roi; "
                               "reproduces a pipeline that skipped this step")
    resample.add_argument("--backend", default="wb", choices=["wb", "neuromaps"])
    resample.add_argument("--print-commands", action="store_true",
                          help="print the wb_command lines that were run")

    # -- config ------------------------------------------------------------- #
    cfg = sub.add_parser("config", help="inspect or create configuration files")
    cfg.add_argument("--show-chain", action="store_true",
                     help="show which config files are considered, and which wins")
    cfg.add_argument("--init-machine", action="store_true",
                     help="scaffold configs/machines/<hostname>.yaml for this machine")
    cfg.add_argument("--machine", help="machine name to use instead of the hostname")
    cfg.add_argument("--description", default="",
                     help="description to put in the scaffolded machine file")
    cfg.add_argument("--overwrite", action="store_true",
                     help="replace an existing machine file")
    cfg.add_argument("--write-user", action="store_true",
                     help=f"write the resolved settings to {user_config_path()}")

    # -- run ---------------------------------------------------------------- #
    run = sub.add_parser("run", help="analyse one statistic map")
    run.add_argument("input", nargs="?", type=Path,
                     help="dscalar/dtseries/dlabel file (91k, 59k or 64k)")
    run.add_argument("--spec", type=Path, help="analysis spec YAML")
    run.add_argument("--left", type=Path, help="left GIFTI metric (instead of CIFTI)")
    run.add_argument("--right", type=Path, help="right GIFTI metric")
    _add_analysis_args(run)

    # -- stats -------------------------------------------------------------- #
    stats = sub.add_parser(
        "stats",
        help="group t-test across subjects, with FWE cluster correction",
    )
    stats.add_argument("participants", type=Path,
                       help="CSV/TSV with one row per scan and a column of file paths")
    stats.add_argument("--test", required=True,
                       choices=["one-sample", "two-sample", "paired"])
    g = stats.add_argument_group("model")
    g.add_argument("--group", dest="group_column",
                   help="column holding the group labels (two-sample)")
    g.add_argument("--groups", nargs=2, metavar=("A", "B"),
                   help="which two labels to compare; the contrast is B - A")
    g.add_argument("--condition", dest="condition_column",
                   help="column holding the two conditions (paired)")
    g.add_argument("--subject", dest="subject_column",
                   help="column pairing the rows together (paired)")
    g.add_argument("--covariate", dest="covariates", action="append", default=[],
                   help="nuisance column; may be given more than once")
    g.add_argument("--file-column", help="column holding the paths (guessed by default)")
    g.add_argument("--variance", choices=["pooled", "welch"], default="pooled")

    g = stats.add_argument_group("correction")
    g.add_argument("--cluster-forming", type=float, default=3.1,
                   help="the height clusters are formed at (default 3.1)")
    g.add_argument("--permutations", type=int, default=0,
                   help="permutations for the second correction (0 = skip)")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--no-rft", action="store_true",
                   help="skip the smoothness estimate and the RFT correction")
    g.add_argument("--surface", default="midthickness",
                   help="geometry used for smoothness and adjacency")

    g = stats.add_argument_group("threshold-free cluster enhancement")
    g.add_argument("--tfce", action="store_true",
                   help="also score TFCE; needs --permutations to become a test")
    g.add_argument("--tfce-2d", action="store_true",
                   help="PALM's -tfce2D preset (H=2, E=1), suggested for surfaces")
    g.add_argument("--tfce-H", dest="tfce_h", type=float, default=None,
                   help="height exponent (PALM default 2)")
    g.add_argument("--tfce-E", dest="tfce_e", type=float, default=None,
                   help="extent exponent (PALM default 0.5, -tfce2D uses 1)")
    g.add_argument("--tfce-dh", type=float, default=None,
                   help="fixed step height; the default is max/100 per map")

    _add_analysis_args(stats)

    # -- batch -------------------------------------------------------------- #
    batch = sub.add_parser("batch", help="analyse many maps with one parameter set")
    batch.add_argument("pattern", help="glob, e.g. 'maps/*.dscalar.nii'")
    _add_analysis_args(batch)

    return parser


def _add_analysis_args(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("statistic")
    g.add_argument("--statistic", choices=["z", "t", "other"])
    g.add_argument("--df", type=float, help="degrees of freedom (required for t maps)")
    g.add_argument("--column", type=int, default=0)

    g = parser.add_argument_group("threshold")
    g.add_argument("--method", choices=["fixed", "fdr", "percentile"])
    g.add_argument("--threshold", type=float, help="value for --method fixed")
    g.add_argument("--q", type=float, help="FDR q, for --method fdr")
    g.add_argument("--percentile", type=float)
    g.add_argument("--direction", choices=["positive", "negative", "two_sided"])

    g = parser.add_argument_group("clustering")
    g.add_argument("--extent", type=int, help="minimum cluster size in vertices")
    g.add_argument("--mesh")
    g.add_argument("--neighbors", dest="neighbor_source", choices=["txt", "surface"])
    g.add_argument("--inf-policy", choices=["clip", "legacy", "nan"])
    g.add_argument("--legacy", action="store_true",
                   help="reproduce the original MATLAB behaviour exactly")

    g = parser.add_argument_group("annotation")
    g.add_argument("--atlas")
    g.add_argument("--top-n", type=int, dest="top_n_regions")
    g.add_argument("--min-percent", type=float, dest="min_region_percent")

    g = parser.add_argument_group("output")
    g.add_argument("-o", "--output-dir", type=Path)
    g.add_argument("--prefix", dest="output_prefix")
    g.add_argument("--report-style", choices=["wide", "long", "legacy"])
    g.add_argument("--format", dest="report_formats", action="append",
                   choices=["csv", "tsv", "xlsx", "md", "json"],
                   help="may be given more than once")
    g.add_argument("--no-cluster-map", action="store_true")
    g.add_argument("--render", action="store_true", help="also save a surfplot figure")
    g.add_argument("--render-surface")
    g.add_argument("--render-layout")
    g.add_argument("--render-format", choices=["png", "pdf", "svg", "tiff"])


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .fonts import apply_to_matplotlib, configure_stdio

    configure_stdio()
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, log_file=args.log_file)
    apply_to_matplotlib()

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        return _cmd_check(args, settings)
    if args.command == "atlases":
        return _cmd_atlases(args, settings)
    if args.command == "config":
        return _cmd_config(args, settings)
    if args.command == "run":
        return _cmd_run(args, settings)
    if args.command == "stats":
        return _cmd_stats(args, settings)
    if args.command == "batch":
        return _cmd_batch(args, settings)
    if args.command == "meshes":
        return _cmd_meshes(args, settings)
    if args.command == "resample":
        return _cmd_resample(args, settings)
    return 1


def _cmd_meshes(args, settings) -> int:
    """What meshes exist, which have templates here, and what can convert."""
    from .mesh import MeshLibrary, list_meshes, plan_route
    from .mesh.spaces import MeshError

    library = MeshLibrary.from_settings(settings)
    print("=== known meshes ============================================")
    print(f"{'mesh':<12} {'vertices/hemi':>13}  family      note")
    for space in list_meshes():
        print(
            f"{space.name:<12} {space.n_vertices:>13}  {space.family:<10}  "
            f"{space.note}"
        )
    print()
    print(library.report())

    available = library.available()
    if not available:
        print(
            "\nNo mesh has both a sphere and an area metric here, so nothing "
            "can be resampled. Add the directories holding your template packs "
            "to resources.mesh_dirs."
        )
        return 0
    if args.routes:
        print("\n=== conversions =============================================")
        for source in available:
            for target in available:
                if source == target:
                    continue
                try:
                    steps = plan_route(source, target, library)
                except MeshError:
                    continue
                route = " -> ".join([source.name] + [s.target.name for s in steps])
                note = "" if len(steps) == 1 else "   (two hops)"
                print(f"  {route}{note}")
    return 0


def _cmd_resample(args, settings) -> int:
    """Move one CIFTI file onto another mesh."""
    from .mesh import MeshLibrary, parse_mesh, resample_cifti
    from .mesh.spaces import MeshError

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 2

    try:
        target = parse_mesh(args.target)
    except MeshError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = args.output
    if output is None:
        stem = args.input.name
        for suffix in (".dscalar.nii", ".dtseries.nii", ".dlabel.nii", ".nii"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        else:
            suffix = args.input.suffix
        tag = target.name.replace(":", "-")
        output = args.input.parent / f"{stem}_{tag}{suffix}"

    try:
        result = resample_cifti(
            args.input, output, target, settings,
            source=args.source,
            library=MeshLibrary.from_settings(settings),
            method=args.method,
            use_roi=not args.no_roi,
            medial_wall=args.medial_wall,
            nan=args.nan,
            coverage=args.coverage,
            discrete=True if args.discrete else (False if args.continuous else "auto"),
            via=args.via or None,
            backend=args.backend,
            progress=_terminal_progress(),
        )
    except Exception as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        log.exception("the resampling failed")
        return 1

    print()
    print(result.describe())
    print(f"  output  {result.output}")
    for message in result.warnings:
        print(f"  warning: {message}")
    if args.print_commands:
        print("\nthe commands that were run:")
        for command in result.commands:
            print(f"  wb_command {command.split(' ', 1)[1]}"
                  if command.startswith("/") else f"  {command}")
    return 0


def _cmd_check(args, settings) -> int:
    from .wb import probe

    print("=== machine =================================================")
    print(f"hostname          : {current_machine()}")
    print(f"platform          : {current_platform()}")
    print(f"profile           : {settings.machine.label()}")
    if settings.machine.notes.strip():
        print(f"notes             : {settings.machine.notes.strip()}")
    print(f"configuration     : {settings.source_path or '(built-in defaults)'}")
    for parent in settings.inherited_from:
        print(f"    extends       : {parent}")
    if settings.source_path is None:
        print("    hint          : cifti-state config --init-machine")
    elif Path(settings.source_path) != machine_config_path():
        print(f"    note          : no machine file at {machine_config_path()};")
        print("                    'cifti-state config --init-machine' creates one")

    print("\n=== resources ===============================================")
    width = 22
    for label, path in (
        ("resources.root", settings.resources.root),
        ("atlas_dir", settings.resources.atlas_dir),
        ("atlas_csv_dir", settings.resources.atlas_csv_dir),
    ):
        print(f"{label:<{width}}: {_exists_mark(path)} {path}")
    tmp = settings.runtime.tmp_dir
    if tmp is not None:
        state = "ok" if Path(tmp).exists() else "will be created"
        print(f"{'tmp_dir':<{width}}: {state} {tmp}")
    for hemi in ("left", "right"):
        for kind in sorted(settings.required_surfaces()):
            label = f"surface {hemi[:1].upper()}/{kind}"
            try:
                path = settings.resources.surface_path(hemi, kind)
            except ConfigError as exc:
                print(f"{label:<{width}}: !  {exc}")
                continue
            print(f"{label:<{width}}: {_exists_mark(path)} {path.name}")
    mesh = settings.defaults.mesh
    if settings.defaults.neighbor_source == "txt":
        for hemi in ("left", "right"):
            label = f"neighbors {hemi}"
            try:
                path = settings.resources.neighbor_path(mesh, hemi)
            except ConfigError as exc:
                print(f"{label:<{width}}: !  {exc}")
                continue
            print(f"{label:<{width}}: {_exists_mark(path)} {path}")

    try:
        library = settings.mesh_library()
        available = library.available()
        names = ", ".join(m.name for m in available) or "none"
        print(f"{'meshes':<{width}}: {names}")
        if len(available) > 1:
            print(f"{'':<{width}}  (cifti-state meshes --routes lists the conversions)")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"{'meshes':<{width}}: !  {exc}")

    problems = settings.validate(require_workbench=args.workbench)
    advisories = settings.advisories()
    if problems:
        print("\nPROBLEMS (these will stop an analysis):")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("\nconfiguration looks complete.")
    if advisories:
        print("\nnotes (not needed for the current settings):")
        for note in advisories:
            print(f"  - {note}")

    print("\n=== workbench ===============================================")
    info = probe(settings)
    print(f"wb_command        : {info['wb_command'] or 'NOT FOUND'}")
    print(f"wb_view           : {info['wb_view'] or 'NOT FOUND'}")
    if info.get("version"):
        for line in info["version"]:
            print(f"                    {line}")
    elif not info["wb_command"]:
        exe = "wb_command.exe" if current_platform() == "windows" else "wb_command"
        print(f"                    set workbench.wb_command to the {exe} in your")
        print("                    machine profile, or put it on PATH.")
        print("                    (clustering and reporting work without it)")

    if args.render:
        from .viz.interactive import check_pyvista
        from .viz.render import check_backend

        print("\n=== rendering ===============================================")
        backend = check_backend()
        interactive = check_pyvista()
        print(f"surfplot          : {backend['surfplot'] and 'yes' or 'NOT INSTALLED'}")
        print(f"vtk               : {backend.get('vtk') or 'NOT INSTALLED'}")
        print(f"offscreen render  : {backend.get('offscreen')}")
        print(f"pyvista           : {interactive.get('pyvista') or 'NOT INSTALLED'}")
        print(f"pyvistaqt (3D tab): {interactive.get('pyvistaqt') or 'NOT INSTALLED'}")
        if not backend["surfplot"] or not interactive.get("pyvistaqt"):
            print("                    pip install -r requirements.txt")
        if backend.get("error"):
            print(f"                    {backend['error']}")
            if current_platform() != "windows":
                print("                    try: xvfb-run -a cifti-state ...")

    if args.fonts or getattr(args, "sample", None):
        # Start Qt first when a specimen was asked for, so the report describes
        # the families Qt will actually draw with rather than matplotlib's list.
        if getattr(args, "sample", None):
            _ensure_qt_application()

    if args.fonts:
        from .fonts import font_report, resolve_fonts

        resolve_fonts(force=True, settings=settings)
        print("\n=== fonts and encoding ======================================")
        for key, value in font_report():
            print(f"{key:<18}: {value}")
        print("\nDrop .ttf/.otf files in the bundled font directory to pin the")
        print("typeface regardless of what is installed on the machine.")

    if getattr(args, "sample", None):
        _write_font_sample(args.sample, settings)

    return 1 if problems else 0


def _ensure_qt_application():
    """A QApplication, or ``None`` when PySide6 is not installed."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        print(f"\nthe specimen needs PySide6 ({exc})", file=sys.stderr)
        return None
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def _write_font_sample(path, settings) -> None:
    """Render the font specimen PNG for ``check --fonts --sample``."""
    if _ensure_qt_application() is None:
        return

    from .fonts import render_specimen

    try:
        written = render_specimen(
            path,
            settings=settings,
            pixel_size=int(settings.interface.font_size_px),
        )
    except Exception as exc:
        print(f"\ncould not render the specimen: {exc}", file=sys.stderr)
        return
    print(f"\nfont specimen written to {written}")
    print("Open it and look at the number rows. If one is wrong, tell me which")
    print("family it is labelled with, or pin a good one in the machine config:")
    print("  interface:\n    ui_font: Segoe UI\n    mono_font: Consolas")


def _exists_mark(path) -> str:
    if path is None:
        return "! "
    return "ok" if Path(path).exists() else "MISSING"


def _cmd_atlases(args, settings) -> int:
    from .io.atlas import list_atlases

    try:
        found = list_atlases(settings, include_unregistered=args.all)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not found:
        print("no atlases found in", settings.resources.atlas_dir)
        return 1
    width = max(len(a["name"]) for a in found)
    for atlas in sorted(found, key=lambda a: (not a["registered"], a["name"])):
        mark = " " if atlas["registered"] else "*"
        regions = f"  ({atlas['n_regions']} regions)" if atlas["n_regions"] else ""
        print(f"{mark} {atlas['name']:<{width}}  {atlas['display_name']}{regions}")
    if args.all:
        print("\n* = present on disk but not in the registry")
    return 0


def _cmd_config(args, settings) -> int:
    if args.show_chain:
        chain = resolution_chain(args.config)
        winner_seen = False
        print(f"machine  : {current_machine()}")
        print(f"platform : {current_platform()}\n")
        for entry in chain:
            if entry["exists"] and not winner_seen:
                mark, winner_seen = "->", True
            elif entry["exists"]:
                mark = "  "
            else:
                mark = " x"
            print(f" {mark} {entry['source']:<26} {entry['path']}")
        print("\n  -> selected    x missing    (blank) present but outranked")
        if settings.inherited_from:
            print("\nextends chain:")
            for parent in settings.inherited_from:
                print(f"     {parent}")
        return 0

    if args.init_machine:
        try:
            path = init_machine_config(
                args.machine,
                description=args.description,
                overwrite=args.overwrite,
            )
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"created {path}")
        print("\nNext: open it, fill in the paths, then run")
        print("    cifti-state check --workbench --render")
        return 0

    if args.write_user:
        path = settings.save(user_config_path())
        print(f"wrote {path}")
        return 0

    print(yaml.safe_dump(settings.to_dict(), sort_keys=False, allow_unicode=True))
    return 0


def _spec_from_args(args, settings, input_path: Optional[Path] = None) -> AnalysisSpec:
    defaults = settings.defaults
    spec = AnalysisSpec(
        input_path=input_path,
        statistic=defaults.statistic,
        df=defaults.df,
        threshold_method=defaults.threshold.method,
        threshold_value=defaults.threshold.value,
        fdr_q=defaults.threshold.q,
        percentile=defaults.threshold.percentile,
        direction=defaults.direction,
        extent=defaults.extent,
        mesh=defaults.mesh,
        neighbor_source=defaults.neighbor_source,
        atlas=defaults.atlas,
        legacy_mode=defaults.legacy_mode,
    )

    if getattr(args, "spec", None):
        with Path(args.spec).open("r", encoding="utf-8") as fh:
            spec = AnalysisSpec.from_dict(yaml.safe_load(fh) or {})
        if input_path is not None:
            spec.input_path = input_path

    mapping = {
        "statistic": "statistic", "df": "df", "column": "column",
        "method": "threshold_method", "threshold": "threshold_value",
        "q": "fdr_q", "percentile": "percentile", "direction": "direction",
        "extent": "extent", "mesh": "mesh",
        "neighbor_source": "neighbor_source", "inf_policy": "inf_policy",
        "atlas": "atlas", "top_n_regions": "top_n_regions",
        "min_region_percent": "min_region_percent",
        "output_dir": "output_dir", "output_prefix": "output_prefix",
        "report_style": "report_style",
        "render_surface": "render_surface", "render_layout": "render_layout",
        "render_format": "render_format",
    }
    for arg_name, field_name in mapping.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            setattr(spec, field_name, value)

    if getattr(args, "report_formats", None):
        spec.report_formats = tuple(args.report_formats)
    if getattr(args, "legacy", False):
        spec.legacy_mode = True
        spec.direction = "positive"
        spec.inf_policy = "legacy"
    if getattr(args, "no_cluster_map", False):
        spec.write_cluster_map = False
    if getattr(args, "render", False):
        spec.render = True
    if getattr(args, "left", None) and getattr(args, "right", None):
        spec.input_left, spec.input_right = args.left, args.right
        spec.input_path = None
    return spec


def _cmd_run(args, settings) -> int:
    spec = _spec_from_args(args, settings, input_path=args.input)
    if not spec.input_path and not (spec.input_left and spec.input_right):
        print("error: give an input file, --left/--right, or --spec", file=sys.stderr)
        return 2
    if spec.output_dir is None:
        print("error: -o/--output-dir is required", file=sys.stderr)
        return 2

    result = run_analysis(spec, settings, progress=_terminal_progress())
    print()
    print(result.summary())
    for key, path in result.outputs.items():
        print(f"  {key:<16} {path}")
    for message in result.warnings:
        print(f"  warning: {message}")
    return 0


def _cmd_stats(args, settings) -> int:
    """Fit a group t-test, then cluster and correct its statistic map."""
    from .core.cluster import find_clusters
    from .core.threshold import compute_threshold
    from .io.cifti import save_like
    from .pipeline import build_adjacency
    from .stats import (
        PALM_2D, PALM_DEFAULT, TFCESettings,
        load_participants, one_sample_t, paired_t, two_sample_t,
    )

    if args.output_dir is None:
        print("error: -o/--output-dir is required", file=sys.stderr)
        return 2
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    people = load_participants(args.participants, file_column=args.file_column)

    want_tfce = bool(args.tfce or args.tfce_2d or args.tfce_h or args.tfce_e)
    base = PALM_2D if args.tfce_2d else PALM_DEFAULT
    tfce_settings = TFCESettings(
        height=base.height if args.tfce_h is None else args.tfce_h,
        extent=base.extent if args.tfce_e is None else args.tfce_e,
        dh=0.0 if args.tfce_dh is None else args.tfce_dh,
    ) if want_tfce else None

    shared = dict(
        settings=settings,
        covariates=args.covariates,
        surface=args.surface,
        smoothness=not args.no_rft,
        permutations=args.permutations,
        cluster_forming=args.cluster_forming,
        extent=args.extent if args.extent is not None else settings.defaults.extent,
        direction=args.direction or "two_sided",
        tfce=want_tfce,
        tfce_settings=tfce_settings,
        seed=args.seed,
        column=args.column,
        progress=_terminal_progress(),
    )

    try:
        if args.test == "one-sample":
            analysis = one_sample_t(people, **shared)
        elif args.test == "two-sample":
            if not args.group_column:
                print("error: --group is required for a two-sample test", file=sys.stderr)
                return 2
            analysis = two_sample_t(
                people, groups=args.groups, variance=args.variance,
                group_column=args.group_column, **shared,
            )
        else:
            if not (args.condition_column and args.subject_column):
                print("error: --condition and --subject are required for a "
                      "paired test", file=sys.stderr)
                return 2
            analysis = paired_t(
                people, condition_column=args.condition_column,
                subject_column=args.subject_column, **shared,
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        log.exception("the group test failed")
        return 1

    print()
    print(analysis.summary())

    prefix = args.output_prefix or f"{args.test.replace('-', '_')}"
    stat_map = analysis.stat_map
    stat_path = out_dir / f"{prefix}_{stat_map.statistic}.dscalar.nii"
    save_like(stat_path, stat_map.left.values, stat_map.right.values,
              stat_map.template, map_name=stat_map.name)

    # Cluster the statistic map at the height the correction was set up for.
    extent = shared["extent"]
    threshold = compute_threshold(
        stat_map.finite_values(), method="fixed", value=args.cluster_forming,
        direction=shared["direction"], statistic=stat_map.statistic,
        df=stat_map.df,
    )
    spec = _spec_from_args(args, settings, input_path=stat_path)
    spec.statistic = stat_map.statistic
    spec.df = stat_map.df
    adjacency = build_adjacency(stat_map, settings, spec)
    clusters = find_clusters(
        stat_map, adjacency, threshold, extent=extent,
        direction=shared["direction"], legacy_mode=False,
    )
    corrected = analysis.correct_clusters(clusters, cluster_forming=args.cluster_forming)

    outputs = {"statistic_map": stat_path}
    if analysis.tfce is not None:
        tfce_map = analysis.tfce_stat_map()
        tfce_path = out_dir / f"{prefix}_tfce.dscalar.nii"
        save_like(tfce_path, tfce_map.left.values, tfce_map.right.values,
                  tfce_map.template, map_name=tfce_map.name)
        outputs["tfce_map"] = tfce_path
        if analysis.tfce_p is not None:
            p_map = analysis.tfce_pmap()
            p_path = out_dir / f"{prefix}_tfce_p.dscalar.nii"
            save_like(p_path, p_map.left.values, p_map.right.values,
                      p_map.template, map_name=p_map.name)
            outputs["tfce_pvalues"] = p_path
    if clusters.n_clusters:
        correction_path = out_dir / f"{prefix}_clusters_corrected.csv"
        corrected.to_csv(correction_path, index=False, encoding="utf-8-sig")
        outputs["corrected_clusters"] = correction_path
    record = out_dir / f"{prefix}_stats.json"
    analysis.to_json(record)
    outputs["record"] = record

    print()
    print(f"{clusters.n_clusters} clusters at {stat_map.statistic} > "
          f"{args.cluster_forming:g}, extent >= {extent}")
    if clusters.n_clusters:
        columns = [c for c in corrected.columns if c.startswith("p_") or c in
                   ("cluster_id", "hemi", "size_vertices", "size_resels",
                    "peak_t", "peak_tfce")]
        print(corrected[columns].head(15).to_string(index=False))
        if len(corrected) > 15:
            print(f"... and {len(corrected) - 15} more")
    for key, path in outputs.items():
        print(f"  {key:<20} {path}")
    for message in analysis.warnings:
        print(f"  warning: {message}")
    return 0


def _cmd_batch(args, settings) -> int:
    paths = sorted(Path(p) for p in glob.glob(args.pattern))
    if not paths:
        print(f"no files match {args.pattern!r}", file=sys.stderr)
        return 1
    if args.output_dir is None:
        print("error: -o/--output-dir is required", file=sys.stderr)
        return 2

    failures = 0
    for index, path in enumerate(paths, start=1):
        print(f"\n[{index}/{len(paths)}] {path.name}")
        spec = _spec_from_args(args, settings, input_path=path)
        spec.output_dir = Path(args.output_dir) / path.name.split(".")[0]
        try:
            result = run_analysis(spec, settings)
            print(f"  {result.summary()}")
        except Exception as exc:
            failures += 1
            log.exception("failed on %s", path)
            print(f"  failed: {exc}", file=sys.stderr)
    print(f"\n{len(paths) - failures}/{len(paths)} succeeded")
    return 1 if failures else 0


def _terminal_progress():
    """A single self-overwriting status line, only when stderr is a terminal."""
    if not sys.stderr.isatty():
        return None

    state = {"width": 0, "percent": -1}

    def progress(fraction: float, message: str) -> None:
        percent = int(fraction * 100)
        if percent == state["percent"] and percent not in (0, 100):
            return
        state["percent"] = percent
        text = f"  [{percent:3d}%] {message}"
        sys.stderr.write("\r" + text + " " * max(0, state["width"] - len(text)))
        sys.stderr.flush()
        state["width"] = len(text)

    return progress


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
