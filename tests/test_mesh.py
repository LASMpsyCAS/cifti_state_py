"""Meshes: naming them, finding their templates, and converting between them.

The naming and locating tests are pure and always run.  The conversion tests
need Connectome Workbench and the template packs, so they skip without them --
and when they do run they check the two things a resampling can silently get
wrong: whether the data survived the trip (a round trip has to come back), and
whether the medial wall and NaN were handled or merely propagated.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from cifti_state.mesh.spaces import (
    KNOWN_MESHES,
    MeshError,
    identify_mesh,
    list_meshes,
    parse_mesh,
)
from cifti_state.mesh.templates import MeshLibrary, common_registration

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = Path(os.environ.get("CIFTI_STATE_TEST_EXAMPLES", REPO / "example_data"))
CONFIG = Path(
    os.environ.get("CIFTI_STATE_TEST_CONFIG", REPO / "example_data" / "example.yaml")
)


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #


def test_a_bare_density_means_the_fs_lr_mesh():
    """What every configuration file in this package has always meant by "32k"."""
    assert parse_mesh("32k").name == "fsLR:32k"
    assert parse_mesh("fsLR:32k") is parse_mesh("32k")
    assert parse_mesh("FS_LR_32K").name == "fsLR:32k"


def test_the_fsaverage_spellings_all_land_in_the_same_place():
    for spelling in ("fsaverage5", "fsavg5", "fs5", "fsaverage-10k", "FSAVERAGE5"):
        assert parse_mesh(spelling).name == "fsaverage5"


def test_an_unknown_mesh_names_the_ones_that_exist():
    with pytest.raises(MeshError, match="unknown mesh"):
        parse_mesh("fsLR:7k")


def test_every_mesh_knows_its_own_size_and_they_are_all_different_within_a_family():
    for family in ("fsLR", "fsaverage"):
        sizes = [m.n_vertices for m in list_meshes(family)]
        assert len(sizes) == len(set(sizes))
        assert all(size > 0 for size in sizes)


def test_the_vertex_count_identifies_a_mesh_when_it_can():
    assert identify_mesh(32492).name == "fsLR:32k"
    assert identify_mesh(2562).name == "fsaverage4"
    assert identify_mesh(40962).name == "fsaverage6"


def test_an_ambiguous_vertex_count_is_refused_rather_than_guessed():
    """10242 is fs_LR 10k *and* fsaverage5, and they are not interchangeable.

    Guessing here would produce a map that looks completely normal and is in
    the wrong space, which is the worst failure mode available. So it stops.
    """
    with pytest.raises(MeshError, match="ambiguous"):
        identify_mesh(10242)
    with pytest.raises(MeshError, match="ambiguous"):
        identify_mesh(163842)


def test_a_preference_settles_an_ambiguous_count():
    assert identify_mesh(10242, prefer="fsLR:32k").name == "fsLR:10k"
    assert identify_mesh(10242, prefer="fsaverage6").name == "fsaverage5"
    assert identify_mesh(10242, prefer="fsLR:10k").name == "fsLR:10k"
    # A preference that is not one of the candidates still names a family.
    assert identify_mesh(163842, prefer="32k").name == "fsLR:164k"


def test_a_vertex_count_no_mesh_has_says_so():
    with pytest.raises(MeshError, match="no known mesh"):
        identify_mesh(12345)


def test_meshes_in_the_same_family_meet_on_their_own_sphere():
    fslr32 = parse_mesh("fsLR:32k")
    fslr10 = parse_mesh("fsLR:10k")
    assert common_registration(fslr32, fslr10) == "fsLR"


def test_crossing_families_meets_on_the_fsaverage_sphere():
    """Because the deformed spheres HCP ships go one way, not both."""
    assert common_registration(parse_mesh("fsLR:32k"), parse_mesh("fsaverage5")) == (
        "fsaverage"
    )
    assert common_registration(parse_mesh("fsaverage5"), parse_mesh("fsLR:32k")) == (
        "fsaverage"
    )


def test_the_59k_mesh_is_not_the_59k_layout():
    """A guard on a genuinely confusing collision, kept in a test on purpose."""
    from cifti_state.io.cifti import KNOWN_LAYOUTS

    assert KNOWN_MESHES["fsLR:59k"].n_vertices == 59292
    assert 59412 in KNOWN_LAYOUTS                       # the layout, not a mesh
    assert all(m.n_vertices != 59412 for m in KNOWN_MESHES.values())


# --------------------------------------------------------------------------- #
# locating templates
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def library() -> MeshLibrary:
    if not (EXAMPLES / "10k").is_dir():
        pytest.skip("the 10k and resample_fsaverage template packs are not present")
    return MeshLibrary(
        [EXAMPLES / "fs_LR_32k", EXAMPLES / "10k", EXAMPLES / "resample_fsaverage"]
    )


def test_the_bundled_packs_are_found_by_their_standard_names(library):
    """No filename is written down anywhere; the conventions do the work."""
    for name in ("fsLR:32k", "fsLR:10k", "fsaverage5"):
        assert library.files_for(name).can_resample, name


def test_the_right_sphere_is_picked_for_each_direction(library):
    """fs_LR to fs_LR uses the plain sphere; crossing uses the deformed one."""
    fslr32 = library.files_for("fsLR:32k")
    native = fslr32.sphere("left", "fsLR")
    deformed = fslr32.sphere("left", "fsaverage")
    assert native != deformed
    assert "deformed_to-fsaverage" in deformed.name
    assert "deformed" not in native.name


def test_a_missing_file_is_explained_rather_than_left_to_wb_command(library):
    files = library.files_for("fsaverage6")
    assert not files.can_resample
    message = files.explain_missing("sphere", "left")
    assert "fsaverage6" in message
    assert "Expected a file named like" in message
    assert "Looked in" in message


def test_superseded_copies_in_misc_are_never_picked_up(library):
    """``misc/`` holds ``old_`` and ``fix_`` spheres. Using one silently would
    resample through the wrong registration."""
    for name in ("fsLR:32k", "fsaverage5"):
        for role, sides in library.files_for(name).files.items():
            for path in sides.values():
                assert not path.name.startswith(("old_", "fix_"))
                assert path.parent.name != "misc"


def test_an_override_wins_over_the_conventions(tmp_path, library):
    fake = tmp_path / "my_sphere.surf.gii"
    fake.write_bytes(b"not really a sphere")
    custom = MeshLibrary(
        [EXAMPLES / "fs_LR_32k"],
        overrides={"fsLR:32k": {"sphere": {"left": str(fake), "right": str(fake)}}},
    )
    assert custom.files_for("fsLR:32k").sphere("left") == fake


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def test_a_direct_route_is_one_step(library):
    from cifti_state.mesh.resample import plan_route

    steps = plan_route("fsLR:32k", "fsLR:10k", library)
    assert len(steps) == 1
    assert steps[0].registration == "fsLR"


def test_crossing_families_is_still_one_step(library):
    from cifti_state.mesh.resample import plan_route

    steps = plan_route("fsLR:32k", "fsaverage5", library)
    assert len(steps) == 1
    assert steps[0].registration == "fsaverage"


def test_a_mesh_with_no_deformed_sphere_routes_through_one_that_has(library):
    """fs_LR 10k cannot reach fsaverage directly, so it goes via fs_LR 32k."""
    from cifti_state.mesh.resample import plan_route

    steps = plan_route("fsLR:10k", "fsaverage5", library)
    assert [s.target.name for s in steps] == ["fsLR:32k", "fsaverage5"]


def test_the_same_mesh_needs_no_steps(library):
    from cifti_state.mesh.resample import plan_route

    assert plan_route("fsLR:32k", "32k", library) == []


# --------------------------------------------------------------------------- #
# converting, for real
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    from cifti_state.config import load_settings

    if not CONFIG.exists():
        pytest.skip(f"no configuration at {CONFIG}")
    loaded = load_settings(CONFIG)
    if loaded.workbench.resolve().wb_command is None:
        pytest.skip("wb_command is not available")
    return loaded


@pytest.fixture(scope="module")
def smooth_map(tmp_path_factory, settings):
    """A dense fs_LR 32k map, smooth enough that resampling should preserve it."""
    from scipy.sparse import diags

    from cifti_state.io.cifti import make_dense_surface_template, save_like
    from cifti_state.io.neighbors import adjacency_from_surface
    from cifti_state.io.surface import load_surface

    rng = np.random.default_rng(0)
    hemis = []
    for hemi in ("left", "right"):
        surface = load_surface(settings.surface_for(hemi, "midthickness"))
        adjacency = adjacency_from_surface(surface.faces, surface.n_vertices)
        degree = np.asarray(adjacency.sum(1)).ravel()
        degree[degree == 0] = 1
        smoother = (diags(1.0 / degree) @ adjacency).tocsr()
        values = rng.normal(size=surface.n_vertices)
        for _ in range(60):
            values = 0.5 * values + 0.5 * (smoother @ values)
        hemis.append(values / values.std())

    path = tmp_path_factory.mktemp("mesh") / "smooth_32k.dscalar.nii"
    save_like(path, hemis[0], hemis[1], make_dense_surface_template(), map_name="smooth")
    return path


def _values(path):
    import nibabel as nib

    return np.asarray(nib.load(str(path)).get_fdata()).ravel()


def test_a_round_trip_through_a_coarser_mesh_comes_back(tmp_path, settings, smooth_map):
    """32k -> 10k -> 32k on a smooth field: the correlation has to be near 1.

    This is the test that would catch the wrong sphere, a missing area metric,
    or a hemisphere swap -- all of which produce output that looks like a brain
    map and correlates with the original at nothing like 0.999.
    """
    from cifti_state.mesh import resample_cifti

    down = tmp_path / "down_10k.dscalar.nii"
    back = tmp_path / "back_32k.dscalar.nii"
    resample_cifti(smooth_map, down, "fsLR:10k", settings)
    resample_cifti(down, back, "fsLR:32k", settings)

    original, returned = _values(smooth_map), _values(back)
    assert returned.shape == original.shape
    assert np.corrcoef(original, returned)[0, 1] > 0.99
    # Resampling must not rescale the data either.
    assert returned.std() == pytest.approx(original.std(), rel=0.05)


def test_the_output_lands_on_the_target_mesh(tmp_path, settings, smooth_map):
    from cifti_state.mesh import parse_mesh, resample_cifti

    for target in ("fsLR:10k", "fsaverage5"):
        out = tmp_path / f"{target.replace(':', '_')}.dscalar.nii"
        result = resample_cifti(smooth_map, out, target, settings)
        assert result.target == parse_mesh(target)
        assert _values(out).size == parse_mesh(target).n_both


def test_fs_lr_10k_and_fsaverage5_are_not_the_same_answer(tmp_path, settings, smooth_map):
    """Same vertex count, different space -- so the values must differ.

    If the deformed sphere were ignored the two would come out identical, and
    the mistake would be invisible in every summary statistic.
    """
    from cifti_state.mesh import resample_cifti

    a = tmp_path / "a.dscalar.nii"
    b = tmp_path / "b.dscalar.nii"
    resample_cifti(smooth_map, a, "fsLR:10k", settings)
    resample_cifti(smooth_map, b, "fsaverage5", settings)
    assert not np.allclose(_values(a), _values(b))


def test_the_medial_wall_is_carried_over_rather_than_filled_in(tmp_path, settings):
    """A 91k input has no wall; the 10k output should not have grown one."""
    from cifti_state.mesh import resample_cifti

    source = EXAMPLES / "maps" / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not source.exists():
        pytest.skip("the example maps are not present")
    out = tmp_path / "wall.dscalar.nii"
    result = resample_cifti(source, out, "fsLR:10k", settings, nan="mask")
    assert result.medial_wall == "excluded"
    assert result.used_roi
    # 20484 dense vertices minus the wall; nothing like the full mesh.
    assert 0.85 < _values(out).size / 20484 < 0.98


def test_masking_nan_keeps_a_thresholded_map_roughly_the_size_it_was(tmp_path, settings):
    """The point of ``nan='mask'``, measured.

    The bundled map stores NaN below threshold, so ~8% of cortex carries a
    value.  Letting the NaN interpolate erodes that to a fraction of itself;
    masking keeps it in the same neighbourhood as the input.
    """
    from cifti_state.mesh import resample_cifti

    source = EXAMPLES / "maps" / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not source.exists():
        pytest.skip("the example maps are not present")

    values = _values(source)
    before = np.isfinite(values).sum() / 59412

    fractions = {}
    for mode in ("propagate", "mask"):
        out = tmp_path / f"nan_{mode}.dscalar.nii"
        resample_cifti(source, out, "fsLR:10k", settings, nan=mode)
        after = _values(out)
        fractions[mode] = np.isfinite(after).sum() / after.size

    assert fractions["propagate"] < before / 3        # eroded badly
    assert 0.5 * before < fractions["mask"] < 1.5 * before


def test_the_commands_it_ran_are_reported(tmp_path, settings, smooth_map):
    """Every call is kept so a surprising result can be reproduced by hand."""
    from cifti_state.mesh import resample_cifti

    result = resample_cifti(
        smooth_map, tmp_path / "out.dscalar.nii", "fsLR:10k", settings
    )
    joined = "\n".join(result.commands)
    assert "-cifti-separate" in joined
    assert "-metric-resample" in joined
    assert "ADAP_BARY_AREA" in joined
    assert "-area-metrics" in joined          # never optional
    assert "-cifti-create-dense-scalar" in joined
    assert result.to_dict()["method"] == "ADAP_BARY_AREA"


def test_map_names_survive_the_conversion(tmp_path, settings, smooth_map):
    import nibabel as nib

    from cifti_state.mesh import resample_cifti

    out = tmp_path / "named.dscalar.nii"
    resample_cifti(smooth_map, out, "fsLR:10k", settings)
    axis = nib.load(str(out)).header.get_axis(0)
    assert list(axis.name) == ["smooth"]


def test_resampling_to_the_mesh_it_is_already_on_is_a_copy(tmp_path, settings, smooth_map):
    from cifti_state.mesh import resample_cifti

    out = tmp_path / "same.dscalar.nii"
    result = resample_cifti(smooth_map, out, "fsLR:32k", settings)
    assert result.steps == []
    assert np.array_equal(_values(out), _values(smooth_map))


def test_an_impossible_conversion_says_what_is_available(tmp_path, settings, smooth_map):
    from cifti_state.mesh import resample_cifti
    from cifti_state.mesh.spaces import MeshError

    with pytest.raises(MeshError) as excinfo:
        resample_cifti(smooth_map, tmp_path / "x.dscalar.nii", "fsaverage6", settings)
    assert "fsaverage6" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# parcellations and network masks
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def parcellation(tmp_path_factory, settings):
    """A three-network map on fs_LR 32k, built from the Glasser parcellation."""
    from cifti_state.io.atlas import load_atlas
    from cifti_state.io.cifti import make_dense_surface_template, save_like

    try:
        atlas = load_atlas("Glasser_2016", settings)
    except Exception:                                   # pragma: no cover
        pytest.skip("the Glasser atlas is not present")

    hemis = []
    for hemi in ("left", "right"):
        labels = np.asarray(atlas.hemi(hemi).labels)
        networks = np.zeros(labels.size)
        networks[(labels > 0) & (labels % 3 == 0)] = 1
        networks[(labels > 0) & (labels % 3 == 1)] = 2
        networks[(labels > 0) & (labels % 3 == 2)] = 5   # a gap in the numbering
        hemis.append(networks)

    path = tmp_path_factory.mktemp("parcel") / "networks_32k.dscalar.nii"
    save_like(path, hemis[0], hemis[1], make_dense_surface_template(),
              map_name="networks")
    return path


def test_a_label_map_is_recognised_without_being_told(tmp_path, settings, parcellation):
    from cifti_state.mesh import resample_cifti

    out = tmp_path / "networks_10k.dscalar.nii"
    result = resample_cifti(parcellation, out, "fsLR:10k", settings)
    assert any("parcellation" in w for w in result.warnings)


def test_resampling_a_parcellation_invents_no_new_labels(tmp_path, settings, parcellation):
    """The whole point: averaging networks 2 and 5 would produce a 3.5.

    The numbering here deliberately has a gap, so an interpolated value could
    not be mistaken for a label that was already there.
    """
    from cifti_state.mesh import resample_cifti

    before = set(np.unique(_values(parcellation)))
    out = tmp_path / "networks_10k.dscalar.nii"
    resample_cifti(parcellation, out, "fsLR:10k", settings)
    after = set(np.unique(_values(out)))
    assert after <= before
    assert after == {0.0, 1.0, 2.0, 5.0}


def test_forcing_the_continuous_path_does_average_them(tmp_path, settings, parcellation):
    """The escape hatch, and the proof that the two paths really differ."""
    from cifti_state.mesh import resample_cifti

    out = tmp_path / "networks_avg.dscalar.nii"
    resample_cifti(parcellation, out, "fsLR:10k", settings, discrete=False)
    values = _values(out)
    assert not np.array_equal(values, np.round(values))     # fractions appeared


def test_a_parcellation_keeps_its_share_of_the_surface(tmp_path, settings, parcellation):
    """Each network should come out about the same size, measured as area.

    Vertex counts are not comparable between meshes, so this compares the
    fraction of total surface area each network holds.
    """
    from cifti_state.io.surface import load_surface, vertex_areas
    from cifti_state.mesh import resample_cifti

    def areas(mesh):
        return np.concatenate([
            vertex_areas(surface.coords, surface.faces)
            for surface in (
                load_surface(settings.surface_for(h, "midthickness", mesh=mesh))
                for h in ("left", "right")
            )
        ])

    out = tmp_path / "networks_10k.dscalar.nii"
    resample_cifti(parcellation, out, "fsLR:10k", settings)

    a32, a10 = areas("fsLR:32k"), areas("fsLR:10k")
    before, after = _values(parcellation), _values(out)
    for network in (1.0, 2.0, 5.0):
        share_before = a32[before == network].sum() / a32.sum()
        share_after = a10[after == network].sum() / a10.sum()
        assert share_after == pytest.approx(share_before, abs=0.01)


def test_a_binary_mask_survives_a_round_trip(tmp_path, settings):
    """A network mask up to 32k and back should return almost exactly."""
    from cifti_state.io.cifti import make_dense_surface_template, save_like
    from cifti_state.mesh import resample_cifti

    # A compact blob, built from the mesh rather than from a shipped file.
    from cifti_state.io.neighbors import adjacency_from_surface
    from cifti_state.io.surface import load_surface

    surface = load_surface(settings.surface_for("left", "midthickness", mesh="fsLR:10k"))
    adjacency = adjacency_from_surface(surface.faces, surface.n_vertices)
    grown = np.zeros(surface.n_vertices, dtype=bool)
    grown[1000] = True
    for _ in range(12):
        grown |= (adjacency @ grown.astype(float)) > 0
    left = grown.astype(float)
    right = np.zeros(surface.n_vertices)

    path = tmp_path / "mask_10k.dscalar.nii"
    save_like(path, left, right,
              make_dense_surface_template(surface.n_vertices, surface.n_vertices),
              map_name="mask")

    up = tmp_path / "mask_32k.dscalar.nii"
    back = tmp_path / "mask_back.dscalar.nii"
    resample_cifti(path, up, "fsLR:32k", settings, source="fsLR:10k")
    resample_cifti(up, back, "fsLR:10k", settings)

    original, returned = _values(path) > 0.5, _values(back) > 0.5
    assert set(np.unique(_values(up))) <= {0.0, 1.0}     # stayed a mask
    dice = 2 * (original & returned).sum() / (original.sum() + returned.sum())
    assert dice > 0.99


# --------------------------------------------------------------------------- #
# the pipeline at another density
# --------------------------------------------------------------------------- #


def test_an_atlas_follows_the_data_to_another_mesh(tmp_path, settings):
    """The Glasser labels ship on 32k; annotation at 10k needs them there."""
    from cifti_state.io.atlas import load_atlas

    thirty_two = load_atlas("Glasser_2016", settings)
    ten = load_atlas("Glasser_2016", settings, mesh="fsLR:10k")

    assert ten.left.n_vertices == 10242
    assert ten.n_regions() == thirty_two.n_regions()   # 360, none lost
    assert set(ten.left.names.values()) == set(thirty_two.left.names.values())


def test_the_whole_pipeline_runs_at_10k(tmp_path, settings):
    """Detection, adjacency, geometry and annotation, all at the input's density."""
    from cifti_state.mesh import resample_cifti
    from cifti_state.pipeline import run_analysis
    from cifti_state.results import AnalysisSpec

    source = EXAMPLES / "maps" / "group_mean_thresh_fdr_E_C.dscalar.nii"
    if not source.exists():
        pytest.skip("the example maps are not present")
    small = tmp_path / "small.dscalar.nii"
    resample_cifti(source, small, "fsLR:10k", settings, nan="mask")

    result = run_analysis(
        AnalysisSpec(
            input_path=small, statistic="z", threshold_method="fixed",
            threshold_value=1.039, direction="positive", extent=5,
            atlas="Glasser_2016", output_dir=None,
        ),
        settings,
    )
    assert result.spec.mesh == "fsLR:10k"
    assert result.clusters.n_clusters > 0
    # Areas come from the 10k midthickness, so they must be real numbers.
    assert result.report["size_mm2"].sum() > 0
    assert result.report["peak_region"].notna().all()
