"""Network figures: reading the files, choosing the edges, styling them.

The reader and selection tests are pure and always run.  The rendering tests
need PyVista and a working offscreen OpenGL context, so they skip without one;
what they check is not that a figure looks nice but that it exists, that the
brain is behind the network rather than painted over it, and that the number
of edges drawn is the number that was selected.

The edge-count rule gets a section to itself because it is the one piece of
behaviour that has to agree with a tool outside this repository.  The
hand-worked example fixes the semantics; ``test_published_edge_counts``
re-checks them against NeuroMArVL's own published ``k -> arrows`` table when
the dataset that table came from is available.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from cifti_state.network.data import (
    NetworkData,
    NetworkError,
    load_network_dir,
    read_attributes,
    read_coordinates,
    read_labels,
    read_matrix,
)
from cifti_state.network.style import (
    BrainStyle,
    EdgeStyle,
    NetworkStyle,
    NodeStyle,
    edge_colors,
    edge_radii,
    load_marvl_settings,
    node_colors,
    node_radii,
    save_marvl_settings,
)
from cifti_state.network.threshold import (
    max_positive_edge_count,
    pair_maxima,
    select_edges,
    threshold_for_edge_count,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def tiny() -> NetworkData:
    """Four nodes, an asymmetric matrix, two attribute columns."""
    matrix = np.array(
        [
            [0.0, 0.9, 0.1, -0.5],
            [0.2, 0.0, 0.8, 0.05],
            [0.3, 0.4, 0.0, 0.7],
            [0.6, -0.2, 0.15, 0.0],
        ]
    )
    return NetworkData(
        coordinates=np.array(
            [[-40.0, 10.0, 20.0], [-10.0, -30.0, 50.0],
             [30.0, 5.0, -10.0], [12.0, -60.0, 8.0]]
        ),
        matrix=matrix,
        labels=("a", "b", "c", "d"),
        attributes={
            "group_id": np.array([1.0, 1.0, 2.0, 3.0]),
            "strength": np.array([0.2, 0.8, 0.5, 1.0]),
        },
        name="tiny",
    )


@pytest.fixture
def written(tmp_path: Path, tiny: NetworkData) -> Path:
    (tmp_path / "coordinates.txt").write_text(
        "x y z\n" + "\n".join(
            " ".join(f"{v:.3f}" for v in row) for row in tiny.coordinates
        ) + "\n"
    )
    (tmp_path / "labels.txt").write_text("\n".join(tiny.labels) + "\n")
    (tmp_path / "matrix_demo.txt").write_text(
        "\n".join(" ".join(f"{v:.6f}" for v in row) for row in tiny.matrix) + "\n"
    )
    (tmp_path / "attributes_demo.txt").write_text(
        "group_id strength\n" + "\n".join(
            f"{g:.0f} {s:.3f}" for g, s in
            zip(tiny.attributes["group_id"], tiny.attributes["strength"])
        ) + "\n"
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def test_a_dataset_round_trips_through_its_files(written, tiny):
    data = load_network_dir(written)
    assert data.n_nodes == 4
    assert data.labels == tiny.labels
    np.testing.assert_allclose(data.coordinates, tiny.coordinates)
    np.testing.assert_allclose(data.matrix, tiny.matrix, atol=1e-6)
    assert data.attribute_names == ("group_id", "strength")
    assert data.name == "demo"


def test_the_header_row_is_recognised_not_required(tmp_path):
    with_header = tmp_path / "a.txt"
    without = tmp_path / "b.txt"
    with_header.write_text("x y z\n1 2 3\n4 5 6\n")
    without.write_text("1 2 3\n4 5 6\n")
    np.testing.assert_allclose(
        read_coordinates(with_header), read_coordinates(without)
    )


@pytest.mark.parametrize("sep", [" ", "\t", ",", "  ", ", "])
def test_any_of_the_usual_separators_works(tmp_path, sep):
    path = tmp_path / "c.txt"
    path.write_text(sep.join(["1", "2", "3"]) + "\n" + sep.join(["4", "5", "6"]))
    np.testing.assert_allclose(
        read_coordinates(path), [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    )


def test_a_label_may_contain_spaces(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("left IFJ\nright SFL\n")
    assert read_labels(path) == ("left IFJ", "right SFL")


def test_a_matrix_with_row_and_column_names_loses_both(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text("a b\na 0 1\nb 2 0\n")
    np.testing.assert_allclose(read_matrix(path), [[0.0, 1.0], [2.0, 0.0]])


def test_unnamed_attribute_columns_still_get_names(tmp_path):
    path = tmp_path / "attr.txt"
    path.write_text("1 0.5\n2 0.7\n")
    assert list(read_attributes(path)) == ["col1", "col2"]


def test_duplicate_attribute_headers_do_not_collide(tmp_path):
    path = tmp_path / "attr.txt"
    path.write_text("deg deg\n1 2\n3 4\n")
    assert list(read_attributes(path)) == ["deg", "deg_2"]


def test_a_matrix_that_is_not_square_is_refused(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text("0 1 2\n3 4 5\n")
    with pytest.raises(NetworkError, match="not square"):
        read_matrix(path)


def test_counts_that_disagree_are_refused_by_name(written):
    (written / "labels.txt").write_text("a\nb\n")
    with pytest.raises(NetworkError, match="4 coordinates but 2 labels"):
        load_network_dir(written)


def test_a_matrix_of_the_wrong_size_names_both_numbers(tmp_path, written):
    (written / "matrix_demo.txt").write_text("0 1\n1 0\n")
    with pytest.raises(NetworkError, match="4 coordinates but the matrix is 2"):
        load_network_dir(written)


def test_several_matrices_need_the_dataset_named(written, tiny):
    (written / "matrix_other.txt").write_text(
        (written / "matrix_demo.txt").read_text()
    )
    (written / "attributes_other.txt").write_text(
        (written / "attributes_demo.txt").read_text()
    )
    with pytest.raises(NetworkError, match="more than one matrix"):
        load_network_dir(written)
    assert load_network_dir(written, dataset="other").name == "other"


def test_abs_files_are_ignored_unless_asked_for(written):
    (written / "matrix_demo_abs.txt").write_text(
        (written / "matrix_demo.txt").read_text()
    )
    (written / "attributes_demo_abs.txt").write_text(
        (written / "attributes_demo.txt").read_text()
    )
    # Two matrices now, but one of them is the _abs variant, so the plain
    # request is still unambiguous.
    assert load_network_dir(written).name == "demo"
    assert load_network_dir(written, absolute=True).name == "demo_abs"


def test_a_directed_matrix_is_not_symmetrised(tiny):
    assert tiny.is_directed
    assert tiny.matrix[0, 1] != tiny.matrix[1, 0]
    assert tiny.asymmetry > 0.5


def test_a_symmetric_matrix_says_so(tiny):
    symmetric = (tiny.matrix + tiny.matrix.T) / 2
    data = NetworkData(coordinates=tiny.coordinates, matrix=symmetric)
    assert not data.is_directed
    assert data.asymmetry == pytest.approx(0.0)


def test_degrees_exclude_the_diagonal_and_take_magnitudes(tiny):
    np.testing.assert_allclose(
        tiny.out_degree(), np.abs(tiny.matrix).sum(axis=1)
    )
    np.testing.assert_allclose(
        tiny.in_degree(), np.abs(tiny.matrix).sum(axis=0)
    )
    # Signed degrees are different, which is the point of the flag.
    assert tiny.in_degree(absolute=False)[1] != tiny.in_degree()[1]


def test_asking_for_an_attribute_that_is_not_there_lists_the_ones_that_are(tiny):
    with pytest.raises(NetworkError, match="group_id, strength"):
        tiny.attribute("in_degree")


# --------------------------------------------------------------------------- #
# the edge-count rule
# --------------------------------------------------------------------------- #


def test_pair_maxima_take_the_larger_signed_direction():
    matrix = np.array([[0.0, 0.1, 0.0],
                       [-0.4, 0.0, 0.2],
                       [0.0, 0.05, 0.0]])
    # pairs: (0,1) -> max(0.1, -0.4) = 0.1
    #        (0,2) -> max(0.0,  0.0) = 0.0
    #        (1,2) -> max(0.2,  0.05) = 0.2
    np.testing.assert_allclose(pair_maxima(matrix), [0.2, 0.1, 0.0])


def test_the_cutoff_comes_from_pairs_but_applies_to_directions():
    # Worked by hand.  Six pairs; ranked maxima 0.9, 0.8, 0.7, 0.6, 0.4, 0.15.
    matrix = np.array(
        [
            [0.0, 0.9, 0.1, -0.5],
            [0.2, 0.0, 0.8, 0.05],
            [0.3, 0.4, 0.0, 0.7],
            [0.6, -0.2, 0.15, 0.0],
        ]
    )
    # (0,1) -> 0.9   (0,2) -> 0.3   (0,3) -> 0.6
    # (1,2) -> 0.8   (1,3) -> 0.05  (2,3) -> 0.7
    np.testing.assert_allclose(
        pair_maxima(matrix), [0.9, 0.8, 0.7, 0.6, 0.3, 0.05]
    )
    # k = 3 cuts at 0.7, and three directions reach it: 0.9, 0.8, 0.7.
    assert threshold_for_edge_count(matrix, 3) == pytest.approx(0.7)
    assert len(select_edges(matrix, edge_count=3)) == 3
    # k = 5 cuts at 0.3, and now six do -- k is not the arrow count.
    assert threshold_for_edge_count(matrix, 5) == pytest.approx(0.3)
    assert len(select_edges(matrix, edge_count=5)) == 6


def test_arrows_never_decrease_as_the_slider_opens(tiny):
    counts = [
        len(select_edges(tiny.matrix, edge_count=k))
        for k in range(1, pair_maxima(tiny.matrix).size + 1)
    ]
    assert counts == sorted(counts)


#: A matrix with one pair that is negative in both directions, so the slider
#: really does run out of positive cutoff before it runs out of pairs.
_HAS_NEGATIVE_PAIR = np.array(
    [
        [0.0, 0.9, 0.1, -0.5],
        [0.2, 0.0, 0.8, -0.30],
        [0.3, -0.4, 0.0, 0.7],
        [0.6, -0.2, 0.15, 0.0],
    ]
)


def test_the_slider_has_a_last_position_before_the_cutoff_turns_negative():
    matrix = _HAS_NEGATIVE_PAIR
    limit = max_positive_edge_count(matrix)
    assert limit < pair_maxima(matrix).size
    assert threshold_for_edge_count(matrix, limit) > 0
    assert threshold_for_edge_count(matrix, limit + 1) <= 0


def test_a_negative_cutoff_is_warned_about(caplog):
    matrix = _HAS_NEGATIVE_PAIR
    limit = max_positive_edge_count(matrix)
    with caplog.at_level("WARNING"):
        select_edges(matrix, edge_count=limit + 1)
    assert "negative" in caplog.text.lower()


def test_edge_count_out_of_range_says_what_the_range_is(tiny):
    with pytest.raises(NetworkError, match=r"between 1 and 6"):
        select_edges(tiny.matrix, edge_count=99)


def test_only_one_selection_rule_at_a_time(tiny):
    with pytest.raises(NetworkError, match="only one of"):
        select_edges(tiny.matrix, edge_count=3, top=4)


def test_top_takes_exactly_that_many(tiny):
    edges = select_edges(tiny.matrix, top=4)
    assert len(edges) == 4
    # And they really are the four largest off-diagonal entries.
    offdiag = tiny.matrix[~np.eye(4, dtype=bool)]
    np.testing.assert_allclose(
        np.sort(edges.weight)[::-1], np.sort(offdiag)[::-1][:4]
    )


def test_top_by_magnitude_lets_a_strong_negative_in(tiny):
    # 0.9 0.8 0.7 0.6 then, by magnitude, -0.5 -- but by signed value, 0.4.
    signed = select_edges(tiny.matrix, top=5)
    by_size = select_edges(tiny.matrix, top=5, absolute=True)
    assert signed.n_negative == 0
    assert by_size.n_negative == 1          # the -0.5
    # The weight kept its sign, so the renderer can still colour it.
    assert by_size.weight.min() == pytest.approx(-0.5)


def test_a_threshold_is_inclusive(tiny):
    edges = select_edges(tiny.matrix, threshold=0.7)
    assert sorted(np.round(edges.weight, 6)) == [0.7, 0.8, 0.9]


def test_dropping_negatives_removes_them_rather_than_recolouring(tiny):
    kept = select_edges(tiny.matrix, threshold=-1.0, drop_negative=True)
    assert kept.n_negative == 0
    assert (kept.weight > 0).all()


def test_self_edges_are_left_out_unless_asked_for():
    matrix = np.array([[0.5, 0.1], [0.1, 0.5]])
    assert len(select_edges(matrix, threshold=0.0)) == 2
    assert len(select_edges(matrix, threshold=0.0, self_edges=True)) == 4


def test_reciprocal_pairs_are_counted(tiny):
    edges = select_edges(tiny.matrix, threshold=-1.0)
    # Every pair is present in both directions once nothing is thresholded out.
    assert edges.reciprocal == 6


#: NeuroMArVL's own published ``k -> arrows`` table for the effective
#: connectivity datasets this module was built against.  Reproducing it is what
#: makes "the same figure, in batch" a true statement rather than a hope.
PUBLISHED_EDGE_COUNTS = {
    "listen":      {8: 14, 12: 19, 16: 23, 20: 31, 24: 36, 28: 42, 35: 69},
    "naming":      {8: 10, 12: 18, 16: 23, 20: 29, 24: 35, 28: 42, 35: 64},
    "reading":     {8: 11, 12: 20, 16: 25, 20: 32, 24: 39, 28: 47, 35: 64},
    "writing":     {8: 10, 12: 19, 16: 27, 20: 32, 24: 37, 28: 41, 35: 63},
    "taskaverage": {16: 24},
}

#: And the last slider position whose cutoff is still positive.
PUBLISHED_POSITIVE_LIMIT = {
    "listen": 31, "naming": 32, "reading": 30, "writing": 25,
    "taskaverage": 30,
}


def _marvl_dir() -> "Path | None":
    raw = os.environ.get("CIFTI_STATE_NETWORK_DATA")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


@pytest.mark.parametrize("dataset", sorted(PUBLISHED_EDGE_COUNTS))
def test_published_edge_counts(dataset):
    """Our cutoff draws the same arrows the browser drew, at every k."""
    root = _marvl_dir()
    if root is None:
        pytest.skip("set CIFTI_STATE_NETWORK_DATA to the dataset directory")
    matches = list(root.rglob(f"matrix_{dataset}.txt"))
    if not matches:
        pytest.skip(f"matrix_{dataset}.txt not found under {root}")
    matrix = read_matrix(matches[0])
    for k, expected in PUBLISHED_EDGE_COUNTS[dataset].items():
        assert len(select_edges(matrix, edge_count=k)) == expected, (
            f"{dataset}: k={k} should draw {expected} arrows"
        )
    assert max_positive_edge_count(matrix) == PUBLISHED_POSITIVE_LIMIT[dataset]


# --------------------------------------------------------------------------- #
# style
# --------------------------------------------------------------------------- #


def test_node_size_spreads_the_attribute_over_the_range(tiny):
    style = NodeStyle(size_by="strength", size_range=(1.0, 5.0))
    radii = node_radii(tiny, style)
    assert radii.min() == pytest.approx(1.0)
    assert radii.max() == pytest.approx(5.0)
    # Order preserved.
    assert list(np.argsort(radii)) == list(np.argsort(tiny.attribute("strength")))


def test_size_from_zero_keeps_radii_proportional(tiny):
    style = NodeStyle(size_by="strength", size_range=(1.0, 5.0),
                      size_from_zero=True)
    radii = node_radii(tiny, style)
    values = tiny.attribute("strength")
    ratio = radii / values
    np.testing.assert_allclose(ratio, ratio[0])


def test_a_constant_attribute_does_not_divide_by_zero(tiny):
    data = NetworkData(
        coordinates=tiny.coordinates, matrix=tiny.matrix,
        attributes={"flat": np.ones(4)},
    )
    radii = node_radii(data, NodeStyle(size_by="flat", size_range=(1.0, 5.0)))
    assert np.isfinite(radii).all()
    np.testing.assert_allclose(radii, 3.0)


def test_a_few_distinct_values_are_treated_as_groups(tiny):
    style = NodeStyle(color_by="group_id",
                      discrete_colors=("#ff0000", "#00ff00", "#0000ff"))
    rgb = node_colors(tiny, style)
    np.testing.assert_allclose(rgb[0], rgb[1])          # both group 1
    np.testing.assert_allclose(rgb[0], [1.0, 0.0, 0.0])
    np.testing.assert_allclose(rgb[2], [0.0, 1.0, 0.0])
    np.testing.assert_allclose(rgb[3], [0.0, 0.0, 1.0])


def test_many_distinct_values_are_treated_as_a_measurement(tiny):
    data = NetworkData(
        coordinates=np.tile(tiny.coordinates, (8, 1)),
        matrix=np.zeros((32, 32)),
        attributes={"x": np.linspace(0, 1, 32)},
    )
    style = NodeStyle(color_by="x")
    assert style.resolved_color_mode(data.attribute("x")) == "continuous"
    rgb = node_colors(data, style)
    assert len(np.unique(rgb, axis=0)) > 20


def test_too_few_colours_for_the_groups_warns_and_repeats(tiny, caplog):
    style = NodeStyle(color_by="group_id", discrete_colors=("#ff0000",))
    with caplog.at_level("WARNING"):
        rgb = node_colors(tiny, style)
    assert "only 1 colours" in caplog.text
    np.testing.assert_allclose(rgb, np.tile([1.0, 0.0, 0.0], (4, 1)))


def test_node_transitioning_edges_start_and_end_at_the_node_colours(tiny):
    rgb = node_colors(tiny, NodeStyle(color_by="group_id"))
    edges = select_edges(tiny.matrix, top=3)
    start, end = edge_colors(
        edges.weight, edges.source, edges.target, rgb,
        EdgeStyle(color_mode="node-transitioning"),
    )
    np.testing.assert_allclose(start, rgb[edges.source])
    np.testing.assert_allclose(end, rgb[edges.target])


def test_a_direction_gradient_overrides_the_colour_mode(tiny):
    rgb = node_colors(tiny, NodeStyle(color_by="group_id"))
    edges = select_edges(tiny.matrix, top=3)
    start, end = edge_colors(
        edges.weight, edges.source, edges.target, rgb,
        EdgeStyle(color_mode="weight", direction_mode="gradient",
                  start_color="#ff0000", end_color="#0000ff"),
    )
    np.testing.assert_allclose(start, np.tile([1.0, 0.0, 0.0], (3, 1)))
    np.testing.assert_allclose(end, np.tile([0.0, 0.0, 1.0], (3, 1)))


def test_sign_colouring_splits_by_sign(tiny):
    edges = select_edges(tiny.matrix, threshold=-1.0)
    style = EdgeStyle(color_mode="sign", positive_color="#ff0000",
                      negative_color="#0000ff")
    start, _ = edge_colors(
        edges.weight, edges.source, edges.target, np.zeros((4, 3)), style
    )
    positive = edges.weight >= 0
    assert np.allclose(start[positive], [1.0, 0.0, 0.0])
    assert np.allclose(start[~positive], [0.0, 0.0, 1.0])
    assert positive.sum() and (~positive).sum()


def test_signed_colouring_keeps_each_sign_in_its_own_colour(tiny):
    """The bug this guards: a weak positive edge coming out purple.

    A single diverging ramp fitted symmetrically about zero puts a weight of
    +0.05 in a set that runs to +0.19 only a quarter of the way towards the
    positive colour -- so it renders closer to the negative one than to the
    positive one, which is the opposite of what it means.
    """
    from matplotlib.colors import to_rgb

    edges = select_edges(tiny.matrix, threshold=-1.0)
    style = EdgeStyle(color_mode="signed", positive_color="#8e1616",
                      negative_color="#3f2071")
    start, end = edge_colors(
        edges.weight, edges.source, edges.target, np.zeros((4, 3)), style
    )
    np.testing.assert_allclose(start, end)     # no gradient along the edge

    positive = edges.weight > 0
    assert positive.sum() and (~positive).sum()
    red, purple = np.array(to_rgb("#8e1616")), np.array(to_rgb("#3f2071"))

    for row, is_positive in zip(start, positive):
        to_red = np.linalg.norm(row - red)
        to_purple = np.linalg.norm(row - purple)
        assert (to_red < to_purple) == bool(is_positive)

    # And within a sign, the strongest edge is the darkest.
    order = np.argsort(np.abs(edges.weight[positive]))
    lightness = start[positive][order].sum(axis=1)
    assert lightness[0] > lightness[-1]


def test_the_signed_ramp_ends_exactly_on_the_colours_you_set(tiny):
    from matplotlib.colors import to_rgb

    style = EdgeStyle(color_mode="signed", positive_color="#8e1616",
                      negative_color="#3f2071")
    weights = np.array([1.0, -1.0, 0.25])
    start, _ = edge_colors(
        weights, np.zeros(3, int), np.zeros(3, int), np.zeros((4, 3)), style
    )
    np.testing.assert_allclose(start[0], to_rgb("#8e1616"), atol=2e-3)
    np.testing.assert_allclose(start[1], to_rgb("#3f2071"), atol=2e-3)


def test_signed_fade_zero_makes_every_edge_the_full_colour(tiny):
    from matplotlib.colors import to_rgb

    weights = np.array([1.0, 0.05])
    style = EdgeStyle(color_mode="signed", positive_color="#8e1616",
                      signed_fade=0.0)
    start, _ = edge_colors(
        weights, np.zeros(2, int), np.zeros(2, int), np.zeros((4, 3)), style
    )
    np.testing.assert_allclose(start[0], start[1], atol=2e-3)
    np.testing.assert_allclose(start[0], to_rgb("#8e1616"), atol=2e-3)


def test_edge_width_by_weight_stays_inside_its_range(tiny):
    edges = select_edges(tiny.matrix, threshold=-1.0)
    radii = edge_radii(
        edges.weight, EdgeStyle(width_by_weight=True, width_range=(0.2, 1.5))
    )
    assert radii.min() == pytest.approx(0.2)
    assert radii.max() == pytest.approx(1.5)


def test_a_bad_mode_name_lists_the_good_ones():
    with pytest.raises(NetworkError, match="node-transitioning"):
        EdgeStyle(color_mode="rainbow")
    with pytest.raises(NetworkError, match="taper"):
        EdgeStyle(direction_mode="wiggle")


# --------------------------------------------------------------------------- #
# NeuroMArVL settings files
# --------------------------------------------------------------------------- #


MARVL_SETTINGS = {
    "edgeSettings": {
        "colorBy": "none",
        "size": 1,
        "thicknessByWeight": False,
        "directionMode": "arrow",
        "directionStartColor": "#FF0000",
        "directionEndColor": "#0000FF",
        "edgeColorByNodeTransition": False,
        "edgeColorByNodeTransitionColor": "#ee2211",
    },
    "nodeSettings": {
        "nodeSizeOrColor": "node-size",
        "nodeSizeAttribute": "in_degree",
        "nodeSizeMin": 0.4,
        "nodeSizeMax": 1.8,
        "nodeColorAttribute": "group_id",
        "nodeColorMode": "discrete",
        "nodeColorDiscrete": ["#ff69b4", "#add8e6", "#4caf50"],
    },
    "surfaceSettings": {"opacity": 0.6, "color": "#e3e3e3"},
    "displaySettings": {"mode": "Top", "labels": True, "split": False,
                        "rotation": False},
    "saveApps": [{"brainSurfaceMode": "both", "edgeCount": 16}],
}


@pytest.fixture
def settings_file(tmp_path) -> Path:
    path = tmp_path / "size_in_degree.json"
    path.write_text(json.dumps(MARVL_SETTINGS, indent=2))
    return path


def test_a_settings_file_brings_everything_across(settings_file):
    style = load_marvl_settings(settings_file)
    assert style.node.size_by == "in_degree"
    assert style.node.color_by == "group_id"
    assert style.node.discrete_colors == ("#ff69b4", "#add8e6", "#4caf50")
    assert style.node.labels is True
    assert style.edge.direction_mode == "arrow"
    assert style.brain.color == "#e3e3e3"
    assert style.brain.opacity == pytest.approx(0.6)
    assert style.views == ("dorsal",)            # its "Top"
    assert style.edge_count == 16


def test_the_size_range_is_scaled_into_millimetres(settings_file):
    style = load_marvl_settings(settings_file, size_to_mm=3.5)
    assert style.node.size_range == pytest.approx((1.4, 6.3))
    # Relative sizes are what the setting means, so the ratio must survive.
    raw = MARVL_SETTINGS["nodeSettings"]
    assert style.node.size_range[1] / style.node.size_range[0] == pytest.approx(
        raw["nodeSizeMax"] / raw["nodeSizeMin"]
    )


def test_the_transition_colour_is_not_used_for_plain_edges(settings_file):
    """The bug this guards: every grey edge coming out bright red."""
    style = load_marvl_settings(settings_file)
    assert style.edge.color_mode == "none"
    assert style.edge.color.lower() != "#ee2211"


def test_the_transition_colour_is_used_when_the_transition_is_on(tmp_path):
    payload = json.loads(json.dumps(MARVL_SETTINGS))
    payload["edgeSettings"]["edgeColorByNodeTransition"] = True
    path = tmp_path / "s.json"
    path.write_text(json.dumps(payload))
    style = load_marvl_settings(path)
    assert style.edge.color_mode == "node-transitioning"
    assert style.edge.color.lower() == "#ee2211"


def test_animation_falls_back_to_arrows(tmp_path, caplog):
    payload = json.loads(json.dumps(MARVL_SETTINGS))
    payload["edgeSettings"]["directionMode"] = "animation"
    path = tmp_path / "s.json"
    path.write_text(json.dumps(payload))
    with caplog.at_level("INFO"):
        style = load_marvl_settings(path)
    assert style.edge.direction_mode == "arrow"
    assert "animation" in caplog.text


def test_one_hemisphere_in_the_settings_means_one_hemisphere(tmp_path):
    payload = json.loads(json.dumps(MARVL_SETTINGS))
    payload["saveApps"][0]["brainSurfaceMode"] = "left"
    path = tmp_path / "s.json"
    path.write_text(json.dumps(payload))
    assert load_marvl_settings(path).brain.hemispheres == ("left",)


def test_settings_survive_a_round_trip(settings_file, tmp_path):
    style = load_marvl_settings(settings_file)
    out = save_marvl_settings(style, tmp_path / "again.json")
    again = load_marvl_settings(out)
    assert again.node.size_by == style.node.size_by
    assert again.node.color_by == style.node.color_by
    assert again.node.size_range == pytest.approx(style.node.size_range)
    assert again.edge.direction_mode == style.edge.direction_mode
    assert again.brain.opacity == pytest.approx(style.brain.opacity)
    assert again.edge_count == style.edge_count
    assert again.views == style.views


def test_a_settings_file_that_is_not_json_says_so(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("not json at all")
    with pytest.raises(NetworkError, match="not valid JSON"):
        load_marvl_settings(path)


# --------------------------------------------------------------------------- #
# the scene
# --------------------------------------------------------------------------- #


def _pyvista_or_skip():
    pytest.importorskip("pyvista")
    try:
        import pyvista as pv

        plotter = pv.Plotter(off_screen=True, window_size=[64, 64])
        plotter.add_mesh(pv.Sphere())
        plotter.screenshot(return_img=True)
        plotter.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no offscreen OpenGL context here ({exc})")


@pytest.fixture
def settings_no_surface(tmp_path):
    from cifti_state.config import Settings

    return Settings()


def test_the_scene_draws_one_sphere_per_node_and_the_brain_last(tiny, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.scene import build_network_scene

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    style = NetworkStyle(
        node=NodeStyle(color_by="group_id"),
        edge=EdgeStyle(direction_mode="arrow"),
    )
    scene = build_network_scene(tiny, settings, style, top=3)
    assert len(scene.node_actors) == tiny.n_nodes
    # One tube plus one cone per edge.
    assert len(scene.edge_actors) == 2 * 3
    assert scene.edges is not None and len(scene.edges) == 3
    # No surface configured, so nothing to draw last -- but the list exists and
    # is the one the renderer adds after everything else.
    assert scene.brain_actors == []


def test_direction_modes_without_arrows_add_no_cones(tiny, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.scene import build_network_scene

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    for mode in ("none", "gradient", "taper", "opacity"):
        scene = build_network_scene(
            tiny, settings, NetworkStyle(edge=EdgeStyle(direction_mode=mode)),
            top=3,
        )
        assert len(scene.edge_actors) == 3, mode


def test_a_tube_is_coloured_from_its_source_end_to_its_target_end(tiny, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.scene import build_network_scene

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    style = NetworkStyle(
        edge=EdgeStyle(direction_mode="gradient",
                       start_color="#ff0000", end_color="#0000ff"),
    )
    scene = build_network_scene(tiny, settings, style, top=1)
    tube, _ = scene.edge_actors[0]
    rgba = np.asarray(tube.point_data["rgba"])
    # Red somewhere, blue somewhere, and nothing in between is both.
    assert rgba[:, 0].max() > 200 and rgba[:, 2].max() > 200
    assert (rgba[:, 0].astype(int) + rgba[:, 2].astype(int)).max() < 300


def test_an_unknown_view_lists_the_known_ones():
    from cifti_state.network.scene import resolve_view

    assert resolve_view("Top") == "dorsal"
    assert resolve_view("LH") == "left"
    with pytest.raises(NetworkError, match="anterior"):
        resolve_view("sideways")


def test_coverage_notices_coordinates_in_the_wrong_space(tiny, monkeypatch):
    from cifti_state.config import Settings
    from cifti_state.io.surface import Surface
    from cifti_state.network import scene as scene_module

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: Path("fake.surf.gii"),
    )
    box = np.array([[-70.0, -100.0, -50.0], [70.0, 70.0, 80.0]])
    monkeypatch.setattr(
        scene_module, "load_surface",
        lambda path, hemisphere="", kind="": Surface(
            coords=box, faces=np.zeros((0, 3), dtype=int), hemisphere=hemisphere,
            kind=kind,
        ),
    )
    inside = scene_module.check_coverage(tiny, settings)
    assert inside["n_inside"] == inside["n_nodes"]

    # Voxel indices rather than millimetres: everything piles into one corner.
    voxels = NetworkData(
        coordinates=tiny.coordinates / 100.0 + 200.0, matrix=tiny.matrix,
        labels=tiny.labels,
    )
    outside = scene_module.check_coverage(voxels, settings)
    assert outside["n_inside"] == 0
    assert outside["worst_outside_mm"] > 100
    assert set(outside["outside_nodes"]) == set(tiny.labels)


# --------------------------------------------------------------------------- #
# the figure
# --------------------------------------------------------------------------- #


def test_a_figure_is_written_with_one_panel_per_view(tiny, tmp_path, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.render import render_network

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    out = tmp_path / "net.png"
    figure = render_network(
        tiny, settings, out=out, views=["left", "dorsal"], top=4,
        size=(220, 200), dpi=80,
    )
    assert out.exists() and out.stat().st_size > 0
    assert figure.views == ("left", "dorsal")
    assert len(figure.panels) == 2
    assert figure.edges is not None and len(figure.edges) == 4


def test_the_caption_states_the_rule_and_the_cutoff(tiny, tmp_path, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.render import render_network

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    figure = render_network(
        tiny, settings, out=tmp_path / "a.png", views=["left"], edge_count=3,
        size=(220, 200), dpi=80,
    )
    assert "k=3" in figure.caption
    assert "0.7" in figure.caption
    assert "directed" in figure.caption


def test_negatives_drawn_unmarked_are_called_out(tiny, tmp_path, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.render import render_network

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    plain = render_network(
        tiny, settings, out=tmp_path / "b.png", views=["left"],
        threshold=-1.0, size=(220, 200), dpi=80,
    )
    assert "not marked as such" in plain.caption

    signed = render_network(
        tiny, settings, out=tmp_path / "c.png", views=["left"],
        threshold=-1.0, size=(220, 200), dpi=80,
        style=NetworkStyle(edge=EdgeStyle(color_mode="sign")),
    )
    assert "not marked as such" not in signed.caption
    assert "negative edges" in signed.caption


def test_cropping_keeps_every_panel_at_the_same_pixel_scale():
    from cifti_state.network.render import _crop_panels

    def panel(size, offset):
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[offset:offset + size, offset:offset + size] = 20
        return img

    small, large = panel(30, 80), panel(90, 40)
    cropped = _crop_panels([small, large], "#ffffff", margin=0.0)
    assert cropped[0].shape == cropped[1].shape
    ink = [int((c < 128).any(axis=2).sum()) for c in cropped]
    # 30x30 and 90x90 of ink: the big one is still nine times the small one.
    assert ink[1] == pytest.approx(ink[0] * 9, rel=0.02)


# --------------------------------------------------------------------------- #
# the interactive window
# --------------------------------------------------------------------------- #


def test_the_printed_command_reproduces_what_you_tuned(tiny):
    from cifti_state.network.interactive import command_line_for

    style = NetworkStyle(
        node=NodeStyle(size_by="strength", color_by="group_id",
                       discrete_colors=("#ff69b4", "#6ab4e3"), labels=True),
        edge=EdgeStyle(color_mode="signed", width_by_weight=True,
                       width_range=(0.3, 1.6), negative_color="#3f2071",
                       positive_color="#8e1616"),
        brain=BrainStyle(opacity=0.42, hemispheres=("left",)),
    )
    line = command_line_for(style, source="net/", edge_count=16, view="left",
                            output="fig.png")
    for expected in ("-k 16", "--views left", "--size-by strength",
                     "--color-by group_id", "#ff69b4", "#6ab4e3",
                     "--labels-on", "--edge-color signed", "#3f2071",
                     "--width-range 0.30 1.60", "--brain-opacity 0.42",
                     "--hemisphere left", "-o fig.png"):
        assert expected in line, expected


def test_the_command_survives_a_round_trip_through_the_parser(tiny):
    """Whatever the window prints has to actually parse and mean the same."""
    import shlex

    from cifti_state.cli import build_parser
    from cifti_state.network.interactive import command_line_for

    style = NetworkStyle(
        node=NodeStyle(size_by="strength", color_by="group_id", labels=False),
        edge=EdgeStyle(color_mode="signed", width_by_weight=True,
                       width_range=(0.3, 1.6)),
        brain=BrainStyle(opacity=0.42),
    )
    line = command_line_for(style, source="net/", edge_count=16, view="left")
    args = build_parser().parse_args(shlex.split(line)[1:])
    assert args.command == "network"
    assert args.edge_count == 16
    assert args.views == ["left"]
    assert args.size_by == "strength"
    assert args.color_by == "group_id"
    assert args.edge_color == "signed"
    assert args.width_range == [0.3, 1.6]
    assert args.brain_opacity == pytest.approx(0.42)
    assert args.show_labels is False


def test_the_sliders_rebuild_the_scene_without_losing_themselves(tiny, monkeypatch):
    """clear_actors() must not take the widgets or the camera with it."""
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.interactive import explore

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    style = NetworkStyle(node=NodeStyle(color_by="group_id"))
    state = explore(tiny, settings, style, edge_count=3, view="left",
                    show=False, window_size=(200, 160))
    plotter = state["plotter"]
    try:
        assert len(state["edges"]) == 3
        before = plotter.camera_position

        state["set_edge_count"](5)
        assert len(state["edges"]) == 6      # k is not the arrow count
        state["set_edge_count"](1)
        assert len(state["edges"]) == 1

        state["set_width_scale"](2.0)
        assert state["scene"].edges is not None
        assert plotter.camera_position == before
    finally:
        plotter.close()


def test_turning_the_shell_down_to_zero_removes_it(tiny, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.io.surface import Surface
    from cifti_state.network import scene as scene_module
    from cifti_state.network.interactive import explore

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: Path("fake.surf.gii"),
    )
    monkeypatch.setattr(
        scene_module, "load_surface",
        lambda path, hemisphere="", kind="": Surface(
            coords=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            faces=np.array([[0, 1, 2]]), hemisphere=hemisphere, kind=kind,
        ),
    )
    state = explore(tiny, settings, NetworkStyle(), edge_count=3, view="left",
                    show=False, window_size=(200, 160))
    plotter = state["plotter"]
    try:
        assert len(state["scene"].brain_actors) == 2
        state["set_opacity"](0.0)
        assert state["scene"].brain_actors == []
        state["set_opacity"](0.5)
        assert len(state["scene"].brain_actors) == 2
    finally:
        plotter.close()


# --------------------------------------------------------------------------- #
# the control window
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    pytest.importorskip("pyvistaqt")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tiny, monkeypatch):
    _pyvista_or_skip()
    from cifti_state.config import Settings
    from cifti_state.network.qtwindow import NetworkWindow

    settings = Settings()
    monkeypatch.setattr(
        type(settings), "surface_for",
        lambda self, hemi, kind, mesh=None: None,
    )
    style = NetworkStyle(
        node=NodeStyle(size_by="strength", color_by="group_id",
                       discrete_colors=("#ff0000", "#00ff00", "#0000ff")),
        edge=EdgeStyle(color_mode="signed"),
    )
    win = NetworkWindow(tiny, settings, style, edge_count=3, view="left")
    yield win
    win.close()


def test_the_window_draws_itself_on_open(window):
    assert window._scene is not None
    assert len(window._scene.edges) == 3
    assert "3" in window.readout.text()


def test_one_colour_swatch_per_level_of_the_colour_attribute(window):
    # group_id has three levels, so three pickers -- not one per node.
    assert [b.color() for b in window.group_colors] == [
        "#ff0000", "#00ff00", "#0000ff"
    ]


def test_the_label_checkbox_turns_labels_on_and_off(window):
    window.labels_on.setChecked(False)
    window.refresh()
    assert window._scene.label_points is None

    window.labels_on.setChecked(True)
    window.refresh()
    assert len(window._scene.label_text) == window.data.n_nodes


def test_changing_the_rule_disables_the_other_rules_inputs(window):
    window.rule.setCurrentIndex(2)          # strongest N
    assert not window.k_spin.isEnabled()
    assert window.top_spin.isEnabled()
    window.top_spin.setValue(5)
    window.refresh()
    assert len(window._scene.edges) == 5

    window.rule.setCurrentIndex(0)
    assert window.k_spin.isEnabled()


def test_a_change_marks_the_button_until_it_is_applied(window):
    """The whole point of a manual Refresh is that you can see it is pending."""
    window.refresh()
    assert window.refresh_button.text() == "Refresh"
    window.brain_opacity.setValue(0.2)
    assert window.refresh_button.text().endswith("•")
    window.refresh()
    assert window.refresh_button.text() == "Refresh"


def test_auto_applies_without_the_button(window):
    window.auto.setChecked(True)
    try:
        window.split_mm.setValue(18.0)
        assert window.refresh_button.text() == "Refresh"
        assert window.current_style().brain.split_mm == pytest.approx(18.0)
    finally:
        window.auto.setChecked(False)


def test_the_command_box_tracks_what_is_on_screen(window):
    window.negative_color.set_color("#112233")
    window.labels_on.setChecked(True)
    window.refresh()
    line = window.command_box.text()
    assert "#112233" in line
    assert "--labels-on" in line
    assert "-k 3" in line

    window.rule.setCurrentIndex(2)
    window.top_spin.setValue(4)
    window.refresh()
    line = window.command_box.text()
    assert "--top 4" in line
    assert "-k " not in line


def test_the_command_box_stays_parseable(window):
    """It is only useful if it can be pasted back."""
    import shlex

    from cifti_state.cli import build_parser

    window.refresh()
    args = build_parser().parse_args(shlex.split(window.command_box.text())[1:])
    assert args.command == "network"


def test_the_readout_warns_once_k_passes_the_positive_cutoff(window):
    window.rule.setCurrentIndex(0)
    window.k_spin.setValue(window.positive_limit)
    window.refresh()
    assert "past" not in window.readout.text()

    if window.positive_limit < window.n_pairs:
        window.k_spin.setValue(window.positive_limit + 1)
        window.refresh()
        assert "past" in window.readout.text()


def test_a_bad_combination_warns_instead_of_killing_the_window(window, monkeypatch):
    from cifti_state.network import qtwindow as module

    seen = []
    monkeypatch.setattr(
        module, "build_network_scene",
        lambda *a, **k: (_ for _ in ()).throw(NetworkError("nope")),
    )
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: seen.append(a[-1]))
    before = window._scene
    window.refresh()
    assert seen and "nope" in seen[0]
    assert window._scene is before      # the old scene is still up
