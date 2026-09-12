#!/usr/bin/env python
"""Moving a map between surface meshes, and checking that it survived.

    python examples/resample_example.py --config example_data/example.yaml

Runs against the bundled example data and prints, at each step, the thing you
would actually want to know: which meshes are available here, which spheres a
conversion picked, how much of a thresholded map came through, and whether a
round trip returns what went in.

The shell script this reproduces is in ``example_data/`` --
``Convert_scripts_*.sh``, the separate / resample / recombine sequence written
out by hand for one subject.  Everything below is that sequence, plus the
medial wall, the NaN handling and the route planning it leaves to you.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np

BANNER = "=" * 72


def head(number: int, title: str) -> None:
    print(f"\n{BANNER}\n {number}. {title}\n{BANNER}")


def values(path) -> np.ndarray:
    import nibabel as nib

    return np.asarray(nib.load(str(path)).get_fdata()).ravel()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--map", type=Path,
                        help="a CIFTI file to convert (default: the bundled one)")
    parser.add_argument("--out", type=Path, default=Path("results/resample"))
    args = parser.parse_args(argv)

    from cifti_state import configure_logging, load_settings
    from cifti_state.mesh import plan_route, resample_cifti

    configure_logging("WARNING")
    config = args.config
    if config is None and os.environ.get("CIFTI_STATE_CONFIG"):
        config = None                              # the environment wins
    settings = load_settings(config)

    root = Path(__file__).resolve().parent.parent
    source = args.map or (
        root / "example_data" / "maps" / "group_mean_thresh_fdr_E_C.dscalar.nii"
    )
    if not source.exists():
        print(f"{source} not found -- pass --map")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 1 -- #
    head(1, "what meshes are available here")
    library = settings.mesh_library()
    print(library.report())
    available = library.available()
    if len(available) < 2:
        print("\nOnly one mesh has its templates, so there is nothing to convert.")
        print("Add the directories holding your template packs to resources.mesh_dirs.")
        return 1

    # ------------------------------------------------------------------ 2 -- #
    head(2, "which routes exist, and how many hops each takes")
    for a in available:
        for b in available:
            if a == b:
                continue
            try:
                steps = plan_route(a, b, library)
            except Exception:
                continue
            print(f"  {a.name:<11} -> {b.name:<11} " +
                  " | ".join(s.describe() for s in steps))

    # ------------------------------------------------------------------ 3 -- #
    head(3, "NaN: what a thresholded map costs, measured")
    before = values(source)
    finite_before = np.isfinite(before).sum()
    print(f"input          : {source.name}")
    print(f"                 {before.size} greyordinates, "
          f"{finite_before} finite")

    targets = [m.name for m in available if m.name != "fsLR:32k"]
    target = targets[0]
    fractions = {}
    for mode in ("propagate", "mask"):
        out = args.out / f"{source.stem}_{target.replace(':', '-')}_{mode}.dscalar.nii"
        result = resample_cifti(source, out, target, settings, nan=mode)
        after = values(out)
        fractions[mode] = np.isfinite(after).sum() / after.size
        print(f"  nan={mode:<10} -> {after.size} greyordinates, "
              f"{np.isfinite(after).sum()} finite "
              f"({100 * fractions[mode]:.1f}% of cortex)")
    print()
    print("'propagate' is what wb_command does on its own: every NaN spreads over")
    print("its whole neighbourhood, so a thresholded blob erodes. 'mask' treats NaN")
    print("as absent data and puts the holes back, which is what the map meant.")

    # ------------------------------------------------------------------ 4 -- #
    head(4, f"the commands that produced the {target} file")
    for command in result.commands:
        print(f"  {command}")

    # ------------------------------------------------------------------ 5 -- #
    head(5, "round trip: does a smooth map come back?")
    print("Downsampling loses detail by definition, so the check is on a smooth")
    print("field, where it should not. A wrong sphere or a missing area metric")
    print("would show up here and almost nowhere else.")
    smooth = _smooth_map(settings, args.out)
    if smooth is None:
        print("  (skipped: no fs_LR 32k midthickness surface)")
        return 0
    down = args.out / "smooth_down.dscalar.nii"
    back = args.out / "smooth_back.dscalar.nii"
    resample_cifti(smooth, down, target, settings)
    resample_cifti(down, back, "fsLR:32k", settings)
    a, b = values(smooth), values(back)
    print()
    print(f"  32k -> {target} -> 32k")
    print(f"  correlation with the original : {np.corrcoef(a, b)[0, 1]:.5f}")
    print(f"  standard deviation  {a.std():.4f} -> {b.std():.4f}")

    print(f"\n{BANNER}\n Done. Files are in {args.out}\n{BANNER}")
    return 0


def _smooth_map(settings, out_dir: Path):
    """A smooth dense fs_LR 32k map, built by averaging noise over the mesh."""
    from scipy.sparse import diags

    from cifti_state.io.cifti import make_dense_surface_template, save_like
    from cifti_state.io.neighbors import adjacency_from_surface
    from cifti_state.io.surface import load_surface

    rng = np.random.default_rng(0)
    hemis = []
    for hemi in ("left", "right"):
        try:
            surface = load_surface(settings.surface_for(hemi, "midthickness"))
        except Exception:
            return None
        adjacency = adjacency_from_surface(surface.faces, surface.n_vertices)
        degree = np.asarray(adjacency.sum(1)).ravel()
        degree[degree == 0] = 1
        smoother = (diags(1.0 / degree) @ adjacency).tocsr()
        field = rng.normal(size=surface.n_vertices)
        for _ in range(60):
            field = 0.5 * field + 0.5 * (smoother @ field)
        hemis.append(field / field.std())

    path = out_dir / "smooth_32k.dscalar.nii"
    save_like(path, hemis[0], hemis[1], make_dense_surface_template(),
              map_name="smooth")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
