"""IO: the three CIFTI layouts, adjacency back-ends, and atlas label tables."""

from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

from cifti_state.io.cifti import (
    describe_layout,
    load_surface_stat_map,
    make_dense_surface_template,
    save_like,
)
from cifti_state.io.neighbors import (
    adjacency_from_surface,
    adjacency_from_txt,
    neighbor_lists,
)


# --------------------------------------------------------------------------- #
# layouts
# --------------------------------------------------------------------------- #


def test_layout_names():
    assert "91k" in describe_layout(91282)
    assert "59k" in describe_layout(59412)
    assert "64k" in describe_layout(64984)
    assert describe_layout(12345) == "12345 greyordinates"


@pytest.mark.requires_data
def test_91k_expands_to_the_full_mesh(settings, examples_dir):
    path = examples_dir / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not path.exists():
        pytest.skip("example not present")
    stat_map = load_surface_stat_map(path)

    assert stat_map.left.n_vertices == 32492
    assert stat_map.right.n_vertices == 32492
    assert stat_map.left.n_present == 29696       # medial wall dropped
    assert stat_map.right.n_present == 29716
    assert stat_map.left.has_medial_wall
    # every vertex the file did not carry holds the fill value
    assert np.all(stat_map.left.values[~stat_map.left.present] == 0.0)
    assert stat_map.concatenated().size == 64984


@pytest.mark.requires_data
def test_59k_and_91k_expand_identically(examples_dir, tmp_path):
    """A cortex-only file must give the same full-mesh arrays as the 91k one."""
    source_path = examples_dir / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not source_path.exists():
        pytest.skip("example not present")

    source = nib.load(str(source_path))
    brain_axis = source.header.get_axis(1)
    row = source.get_fdata()[0]

    parts, positions = [], []
    for name, sl, _model in brain_axis.iter_structures():
        if name in ("CIFTI_STRUCTURE_CORTEX_LEFT", "CIFTI_STRUCTURE_CORTEX_RIGHT"):
            parts.append(brain_axis[sl])
            positions.append(np.arange(brain_axis.size)[sl])
    cortex_axis = parts[0] + parts[1]
    index = np.concatenate(positions)

    cortex_path = tmp_path / "cortex59k.dscalar.nii"
    nib.save(
        nib.cifti2.Cifti2Image(
            row[index][None, :].astype(np.float32),
            header=(nib.cifti2.ScalarAxis(["x"]), cortex_axis),
            nifti_header=source.nifti_header,
        ),
        str(cortex_path),
    )

    a = load_surface_stat_map(source_path)
    b = load_surface_stat_map(cortex_path)
    assert b.template.n_columns == 59412
    assert np.array_equal(
        np.nan_to_num(a.concatenated()), np.nan_to_num(b.concatenated())
    )
    assert np.array_equal(a.present_mask(), b.present_mask())


@pytest.mark.requires_data
@pytest.mark.parametrize("name", ["group_mean_thresh_fdr_E_C.dscalar.nii"])
def test_round_trip_preserves_values(examples_dir, tmp_path, name):
    path = examples_dir / name
    if not path.exists():
        pytest.skip("example not present")
    original = load_surface_stat_map(path)
    out = save_like(
        tmp_path / "round_trip.dscalar.nii",
        original.left.values,
        original.right.values,
        original.template,
    )
    back = load_surface_stat_map(out)
    assert np.allclose(
        np.nan_to_num(original.concatenated()),
        np.nan_to_num(back.concatenated()),
        atol=1e-6,
    )


def test_dense_template_built_from_scratch():
    template = make_dense_surface_template(32492, 32492)
    assert template.n_columns == 64984
    assert template.has_structure("CIFTI_STRUCTURE_CORTEX_LEFT")
    assert template.n_vertices("CIFTI_STRUCTURE_CORTEX_RIGHT") == 32492


# --------------------------------------------------------------------------- #
# adjacency
# --------------------------------------------------------------------------- #


def test_adjacency_from_txt_parses_ragged_rows(tmp_path):
    path = tmp_path / "nb.txt"
    path.write_text("0 1 2\n1 0 2\n2 0 1 NaN\n", encoding="utf-8")
    adjacency = adjacency_from_txt(path, 3)
    assert adjacency.shape == (3, 3)
    lists = neighbor_lists(adjacency)
    assert sorted(lists[0]) == [1, 2]
    assert sorted(lists[2]) == [0, 1]
    assert adjacency[0, 0] == 0                     # no self loops


def test_adjacency_is_symmetric_even_from_one_sided_input(tmp_path):
    path = tmp_path / "nb.txt"
    path.write_text("0 1\n1\n", encoding="utf-8")
    adjacency = adjacency_from_txt(path, 2)
    assert adjacency[0, 1] == 1 and adjacency[1, 0] == 1


def test_adjacency_from_surface_on_a_single_triangle():
    faces = np.array([[0, 1, 2]])
    adjacency = adjacency_from_surface(faces, 3)
    assert adjacency.sum() == 6                     # three undirected edges
    assert np.array_equal(adjacency.toarray().diagonal(), [0, 0, 0])


@pytest.mark.requires_data
def test_txt_and_surface_adjacency_agree(settings):
    """The pre-computed tables and the mesh topology describe the same graph."""
    from cifti_state.io.neighbors import check_agreement

    from cifti_state.config import ConfigError

    for hemi in ("left", "right"):
        try:
            txt = settings.resources.neighbor_path(settings.defaults.mesh, hemi)
        except ConfigError:
            pytest.skip("no neighbour tables configured (the bundled example "
                        "derives the adjacency from the mesh instead)")
        surface = settings.resources.surface_path(hemi, "midthickness")
        if not txt.exists() or not surface.exists():
            pytest.skip("template resources not available")
        identical, n_differing = check_agreement(txt, surface)
        assert identical, f"{hemi}: {n_differing} vertices differ"


# --------------------------------------------------------------------------- #
# atlases
# --------------------------------------------------------------------------- #


@pytest.mark.requires_data
def test_atlas_names_come_from_the_label_table(settings):
    from cifti_state.io.atlas import load_atlas

    atlas = load_atlas("Glasser_2016", settings)
    assert atlas.left.n_vertices == 32492
    assert atlas.right.n_vertices == 32492
    assert atlas.n_regions() == 360
    assert atlas.is_unknown("left", 0)
    # The hemisphere prefix is stripped; hemisphere is a column, not a name.
    assert not atlas.region_name("left", 1).startswith("L_")


@pytest.mark.requires_data
def test_both_hemispheres_read_their_own_file(settings):
    """The AAL branch of w_find_brain_region.m read the left file twice."""
    from cifti_state.io.atlas import load_atlas

    if not (settings.resources.atlas_dir / "data_aal_L.label.gii").exists():
        pytest.skip("data_aal atlas not present")
    atlas = load_atlas("data_aal", settings)
    assert not np.array_equal(atlas.left.labels, atlas.right.labels)


@pytest.mark.requires_data
def test_desikan_label_ids_are_per_hemisphere(settings):
    from cifti_state.io.atlas import load_atlas

    try:
        atlas = load_atlas("Desikan", settings)
    except FileNotFoundError:
        pytest.skip("the Desikan atlas is not in this template pack")
    # Both hemispheres use the same small id range; no +36 offset is applied.
    assert atlas.left.labels.max() <= 40
    assert atlas.right.labels.max() <= 40
