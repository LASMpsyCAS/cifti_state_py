#!/usr/bin/env python
"""The three group t-tests and all three cluster corrections, start to finish.

    python examples/make_example_study.py --out study --effect 0.9
    python examples/make_example_study.py --out study_paired --paired --effect 0.7
    python examples/group_stats_example.py --study study --paired-study study_paired

Every step prints what it found, so the numbers in the README can be checked
rather than taken on trust.  The study is synthetic (see
``make_example_study.py``) and a blob of known size was planted in one group,
so you can watch the correction separate it from the noise clusters around it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

BANNER = "=" * 72


def head(number, title: str) -> None:
    print(f"\n{BANNER}\n {number}. {title}\n{BANNER}")


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


def cluster_and_correct(analysis, settings, *, cluster_forming, extent):
    """Cluster the statistic map and attach both corrections."""
    from cifti_state.core.cluster import find_clusters
    from cifti_state.core.threshold import compute_threshold
    from cifti_state.pipeline import build_adjacency
    from cifti_state.results import AnalysisSpec

    stat_map = analysis.stat_map
    threshold = compute_threshold(
        stat_map.finite_values(), method="fixed", value=cluster_forming,
        direction="two_sided", statistic=stat_map.statistic, df=stat_map.df,
    )
    adjacency = build_adjacency(stat_map, settings, AnalysisSpec(mesh="32k"))
    clusters = find_clusters(
        stat_map, adjacency, threshold, extent=extent, direction="two_sided",
    )
    return clusters, analysis.correct_clusters(clusters, cluster_forming=cluster_forming)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--study", type=Path, required=True,
                        help="folder from make_example_study.py")
    parser.add_argument("--paired-study", type=Path,
                        help="a second folder made with --paired")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--permutations", type=int, default=1000)
    parser.add_argument("--tfce", action="store_true",
                        help="also score TFCE (PALM's -tfce2D); "
                             "adds about 3.5 minutes per thousand rearrangements")
    parser.add_argument("--cluster-forming", type=float, default=3.1)
    parser.add_argument("--extent", type=int, default=20)
    args = parser.parse_args(argv)

    from cifti_state import configure_logging, load_settings
    from cifti_state.stats import (
        PALM_2D, load_participants, one_sample_t, paired_t, two_sample_t,
    )
    from cifti_state.stats.design import Participants

    configure_logging("WARNING")
    settings = load_settings(_resolve_config(args.config))

    table = args.study / "participants.csv"
    if not table.exists():
        print(f"{table} not found -- run make_example_study.py first")
        return 1

    # ------------------------------------------------------------------ 1 -- #
    head(1, "the study")
    people = load_participants(table)
    print(f"table         : {table}")
    print(f"rows          : {len(people)}")
    print(f"columns       : {list(people.frame.columns)}")
    print(people.frame.groupby("group").size().to_string())

    # ------------------------------------------------------------------ 2 -- #
    head(2, "one-sample: is the mean map different from zero?")
    group_b = people.frame[people.frame.group == "b"].reset_index(drop=True)
    one = one_sample_t(
        Participants(frame=group_b, file_column=people.file_column), settings,
        permutations=0,
    )
    print(one.summary())

    # ------------------------------------------------------------------ 3 -- #
    head(3, "two-sample, with a covariate")
    two = two_sample_t(
        people, settings, "group", covariates=["age"],
        permutations=args.permutations,
        cluster_forming=args.cluster_forming, extent=args.extent,
        tfce=args.tfce, tfce_settings=PALM_2D,
    )
    print(two.summary())
    print()
    print("the model that was actually fitted:")
    for note in two.design.notes:
        print(f"  - {note}")

    # ------------------------------------------------------------------ 4 -- #
    head(4, "smoothness, measured on the residuals")
    smooth = two.smoothness
    print(f"search region : {smooth.n_vertices} vertices, "
          f"{smooth.n_triangles} triangles")
    print(f"resels        : R0 {smooth.resels[0]:.0f} (Euler characteristic), "
          f"R1 {smooth.resels[1]:.1f}, R2 {smooth.resels[2]:.1f}")
    print(f"FWHM          : {smooth.fwhm_mm:.2f} mm "
          f"({smooth.fwhm:.2f} vertex spacings)")
    print()
    print("what that implies under random field theory:")
    print(f"  {two.random_field.describe(u=args.cluster_forming)}")

    # ------------------------------------------------------------------ 5 -- #
    head(5, "clusters, and how surprising each one is")
    clusters, corrected = cluster_and_correct(
        two, settings,
        cluster_forming=args.cluster_forming, extent=args.extent,
    )
    print(f"{clusters.n_clusters} clusters at |t| > {args.cluster_forming:g}, "
          f"extent >= {args.extent}")
    if clusters.n_clusters:
        print()
        print(corrected.to_string(index=False))
        print()
        survivors = corrected[corrected.get("p_rft_cluster", 1.0) <= 0.05]
        print(f"surviving RFT cluster correction at 0.05        : {len(survivors)}")
        if "p_perm_cluster" in corrected:
            perm = corrected[corrected["p_perm_cluster"] <= 0.05]
            print(f"surviving permutation cluster correction at 0.05 : {len(perm)}")

    # ------------------------------------------------------------------ 6 -- #
    head(6, "do the two corrections agree?")
    if clusters.n_clusters and "p_perm_cluster" in corrected:
        pair = corrected[["size_vertices", "p_rft_cluster", "p_perm_cluster"]]
        print(pair.to_string(index=False))
        both = np.corrcoef(
            np.log10(np.clip(corrected["p_rft_cluster"], 1e-6, 1)),
            np.log10(np.clip(corrected["p_perm_cluster"], 1e-6, 1)),
        )[0, 1]
        print(f"\ncorrelation of the two (log10 P): {both:.3f}")
        print("RFT is usually the slightly more liberal of the two; a large gap "
              "means\nits assumptions deserve a second look.")
        print(f"the permutation test cannot report a P below "
              f"{two.permutation.resolution():.2g} with "
              f"{two.permutation.n_permutations} rearrangements")

    # ------------------------------------------------------------------ 7 -- #
    head(7, "TFCE: the same question without a cluster-forming threshold")
    if two.tfce is None:
        print("not requested -- add --tfce to see it")
    else:
        print(f"settings      : {two.tfce_settings.describe()}")
        print(f"max score     : {two.tfce.max():.4g}")
        p_tfce = two.tfce_p
        if p_tfce is None:
            print("no permutation null, so the score is a map rather than a test")
        else:
            print(f"threshold at corrected 0.05 : "
                  f"{two.permutation.tfce_threshold(0.05):.4g}")
            print(f"vertices at corrected P<=0.05: {int((p_tfce <= 0.05).sum())}")
            print()
            print("no threshold was chosen anywhere above -- which is the point.")
            if clusters.n_clusters and "p_tfce" in corrected:
                print()
                print(corrected[["cluster_id", "size_vertices", "peak_t",
                                 "p_perm_cluster", "peak_tfce", "p_tfce"]]
                      .to_string(index=False))
                print("\nthe tallest peak is not always the strongest TFCE: "
                      "extent counts too.")

    # ------------------------------------------------------------------ 8 -- #
    head(8, "the Welch alternative, for unequal variances")
    welch = two_sample_t(
        people, settings, "group", covariates=["age"],
        variance="welch", permutations=0,
    )
    print(welch.summary())
    for message in welch.warnings:
        print(f"  note: {message}")

    # ------------------------------------------------------------------ 8 -- #
    if args.paired_study:
        head(9, "paired")
        paired_table = args.paired_study / "participants.csv"
        if not paired_table.exists():
            print(f"{paired_table} not found -- skipping")
        else:
            paired_people = load_participants(paired_table)
            paired = paired_t(
                paired_people, settings, "condition", "subject",
                conditions=("pre", "post"),
                permutations=0,
            )
            print(paired.summary())
            print(f"\ncontrast      : {paired.design.contrast_name}")
            for note in paired.design.notes:
                print(f"  - {note}")

    print(f"\n{BANNER}\n Done.\n{BANNER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
