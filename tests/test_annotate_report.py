"""Annotation and report assembly on a hand-built atlas."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cifti_state.core.annotate import UNLABELLED, annotate_clusters
from cifti_state.core.cluster import ClusterInfo, ClusterParams, ClusterResult
from cifti_state.core.report import build_report, save_report
from cifti_state.io.atlas import Atlas, AtlasHemisphere


@pytest.fixture
def tiny_atlas():
    """10 vertices per hemisphere; ids 0 (unknown), 1 and 2."""
    left = AtlasHemisphere(
        labels=np.array([0, 1, 1, 1, 1, 2, 2, 0, 0, 0]),
        names={0: "???", 1: "Region_A", 2: "Region_B"},
        hemisphere="left",
    )
    right = AtlasHemisphere(
        labels=np.array([3, 3, 3, 3, 3, 3, 3, 3, 3, 3]),
        names={3: "Region_C"},
        hemisphere="right",
    )
    return Atlas(name="tiny", display_name="Tiny", left=left, right=right)


@pytest.fixture
def one_cluster():
    labels_left = np.zeros(10, dtype=np.int32)
    labels_left[1:8] = 1          # 4x Region_A, 2x Region_B, 1x unknown
    return ClusterResult(
        labels_left=labels_left,
        labels_right=np.zeros(10, dtype=np.int32),
        clusters=[ClusterInfo(1, "left", 1, 7, 1)],
        params=ClusterParams(1.0, None, 3, "positive"),
    )


def test_percentages_use_the_whole_cluster_as_denominator(tiny_atlas, one_cluster):
    table = annotate_clusters(one_cluster, tiny_atlas, top_n=2)
    assert list(table["region"]) == ["Region_A", "Region_B"]
    assert table.loc[0, "percent"] == pytest.approx(100 * 4 / 7)
    assert table.loc[1, "percent"] == pytest.approx(100 * 2 / 7)
    # ... and percent_of_labelled ignores the unlabelled vertex
    assert table.loc[0, "percent_of_labelled"] == pytest.approx(100 * 4 / 6)


def test_top_n_zero_returns_every_region(tiny_atlas, one_cluster):
    assert len(annotate_clusters(one_cluster, tiny_atlas, top_n=0)) == 2


def test_single_region_cluster_is_not_padded(tiny_atlas):
    """MATLAB duplicated the row when only one region matched."""
    labels_left = np.zeros(10, dtype=np.int32)
    labels_left[1:5] = 1                       # entirely inside Region_A
    clusters = ClusterResult(
        labels_left=labels_left,
        labels_right=np.zeros(10, dtype=np.int32),
        clusters=[ClusterInfo(1, "left", 1, 4, 1)],
        params=ClusterParams(1.0, None, 3, "positive"),
    )
    table = annotate_clusters(clusters, tiny_atlas, top_n=2)
    assert len(table) == 1
    assert table.loc[0, "region"] == "Region_A"


def test_no_state_leaks_between_clusters(tiny_atlas):
    """The bug where label_index_name kept the previous cluster's second region."""
    labels_left = np.zeros(10, dtype=np.int32)
    labels_left[1:8] = 1                       # two regions
    labels_left[8:10] = 0
    labels_right = np.zeros(10, dtype=np.int32)
    labels_right[0:5] = 2                      # one region only
    clusters = ClusterResult(
        labels_left=labels_left,
        labels_right=labels_right,
        clusters=[
            ClusterInfo(1, "left", 1, 7, 1),
            ClusterInfo(2, "right", 1, 5, 0),
        ],
        params=ClusterParams(1.0, None, 3, "positive"),
    )
    table = annotate_clusters(clusters, tiny_atlas, top_n=2)
    second = table[table["cluster_id"] == 2]
    assert len(second) == 1
    assert set(second["region"]) == {"Region_C"}


def test_unlabelled_cluster_reports_medial_wall(tiny_atlas):
    labels_left = np.zeros(10, dtype=np.int32)
    labels_left[7:10] = 1                      # all unknown vertices
    clusters = ClusterResult(
        labels_left=labels_left,
        labels_right=np.zeros(10, dtype=np.int32),
        clusters=[ClusterInfo(1, "left", 1, 3, 7)],
        params=ClusterParams(1.0, None, 3, "positive"),
    )
    table = annotate_clusters(clusters, tiny_atlas)
    assert table.loc[0, "region"] == UNLABELLED


def test_min_percent_filters_small_contributions(tiny_atlas, one_cluster):
    table = annotate_clusters(one_cluster, tiny_atlas, top_n=0, min_percent=40.0)
    assert list(table["region"]) == ["Region_A"]


def test_mesh_mismatch_is_caught(one_cluster):
    wrong = Atlas(
        name="wrong",
        display_name="wrong",
        left=AtlasHemisphere(np.zeros(5, int), {0: "x"}, "left"),
        right=AtlasHemisphere(np.zeros(5, int), {0: "x"}, "right"),
    )
    with pytest.raises(ValueError, match="mesh mismatch"):
        annotate_clusters(one_cluster, wrong)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


@pytest.fixture
def peaks_frame():
    return pd.DataFrame(
        {
            "cluster_id": [1, 2],
            "hemisphere": ["left", "right"],
            "sign": [1, -1],
            "size_vertices": [7, 5],
            "size_mm2": [12.34, 9.87],
            "peak_value": [4.321, -3.210],
            "peak_vertex": [3, 2],
            "mean_value": [3.0, -2.5],
            "peak_x": [-40.4, 41.6],
            "peak_y": [10.2, -8.8],
            "peak_z": [2.1, 3.9],
        }
    )


@pytest.fixture
def annotations_frame():
    return pd.DataFrame(
        {
            "cluster_id": [1, 1, 2],
            "hemisphere": ["left", "left", "right"],
            "rank": [1, 2, 1],
            "region": ["Region_A", "Region_B", "Region_C"],
            "percent": [57.1, 28.6, 100.0],
            "peak_region": ["Region_A", "Region_A", "Region_C"],
        }
    )


def test_wide_report_has_one_row_per_cluster(peaks_frame, annotations_frame):
    table = build_report(peaks_frame, annotations_frame, style="wide")
    assert len(table) == 2
    assert table.loc[0, "regions"] == "Region_A (57.1%); Region_B (28.6%)"
    assert table.loc[0, "primary_region"] == "Region_A"
    assert list(table["hemi"]) == ["L", "R"]
    assert list(table["direction"]) == ["positive", "negative"]


def test_long_report_has_one_row_per_region(peaks_frame, annotations_frame):
    table = build_report(peaks_frame, annotations_frame, style="long")
    assert len(table) == 3
    assert list(table.columns[:5]) == [
        "cluster_id", "hemisphere", "rank", "region", "percent"
    ]


def test_legacy_style_uses_matlab_column_names(peaks_frame, annotations_frame):
    table = build_report(peaks_frame, annotations_frame, style="legacy")
    assert {"cluster_id", "zstat", "cluster_size", "peak_region", "percent", "name"} <= set(
        table.columns
    )


def test_coordinates_are_rounded_to_whole_millimetres(peaks_frame, annotations_frame):
    table = build_report(peaks_frame, annotations_frame, coordinate_decimals=0)
    assert table.loc[0, "peak_x"] == -40.0
    assert table.loc[0, "peak_value"] == 4.321


def test_save_report_round_trip(peaks_frame, annotations_frame, tmp_path):
    table = build_report(peaks_frame, annotations_frame)
    path = save_report(table, tmp_path / "report.csv")
    back = pd.read_csv(path)
    assert len(back) == len(table)
    assert "regions" in back.columns
