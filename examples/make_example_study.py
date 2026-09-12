#!/usr/bin/env python
"""Synthesise a small surface study, so the group tests can be run end to end.

Real subject data is not something this repository can ship, and a group test
needs a whole cohort.  This builds one on the bundled fs_LR 32k mesh: smooth
noise per subject, optionally with a compact blob planted in one group, written
out as ordinary ``dscalar.nii`` files with a ``participants.csv`` beside them.

    python examples/make_example_study.py --out study --effect 0.9
    python examples/make_example_study.py --out study_null --effect 0    # no signal
    python examples/make_example_study.py --out study_paired --paired --effect 0.7

Then::

    cifti-state stats study/participants.csv --test two-sample --group group \\
        --cluster-forming 3.1 --extent 20 --permutations 1000 -o results/group

The noise is smoothed by iterating a graph-average over the mesh, which gives a
field with a realistic spatial correlation without needing a smoothing kernel
in millimetres.  ``--steps`` controls how smooth: roughly 40 steps gives an
11 mm FWHM on this mesh, 60 steps about 14 mm.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import diags


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_config(explicit):
    if explicit is not None:
        return explicit
    import os

    if os.environ.get("CIFTI_STATE_CONFIG"):
        return None
    from cifti_state.config import current_machine

    if (_repo_root() / "configs" / "machines" / f"{current_machine()}.yaml").exists():
        return None
    bundled = _repo_root() / "example_data" / "example.yaml"
    return bundled if bundled.exists() else None


def smoothing_operator(adjacency):
    """Row-normalised adjacency: one step averages each vertex with its neighbours."""
    degree = np.asarray(adjacency.sum(1)).ravel()
    degree[degree == 0] = 1.0
    return (diags(1.0 / degree) @ adjacency).tocsr()


def smooth_noise(rng, operator, n_rows: int, n_vertices: int, steps: int) -> np.ndarray:
    """``n_rows`` independent smooth fields, each standardised to unit variance."""
    field = rng.normal(size=(n_rows, n_vertices))
    for _ in range(steps):
        field = 0.5 * field + 0.5 * (field @ operator.T)
    spread = field.std(axis=1, keepdims=True)
    spread[spread == 0] = 1.0
    return field / spread


def grow_patch(adjacency, seed: int, size: int, rng) -> np.ndarray:
    """A connected patch of roughly *size* vertices, grown from one seed."""
    frontier = {int(seed)}
    chosen = set(frontier)
    while len(chosen) < size:
        nxt: set[int] = set()
        for vertex in frontier:
            start, end = adjacency.indptr[vertex], adjacency.indptr[vertex + 1]
            nxt.update(adjacency.indices[start:end].tolist())
        new = nxt - chosen
        if not new:
            break
        chosen |= new
        frontier = new
    return np.fromiter(chosen, dtype=int)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="study folder to create")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--n-per-group", type=int, default=14)
    parser.add_argument("--steps", type=int, default=40,
                        help="smoothing iterations (40 ~ 11 mm FWHM)")
    parser.add_argument("--effect", type=float, default=0.9,
                        help="planted effect size in SD; 0 for a null study")
    parser.add_argument("--patch", type=int, default=600,
                        help="size of the planted blob, in vertices")
    parser.add_argument("--paired", action="store_true",
                        help="two conditions per subject instead of two groups")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    from cifti_state import configure_logging, load_settings
    from cifti_state.io.cifti import load_surface_stat_map, save_like
    from cifti_state.io.neighbors import adjacency_from_surface
    from cifti_state.io.surface import load_hemisphere_surfaces

    configure_logging("WARNING")
    settings = load_settings(_resolve_config(args.config))
    problems = settings.validate()
    if problems:
        print("Configuration problems:", *problems, sep="\n  ")
        return 1

    example = _repo_root() / "example_data" / "maps" / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not example.exists():
        print(f"need a template map to copy the layout from; {example} is missing")
        return 1
    template_map = load_surface_stat_map(example, statistic="other")
    template = template_map.template
    n_left = template_map.left.n_vertices
    n_right = template_map.right.n_vertices

    surfaces = load_hemisphere_surfaces(settings, "midthickness")
    adjacency = {
        "left": adjacency_from_surface(surfaces["left"].faces, n_left),
        "right": adjacency_from_surface(surfaces["right"].faces, n_right),
    }
    operators = {side: smoothing_operator(adj) for side, adj in adjacency.items()}

    rng = np.random.default_rng(args.seed)

    signal = np.zeros(n_left + n_right)
    if args.effect:
        patch = grow_patch(adjacency["left"], rng.integers(n_left), args.patch, rng)
        signal[patch] = args.effect
        print(f"planted a {patch.size}-vertex blob of {args.effect} SD "
              f"on the left hemisphere")

    maps_dir = args.out / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    rows = ["subject,group,condition,age,sex,file"]

    total = args.n_per_group * 2
    for index in range(total):
        group = "a" if index < args.n_per_group else "b"
        conditions = ("pre", "post") if args.paired else ("na",)
        for condition in conditions:
            carries_effect = (
                condition == "post" if args.paired else group == "b"
            )
            noise = np.concatenate([
                smooth_noise(rng, operators["left"], 1, n_left, args.steps)[0],
                smooth_noise(rng, operators["right"], 1, n_right, args.steps)[0],
            ])
            values = noise + (signal if carries_effect else 0.0)

            subject = f"sub-{index:03d}"
            stem = subject if condition == "na" else f"{subject}_{condition}"
            path = maps_dir / f"{stem}.dscalar.nii"
            save_like(path, values[:n_left], values[n_left:], template, map_name=stem)
            rows.append(
                f"{subject},{group},{condition},{20 + index % 25},"
                f"{'M' if index % 3 else 'F'},maps/{path.name}"
            )
        print(f"  {index + 1}/{total}", end="\r", flush=True)

    table = args.out / "participants.csv"
    table.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"\nwrote {len(rows) - 1} maps and {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
