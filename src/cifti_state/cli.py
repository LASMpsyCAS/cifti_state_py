"""Command line interface.

    cifti-state check                      # environment / configuration report
    cifti-state atlases                    # what atlases are available
    cifti-state run MAP.dscalar.nii ...    # one analysis
    cifti-state run --spec spec.yaml       # the same, from a file
    cifti-state batch 'maps/*.dscalar.nii' # many maps, one parameter set

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
    if args.command == "batch":
        return _cmd_batch(args, settings)
    return 1


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
