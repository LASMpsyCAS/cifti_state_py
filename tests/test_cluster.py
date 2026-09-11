"""Clustering behaviour on synthetic data, where the answer is known by hand."""

from __future__ import annotations

import numpy as np
import pytest

from cifti_state.core.cluster import find_clusters, sanitise
from cifti_state.core.threshold import ThresholdResult


def threshold(positive=None, negative=None, direction="positive"):
    return ThresholdResult(
        positive=positive,
        negative=negative,
        method="fixed",
        direction=direction,
        statistic="z",
    )


def test_positive_only_matches_legacy_behaviour(toy_map, line_graph):
    adjacency = {"left": line_graph, "right": line_graph}
    result = find_clusters(
        toy_map, adjacency, threshold(positive=1.0), extent=3, direction="positive"
    )
    # left: vertices 0-2 above threshold (3 of them); the -3 run is ignored.
    # right: vertices 1-4 above threshold (4 of them); the lone 2.0 is too small.
    assert result.n_clusters == 2
    assert result.n_left == 1 and result.n_right == 1
    assert np.array_equal(np.flatnonzero(result.labels_left == 1), [0, 1, 2])
    assert np.array_equal(np.flatnonzero(result.labels_right == 2), [1, 2, 3, 4])


def test_negative_direction_finds_what_legacy_missed(toy_map, line_graph):
    adjacency = {"left": line_graph, "right": line_graph}
    result = find_clusters(
        toy_map, adjacency, threshold(negative=-1.0), extent=3, direction="negative"
    )
    assert result.n_clusters == 1
    info = result.clusters[0]
    assert info.hemisphere == "left" and info.sign == -1
    assert np.array_equal(np.flatnonzero(result.labels_left == 1), [5, 6, 7, 8])


def test_two_sided_orders_positive_before_negative(toy_map, line_graph):
    adjacency = {"left": line_graph, "right": line_graph}
    result = find_clusters(
        toy_map,
        adjacency,
        threshold(positive=1.0, negative=-1.0, direction="two_sided"),
        extent=3,
        direction="two_sided",
    )
    assert result.n_clusters == 3
    signs = [c.sign for c in result.clusters]
    hemis = [c.hemisphere for c in result.clusters]
    assert hemis == ["left", "left", "right"]
    assert signs == [1, -1, 1]          # positive first within a hemisphere


def test_extent_filter_does_not_consume_cluster_numbers(toy_map, line_graph):
    adjacency = {"left": line_graph, "right": line_graph}
    result = find_clusters(
        toy_map, adjacency, threshold(positive=1.0), extent=4, direction="positive"
    )
    # only the right-hemisphere run of 4 survives, and it must be cluster 1
    assert result.n_clusters == 1
    assert result.clusters[0].cluster_id == 1
    assert result.labels_right.max() == 1


def test_cluster_ids_follow_ascending_min_vertex(line_graph):
    from cifti_state.io.cifti import HemiSurfaceData, SurfaceStatMap

    def hemi(values, name):
        values = np.asarray(values, float)
        return HemiSurfaceData(
            values=values,
            present=np.ones(values.size, bool),
            vertex_index=np.arange(values.size),
            n_vertices=values.size,
            hemisphere=name,
        )

    left = [0, 0, 5, 5, 0, 0, 5, 5, 0, 0]
    stat_map = SurfaceStatMap(left=hemi(left, "left"), right=hemi([0] * 10, "right"))
    result = find_clusters(
        stat_map,
        {"left": line_graph, "right": line_graph},
        threshold(positive=1.0),
        extent=2,
    )
    assert [c.min_vertex for c in result.clusters] == [2, 6]
    assert [c.cluster_id for c in result.clusters] == [1, 2]


@pytest.mark.parametrize(
    "policy, expected_top",
    [("clip", 5.0), ("legacy", 5.0), ("nan", 5.0)],
)
def test_sanitise_infinities(policy, expected_top):
    values = np.array([1.0, np.inf, -np.inf, 5.0, np.nan])
    out = sanitise(values, inf_policy=policy)
    assert out[0] == 1.0 and out[3] == expected_top
    if policy == "clip":
        assert out[1] == 5.0 and out[2] == 1.0
    elif policy == "legacy":
        assert out[1] == 5.0 and out[2] == 5.0     # the old, asymmetric behaviour
    else:
        assert np.isnan(out[1]) and np.isnan(out[2])


def test_negative_infinity_does_not_create_a_positive_cluster(line_graph):
    """The bug 'inf_policy=clip' fixes, demonstrated."""
    from cifti_state.io.cifti import HemiSurfaceData, SurfaceStatMap

    def hemi(values, name):
        values = np.asarray(values, float)
        return HemiSurfaceData(
            values=values,
            present=np.ones(values.size, bool),
            vertex_index=np.arange(values.size),
            n_vertices=values.size,
            hemisphere=name,
        )

    left = [10.0, -np.inf, -np.inf, -np.inf, 0, 0, 0, 0, 0, 0]
    stat_map = SurfaceStatMap(left=hemi(left, "left"), right=hemi([0] * 10, "right"))
    adjacency = {"left": line_graph, "right": line_graph}

    clipped = find_clusters(
        stat_map, adjacency, threshold(positive=1.0), extent=3, inf_policy="clip"
    )
    legacy = find_clusters(
        stat_map, adjacency, threshold(positive=1.0), extent=3, inf_policy="legacy"
    )
    assert clipped.n_clusters == 0        # correct: -inf is not supra-threshold
    assert legacy.n_clusters == 1         # the old behaviour invents a cluster


def test_medial_wall_is_excluded(line_graph):
    from cifti_state.io.cifti import HemiSurfaceData, SurfaceStatMap

    values = np.full(10, 5.0)
    present = np.ones(10, bool)
    present[3:] = False                    # pretend 3..9 are medial wall
    left = HemiSurfaceData(values, present, np.arange(3), 10, "left")
    right = HemiSurfaceData(
        np.zeros(10), np.ones(10, bool), np.arange(10), 10, "right"
    )
    stat_map = SurfaceStatMap(left=left, right=right)
    result = find_clusters(
        stat_map,
        {"left": line_graph, "right": line_graph},
        threshold(positive=1.0),
        extent=2,
        restrict_to_present=True,
    )
    assert result.n_clusters == 1
    assert np.array_equal(np.flatnonzero(result.labels_left == 1), [0, 1, 2])
