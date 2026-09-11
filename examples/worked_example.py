#!/usr/bin/env python
"""A complete worked example, exercising every part of the package.

Run it from anywhere once your machine profile is set up:

    python examples/worked_example.py --out results/worked_example

It will:

1. resolve the machine profile and report the environment
2. load a 91k greyordinate map, and prove that the 59k and 64k layouts of the
   same data expand to identical full-mesh arrays
3. threshold (fixed, FDR and percentile) and compare
4. cluster in legacy mode, and again allowing negative clusters
5. measure peaks, annotate against two atlases, build all three report styles
6. render figures with surfplot
7. write everything out and, if Workbench is installed, smooth a copy with
   wb_command and open the result in wb_view (only with --wb-view)

Every step prints what it did, so the output doubles as a smoke test.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from cifti_state import AnalysisSpec, configure_logging, load_settings, run_analysis
from cifti_state.config import current_machine, current_platform
from cifti_state.core import (
    annotate_clusters,
    build_report,
    cluster_peaks,
    compute_threshold,
    find_clusters,
    save_report,
)
from cifti_state.io import (
    load_atlas,
    load_hemisphere_surfaces,
    load_surface_stat_map,
    check_agreement,
)
from cifti_state.pipeline import build_adjacency

BANNER = "=" * 72


def head(title: str) -> None:
    print(f"\n{BANNER}\n {title}\n{BANNER}")


def _repo_root() -> Path:
    """The checkout this script lives in."""
    return Path(__file__).resolve().parent.parent


def _resolve_config(explicit):
    """Fall back to the bundled example configuration on a fresh clone.

    Without this, running the script straight after ``git clone`` fails at the
    configuration check -- there is no machine profile yet -- which is a poor
    first impression of a tool whose own example data is sitting right there.
    """
    if explicit is not None:
        return explicit
    import os

    if os.environ.get("CIFTI_STATE_CONFIG"):
        return None
    from cifti_state.config import current_machine

    machine = _repo_root() / "configs" / "machines" / f"{current_machine()}.yaml"
    if machine.exists():
        return None
    bundled = _repo_root() / "example_data" / "example.yaml"
    if bundled.exists():
        print(f"(no machine profile yet -- using {bundled})")
        return bundled
    return None


def _find_example_data(settings):
    """Where the example maps are: bundled first, then beside the templates."""
    candidates = [_repo_root() / "example_data" / "maps"]
    if settings.resources.root is not None:
        candidates.append(settings.resources.root.parent / "Example_test")
    for candidate in candidates:
        if (candidate / "group_mean_thresh_fdr_E_D.dscalar.nii").exists():
            return candidate
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="folder holding group_mean_thresh_fdr_E_*.dscalar.nii "
             "(default: the Example_test folder next to the config's resources)",
    )
    parser.add_argument("--out", type=Path, default=Path("results/worked_example"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument(
        "--wb-view", action="store_true", help="open the result in wb_view at the end"
    )
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    settings = load_settings(_resolve_config(args.config))

    # ---------------------------------------------------------------- 1 ---- #
    head("1. environment")
    print(f"machine       : {current_machine()}  ({current_platform()})")
    print(f"profile       : {settings.machine.label()}")
    print(f"config        : {settings.source_path}")
    for parent in settings.inherited_from:
        print(f"  extends     : {parent}")
    problems = settings.validate()
    print(f"config status : {'OK' if not problems else 'PROBLEMS'}")
    for problem in problems:
        print(f"  - {problem}")
    if problems:
        print("\nFix the profile first: cifti-state check")
        return 2

    from cifti_state.wb import probe

    info = probe(settings)
    print(f"wb_command    : {info['wb_command'] or 'not installed (not required)'}")

    data_dir = args.data_dir or _find_example_data(settings)
    if data_dir is None or not data_dir.exists():
        print("\nCould not find the example data. Pass --data-dir.")
        return 2
    stat_path = data_dir / "group_mean_thresh_fdr_E_D.dscalar.nii"
    if not stat_path.exists():
        print(f"\n{stat_path} not found. Pass --data-dir.")
        return 2
    print(f"data          : {stat_path}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 2 ---- #
    head("2. reading the map (91k / 59k / 64k all describe the same cortex)")
    stat_map = load_surface_stat_map(stat_path, statistic="z")
    described = stat_map.describe()
    print(f"layout        : {described['layout']}")
    print(f"left          : {described['n_present_left']}/{described['n_vertices_left']}"
          f" vertices stored")
    print(f"right         : {described['n_present_right']}/{described['n_vertices_right']}"
          f" vertices stored")
    print(f"medial wall   : {'excluded by the file' if described['medial_wall_excluded'] else 'included'}")
    print(f"values        : {described['n_finite']} finite, "
          f"range {described['min']:.4g} .. {described['max']:.4g}")
    print(f"full mesh     : {stat_map.concatenated().size} values after expansion")

    # round trip through a 64k dense layout
    from cifti_state.io.cifti import make_dense_surface_template, save_like

    dense = make_dense_surface_template(*stat_map.n_vertices)
    dense_path = save_like(
        out_dir / "as_64k_dense.dscalar.nii",
        stat_map.left.values, stat_map.right.values, dense, map_name="dense",
    )
    back = load_surface_stat_map(dense_path)
    same = np.allclose(
        np.nan_to_num(stat_map.concatenated()),
        np.nan_to_num(back.concatenated()),
        atol=1e-6,
    )
    print(f"re-read as 64k: {back.describe()['layout']}  identical={same}")

    # ---------------------------------------------------------------- 3 ---- #
    head("3. adjacency (txt tables vs surface topology)")
    # The MATLAB tables are preferred when the configuration has them; the
    # bundled example does not ship them, so it builds the graph from the mesh
    # instead -- which step 3b then shows is the identical graph anyway.
    source = "txt"
    try:
        settings.resources.neighbor_path(settings.defaults.mesh, "left")
    except Exception:
        source = "surface"
        print("neighbour txt : not configured -- deriving from the mesh instead")
    adjacency = build_adjacency(
        stat_map, settings, AnalysisSpec(mesh=settings.defaults.mesh,
                                        neighbor_source=source)
    )
    degrees = np.diff(adjacency["left"].indptr)
    print(f"left graph    : {adjacency['left'].shape[0]} vertices, "
          f"{adjacency['left'].nnz // 2} edges")
    print(f"degree        : {dict(zip(*np.unique(degrees, return_counts=True)))}")
    try:
        txt = settings.resources.neighbor_path(settings.defaults.mesh, "left")
        surf = settings.resources.surface_path("left", "midthickness")
        identical, n_bad = check_agreement(txt, surf)
        print(f"txt == mesh   : {identical} ({n_bad} vertices differ)")
    except Exception as exc:
        print(f"txt == mesh   : could not check ({exc})")

    # ---------------------------------------------------------------- 4 ---- #
    head("4. thresholds")
    values = stat_map.finite_values()
    for label, kwargs in (
        ("fixed 1.09", dict(method="fixed", value=1.09)),
        ("FDR q=.05", dict(method="fdr", q=0.05)),
        ("95th pct", dict(method="percentile", percentile=95.0)),
    ):
        thr = compute_threshold(values, direction="positive", statistic="z", **kwargs)
        shown = f"{thr.positive:.4f}" if thr.positive is not None else "none survive"
        print(f"{label:<12}: {shown:<14} {thr.n_suprathreshold} vertices")
    threshold = compute_threshold(values, method="fixed", value=1.09)

    # ---------------------------------------------------------------- 5 ---- #
    head("5. clustering")
    legacy = find_clusters(stat_map, adjacency, threshold, extent=20, legacy_mode=True)
    print(f"legacy (positive only) : {legacy.n_clusters} clusters "
          f"(L {legacy.n_left} / R {legacy.n_right})")
    sizes = sorted((c.size_vertices for c in legacy.clusters), reverse=True)
    print(f"largest five sizes     : {sizes[:5]}")

    two_sided = compute_threshold(
        values, method="fixed", value=1.09, direction="two_sided"
    )
    both = find_clusters(
        stat_map, adjacency, two_sided, extent=20, direction="two_sided"
    )
    n_neg = sum(1 for c in both.clusters if c.sign < 0)
    print(f"two-sided              : {both.n_clusters} clusters, {n_neg} negative")
    print("  (this map is already one-sided, so a negative count of 0 is expected)")

    # ---------------------------------------------------------------- 6 ---- #
    head("6. peaks and anatomical annotation")
    surfaces = load_hemisphere_surfaces(settings, "midthickness")
    peaks = cluster_peaks(stat_map, legacy, surfaces=surfaces)
    print(f"peak table    : {len(peaks)} rows, columns = {list(peaks.columns)[:8]} ...")
    print(peaks[["cluster_id", "hemisphere", "size_vertices", "size_mm2",
                 "peak_value", "peak_x", "peak_y", "peak_z"]].head(3).to_string(index=False))

    reports = {}
    for atlas_name in ("Glasser_2016", "Desikan"):
        try:
            atlas = load_atlas(atlas_name, settings)
        except FileNotFoundError:
            print(f"\n{atlas_name}: not present, skipping")
            continue
        annotations = annotate_clusters(legacy, atlas, peaks=peaks, top_n=2)
        report = build_report(peaks, annotations, style="wide")
        reports[atlas_name] = report
        print(f"\n{atlas_name} ({atlas.n_regions()} regions)")
        print(report[["cluster_id", "hemi", "size_vertices",
                      "peak_region", "regions"]].head(3).to_string(index=False))

    # ---------------------------------------------------------------- 7 ---- #
    head("7. report formats")
    primary = next(iter(reports.values())) if reports else None
    if primary is not None:
        for fmt in ("csv", "tsv", "xlsx", "md", "json"):
            try:
                path = save_report(primary, out_dir / f"report.{fmt}", fmt=fmt)
                print(f"  {fmt:<5} {path.stat().st_size:>8} bytes  {path.name}")
            except Exception as exc:
                print(f"  {fmt:<5} skipped: {exc}")
        for style in ("wide", "long", "legacy"):
            atlas = load_atlas("Glasser_2016", settings)
            annotations = annotate_clusters(legacy, atlas, peaks=peaks, top_n=2)
            table = build_report(peaks, annotations, style=style)
            print(f"  style {style:<7} {len(table):>4} rows, "
                  f"{len(table.columns)} columns")

    # ---------------------------------------------------------------- 8 ---- #
    if not args.no_render:
        head("8. figures (surfplot)")
        from cifti_state.viz.render import (
            check_backend,
            render_clusters,
            render_stat_map,
            save_figure,
        )

        backend = check_backend()
        if not backend.get("offscreen"):
            print(f"rendering unavailable: {backend}")
            if current_platform() != "windows":
                print("try: xvfb-run -a python examples/worked_example.py")
        else:
            figure = render_stat_map(
                stat_map, settings, clusters=legacy, mask_to_clusters=True,
                layout="grid_4", title="E_D  z > 1.09, k >= 20", colorbar_label="z",
            )
            print(f"  {save_figure(figure, out_dir / 'stat_map.png', settings=settings)}")
            figure = render_clusters(
                legacy, settings, layout="row_4",
                title=f"{legacy.n_clusters} clusters",
            )
            print(f"  {save_figure(figure, out_dir / 'clusters.png', settings=settings)}")

    # ---------------------------------------------------------------- 9 ---- #
    head("9. the same thing through the pipeline")
    spec = AnalysisSpec(
        input_path=stat_path,
        threshold_method="fixed", threshold_value=1.09,
        direction="positive", extent=20, legacy_mode=True,
        atlas="Glasser_2016", top_n_regions=2,
        neighbor_source=source,
        output_dir=out_dir / "pipeline",
        report_formats=("csv", "xlsx"),
        render=not args.no_render,
    )
    result = run_analysis(spec, settings)
    print(result.summary())
    for key, path in result.outputs.items():
        print(f"  {key:<16} {path.name}")
    for message in result.warnings:
        print(f"  warning: {message}")

    # --------------------------------------------------------------- 10 ---- #
    head("10. Workbench")
    if not info["available"]:
        print("wb_command not installed — skipping. Everything above ran without it.")
    else:
        from cifti_state.wb import open_in_wb_view, smooth_cifti

        smoothed = out_dir / "smoothed.dscalar.nii"
        wb_result = smooth_cifti(
            stat_path, smoothed, settings, surface_kernel=2.0, volume_kernel=2.0
        )
        print(f"command  : {wb_result.command_line}")
        print(f"exit     : {wb_result.returncode} in {wb_result.duration:.1f}s")
        print(f"output   : {smoothed} ({smoothed.stat().st_size} bytes)")
        if args.wb_view:
            files = [result.outputs.get("cluster_map"), stat_path]
            files += [settings.resources.surface_path(h, "inflated")
                      for h in ("left", "right")]
            open_in_wb_view([f for f in files if f], settings)
            print("wb_view launched")
        else:
            print("(pass --wb-view to open the result in wb_view)")

    head("done")
    print(f"everything written to {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
