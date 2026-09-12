"""Group statistics: the model, the smoothness, and both corrections.

The model is checked against scipy, which is the arbiter for a t-test.  The
random field machinery is checked against things that are true by construction
-- the Euler characteristic of a known mesh, a field smoothed to a known FWHM,
the ordering of P values -- and the permutation machinery against the one
identity that must hold exactly: the unpermuted rearrangement has to reproduce
the observed statistic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.sparse import csr_matrix

from cifti_state.stats.design import (
    DesignError,
    Participants,
    load_participants,
    one_sample_design,
    paired_design,
    two_sample_design,
)
from cifti_state.stats.glm import fit_glm, t_to_z, welch_two_sample
from cifti_state.stats.permutation import (
    _rearrangements,
    cluster_labels,
    permutation_test,
)
from cifti_state.stats.resels import build_topology, compute_resels, edge_roughness
from cifti_state.stats.rft import RandomField, ec_density


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def grid_faces(rows: int, cols: int) -> np.ndarray:
    """Triangulate a rows x cols lattice: two right triangles per square."""
    index = np.arange(rows * cols).reshape(rows, cols)
    faces = []
    for i in range(rows - 1):
        for j in range(cols - 1):
            a, b, c, d = index[i, j], index[i, j + 1], index[i + 1, j], index[i + 1, j + 1]
            faces += [[a, b, c], [b, d, c]]
    return np.array(faces, dtype=int)


def grid_coords(rows: int, cols: int) -> np.ndarray:
    i, j = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
    return np.column_stack([i.ravel(), j.ravel(), np.zeros(rows * cols)]).astype(float)


def smoothed_grid_field(rng, rows, cols, n, fwhm):
    """n independent fields on a lattice, smoothed to a known FWHM."""
    from scipy.ndimage import gaussian_filter

    sigma = fwhm / np.sqrt(8.0 * np.log(2.0))
    out = np.empty((n, rows * cols))
    for k in range(n):
        noise = rng.normal(size=(rows, cols))
        out[k] = gaussian_filter(noise, sigma, mode="wrap").ravel()
    return out


@pytest.fixture
def chain_graph():
    """A 400-vertex path graph and the field defined on it."""
    n = 400
    rows = list(range(n - 1)) + list(range(1, n))
    cols = list(range(1, n)) + list(range(n - 1))
    return csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))


@pytest.fixture
def study_table(tmp_path):
    """A participants table whose files exist, so paths resolve."""
    maps = tmp_path / "maps"
    maps.mkdir()
    lines = ["subject,group,condition,age,sex,file"]
    for i in range(1, 11):
        group = "patient" if i % 2 else "control"
        for condition in ("pre", "post"):
            name = f"sub-{i:02d}_{condition}.dscalar.nii"
            (maps / name).write_bytes(b"placeholder")
            lines.append(
                f"sub-{i:02d},{group},{condition},{20 + i},"
                f"{'M' if i % 3 else 'F'},maps/{name}"
            )
    path = tmp_path / "participants.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def only(participants: Participants, column: str, value: str) -> Participants:
    frame = participants.frame
    kept = frame[frame[column] == value].reset_index(drop=True)
    return Participants(frame=kept, file_column=participants.file_column)


# --------------------------------------------------------------------------- #
# the participants table and the designs
# --------------------------------------------------------------------------- #


def test_the_file_column_is_found_and_resolved_against_the_table(study_table):
    people = load_participants(study_table)
    assert len(people) == 20
    assert people.file_column == "file"
    assert all(path.is_absolute() for path in people.paths)
    assert people.missing() == []


def test_a_missing_file_is_refused_with_the_paths_it_looked_at(study_table, tmp_path):
    text = study_table.read_text(encoding="utf-8")
    study_table.write_text(text + "sub-99,control,pre,30,M,maps/absent.dscalar.nii\n",
                           encoding="utf-8")
    with pytest.raises(DesignError, match="do not exist"):
        load_participants(study_table)


def test_numeric_columns_become_numbers_and_labels_stay_strings(study_table):
    people = load_participants(study_table)
    assert pd.api.types.is_numeric_dtype(people.frame["age"])
    assert not pd.api.types.is_numeric_dtype(people.frame["group"])


def test_one_sample_centres_its_covariates(study_table):
    people = only(load_participants(study_table), "condition", "post")
    design = one_sample_design(people, covariates=["age"])
    assert design.names == ["intercept", "age"]
    assert design.matrix[:, 1].mean() == pytest.approx(0.0)
    assert list(design.contrast) == [1.0, 0.0]
    assert design.dof == len(people) - 2


def test_two_sample_says_which_group_is_which(study_table):
    people = only(load_participants(study_table), "condition", "post")
    design = two_sample_design(people, "group")
    assert design.groups == ("control", "patient")
    assert design.contrast_name == "patient - control"
    # the tested column is the dummy for the *second* group
    assert design.matrix[:, 1].sum() == 5


def test_two_sample_refuses_a_column_that_is_not_two_groups(study_table):
    people = load_participants(study_table)
    with pytest.raises(DesignError, match="exactly two"):
        two_sample_design(people, "subject")


def test_a_rank_deficient_model_is_refused_rather_than_fitted(study_table):
    people = only(load_participants(study_table), "condition", "post")
    frame = people.frame.copy()
    frame["copy_of_group"] = frame["group"]
    doubled = Participants(frame=frame, file_column=people.file_column)
    with pytest.raises(DesignError, match="rank deficient"):
        two_sample_design(doubled, "group", covariates=["copy_of_group"])


def test_paired_pairs_on_subject_and_drops_the_incomplete(study_table, tmp_path):
    people = load_participants(study_table)
    design = paired_design(people, "condition", "subject")
    assert design.kind == "paired"
    assert len(design.paths) == 10
    assert design.subtract_paths is not None
    assert len(design.subtract_paths) == 10
    # every pair must be the same subject
    for primary, baseline in zip(design.paths, design.subtract_paths):
        assert primary.name.split("_")[0] == baseline.name.split("_")[0]


def test_paired_refuses_a_subject_with_two_rows_for_one_condition(study_table):
    text = study_table.read_text(encoding="utf-8")
    study_table.write_text(text + "sub-01,patient,pre,21,M,maps/sub-01_pre.dscalar.nii\n",
                           encoding="utf-8")
    people = load_participants(study_table)
    with pytest.raises(DesignError, match="more than one row"):
        paired_design(people, "condition", "subject")


# --------------------------------------------------------------------------- #
# the model, against scipy
# --------------------------------------------------------------------------- #


def test_one_sample_t_matches_scipy():
    rng = np.random.default_rng(0)
    data = rng.normal(0.3, 1.2, size=(17, 300))
    fit = fit_glm(data, np.ones((17, 1)), np.array([1.0]))
    reference = stats.ttest_1samp(data, 0.0, axis=0)
    assert np.allclose(fit.t, reference.statistic)
    assert np.allclose(fit.pvalues(direction="two_sided"), reference.pvalue)
    assert fit.dof == 16


def test_two_sample_t_matches_scipy():
    rng = np.random.default_rng(1)
    data = np.vstack([rng.normal(0, 1, (12, 300)), rng.normal(0.5, 1, (15, 300))])
    group = np.r_[np.zeros(12), np.ones(15)]
    fit = fit_glm(data, np.column_stack([np.ones(27), group]), np.array([0.0, 1.0]))
    reference = stats.ttest_ind(data[12:], data[:12], axis=0, equal_var=True)
    assert np.allclose(fit.t, reference.statistic)
    assert fit.dof == 25


def test_paired_t_matches_scipy():
    rng = np.random.default_rng(2)
    before = rng.normal(0, 1, (14, 300))
    after = before + rng.normal(0.4, 0.6, (14, 300))
    fit = fit_glm(after - before, np.ones((14, 1)), np.array([1.0]))
    reference = stats.ttest_rel(after, before, axis=0)
    assert np.allclose(fit.t, reference.statistic)


def test_welch_matches_scipy_including_its_degrees_of_freedom():
    rng = np.random.default_rng(3)
    data = np.vstack([rng.normal(0, 1, (12, 300)), rng.normal(0.5, 3, (15, 300))])
    second = np.r_[np.zeros(12, bool), np.ones(15, bool)]
    fit = welch_two_sample(data, second)
    reference = stats.ttest_ind(data[12:], data[:12], axis=0, equal_var=False)
    assert np.allclose(fit.t, reference.statistic)
    assert np.allclose(fit.dof_map, reference.df)
    assert np.allclose(fit.pvalues(direction="two_sided"), reference.pvalue)


def test_a_covariate_changes_the_answer_the_way_least_squares_says():
    rng = np.random.default_rng(4)
    n, v = 30, 200
    covariate = rng.normal(size=n)
    group = np.r_[np.zeros(15), np.ones(15)]
    data = rng.normal(size=(n, v)) + 2.0 * covariate[:, None]
    design = np.column_stack([np.ones(n), group, covariate - covariate.mean()])
    contrast = np.array([0.0, 1.0, 0.0])
    fit = fit_glm(data, design, contrast)

    beta, *_ = np.linalg.lstsq(design, data, rcond=None)
    residual = data - design @ beta
    sigma2 = (residual ** 2).sum(0) / (n - 3)
    factor = contrast @ np.linalg.pinv(design.T @ design) @ contrast
    assert np.allclose(fit.t, (contrast @ beta) / np.sqrt(sigma2 * factor))


def test_t_to_z_preserves_the_p_value():
    rng = np.random.default_rng(5)
    data = rng.normal(0.2, 1.0, size=(9, 500))
    fit = fit_glm(data, np.ones((9, 1)), np.array([1.0]))
    converted = t_to_z(fit)
    assert converted.statistic == "z"
    assert np.isfinite(converted.t).all()
    assert np.allclose(
        fit.pvalues(direction="positive"),
        converted.pvalues(direction="positive"),
        atol=1e-12,
    )


def test_a_vertex_with_no_variance_is_dropped_rather_than_made_infinite():
    data = np.ones((10, 5))
    data[:, 1] = np.arange(10)
    fit = fit_glm(data, np.ones((10, 1)), np.array([1.0]))
    assert not fit.mask[0]              # constant column -> no residual variance
    assert fit.t[0] == 0.0
    assert np.isfinite(fit.t).all()


def test_an_inestimable_contrast_is_refused():
    data = np.random.default_rng(6).normal(size=(10, 20))
    design = np.ones((10, 1))
    with pytest.raises(ValueError, match="not estimable"):
        fit_glm(data, design, np.array([0.0]))


# --------------------------------------------------------------------------- #
# smoothness
# --------------------------------------------------------------------------- #


def test_the_euler_characteristic_of_a_disc_is_one():
    faces = grid_faces(12, 14)
    topology = build_topology(faces, n_vertices=12 * 14)
    residuals = np.random.default_rng(7).normal(size=(8, 12 * 14))
    resl = edge_roughness(residuals, topology)
    estimate = compute_resels(resl, topology, np.ones(12 * 14, dtype=bool))
    assert estimate.euler_characteristic == pytest.approx(1.0)


def test_two_hemispheres_give_an_euler_characteristic_of_two():
    faces = grid_faces(10, 10)
    topology = build_topology(faces, faces, n_left=100, n_vertices=200)
    residuals = np.random.default_rng(8).normal(size=(6, 200))
    resl = edge_roughness(residuals, topology)
    estimate = compute_resels(resl, topology, np.ones(200, dtype=bool))
    assert estimate.euler_characteristic == pytest.approx(2.0)


def test_resels_per_vertex_sum_to_the_total_area_in_resels():
    faces = grid_faces(15, 15)
    topology = build_topology(faces, n_vertices=225)
    residuals = np.random.default_rng(9).normal(size=(10, 225))
    resl = edge_roughness(residuals, topology)
    mask = np.ones(225, dtype=bool)
    mask[:12] = False
    estimate = compute_resels(resl, topology, mask)
    assert estimate.resels_per_vertex.sum() == pytest.approx(estimate.resels[2])
    assert estimate.resels_per_vertex[~mask].sum() == 0.0


def test_a_smoother_field_yields_fewer_resels():
    rng = np.random.default_rng(10)
    rows = cols = 60
    faces = grid_faces(rows, cols)
    topology = build_topology(faces, n_vertices=rows * cols)
    mask = np.ones(rows * cols, dtype=bool)

    areas = []
    for fwhm in (2.0, 4.0, 8.0):
        residuals = smoothed_grid_field(rng, rows, cols, 24, fwhm)
        residuals -= residuals.mean(axis=0)
        resl = edge_roughness(residuals, topology)
        areas.append(compute_resels(resl, topology, mask).resels[2])
    assert areas[0] > areas[1] > areas[2]


def test_the_estimated_fwhm_recovers_a_known_smoothing_kernel():
    """The point of the whole exercise: does the number mean what it says?"""
    rng = np.random.default_rng(11)
    rows = cols = 70
    faces = grid_faces(rows, cols)
    topology = build_topology(faces, n_vertices=rows * cols)
    # Stay away from the boundary, where the wrapped smoothing and the mesh
    # edge disagree about what is going on.
    mask = np.zeros((rows, cols), dtype=bool)
    mask[10:-10, 10:-10] = True
    mask = mask.ravel()

    for true_fwhm in (4.0, 6.0):
        residuals = smoothed_grid_field(rng, rows, cols, 40, true_fwhm)
        residuals -= residuals.mean(axis=0)
        resl = edge_roughness(residuals, topology)
        estimate = compute_resels(resl, topology, mask)
        assert estimate.fwhm == pytest.approx(true_fwhm, rel=0.15), (
            f"estimated {estimate.fwhm:.2f} for a true FWHM of {true_fwhm}"
        )


# --------------------------------------------------------------------------- #
# random field theory
# --------------------------------------------------------------------------- #


def test_the_zero_dimensional_density_is_the_tail_probability():
    assert ec_density(2.0, 0, statistic="z") == pytest.approx(stats.norm.sf(2.0))
    assert ec_density(2.0, 0, statistic="t", dof=12) == pytest.approx(
        stats.t.sf(2.0, 12)
    )


def test_a_t_field_approaches_a_gaussian_one_as_the_dof_grow():
    for dimension in (0, 1, 2):
        gaussian = float(ec_density(3.0, dimension, statistic="z"))
        heavy = float(ec_density(3.0, dimension, statistic="t", dof=100000))
        assert heavy == pytest.approx(gaussian, rel=1e-3)


def test_peak_p_values_fall_as_the_threshold_rises():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=25, statistic="t")
    values = field.peak_pvalue(np.array([2.0, 3.0, 4.0, 5.0, 6.0, 7.0]))
    assert np.all(np.diff(values) <= 0)            # never rises
    assert np.all(np.diff(values[values < 1.0]) < 0)   # strictly falls once below 1
    assert values[0] == pytest.approx(1.0)
    assert values[-1] < 0.05


def test_the_peak_threshold_round_trips_through_its_own_p_value():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=25, statistic="t")
    for alpha in (0.05, 0.01, 0.001):
        assert float(field.peak_pvalue(field.peak_threshold(alpha))) == pytest.approx(
            alpha, rel=1e-6
        )


def test_peak_p_never_exceeds_the_bonferroni_bound():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=1000,
                        dof=20, statistic="t")
    for u in (1.0, 1.5, 2.0, 3.0):
        bound = 1000 * float(ec_density(u, 0, statistic="t", dof=20))
        assert float(field.peak_pvalue(u)) <= min(bound, 1.0) + 1e-12


def test_cluster_p_values_fall_as_the_cluster_grows():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=25, statistic="t")
    values = field.cluster_pvalue(np.array([0.1, 0.5, 1.0, 2.0, 5.0]), u=3.1)
    assert np.all(np.diff(values) < 0)


def test_the_exact_cluster_distribution_is_stricter_than_the_gaussian_one():
    """The whole reason it exists: the closed form is liberal for a t field.

    With the variance estimated rather than known, a null map throws up big
    clusters more often than the Gaussian formula expects, so its P values come
    out too small. The gap is large -- orders of magnitude in the tail -- which
    is why the exact distribution is the default.
    """
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=20, statistic="t")
    extents = np.array([0.5, 1.0, 2.0, 4.0])
    exact = field.cluster_pvalue(extents, u=3.1, method="exact")
    gaussian = field.cluster_pvalue(extents, u=3.1, method="gaussian")
    assert np.all(exact >= gaussian)
    assert exact[-1] > 10 * gaussian[-1]


def test_a_gaussian_field_uses_the_closed_form_either_way():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=np.inf, statistic="z")
    extents = np.array([0.5, 2.0])
    assert np.allclose(
        field.cluster_pvalue(extents, u=3.1),
        field.cluster_pvalue(extents, u=3.1, method="gaussian"),
    )


def test_the_cluster_extent_threshold_round_trips():
    field = RandomField(resels=np.array([2.0, 30.0, 700.0]), n_vertices=59412,
                        dof=25, statistic="t")
    for alpha in (0.05, 0.01):
        extent = field.cluster_extent_threshold(alpha, u=3.1)
        assert float(field.cluster_pvalue(extent, u=3.1)) == pytest.approx(
            alpha, rel=1e-4
        )


# --------------------------------------------------------------------------- #
# permutation
# --------------------------------------------------------------------------- #


def test_the_unpermuted_rearrangement_reproduces_the_observed_statistic(chain_graph):
    rng = np.random.default_rng(12)
    data = rng.normal(size=(12, 400))
    design = np.ones((12, 1))
    contrast = np.array([1.0])
    fit = fit_glm(data, design, contrast)
    result = permutation_test(data, design, contrast, chain_graph,
                              n_permutations=24, cluster_forming=1.5, seed=0)
    assert result.max_statistic[0] == pytest.approx(np.abs(fit.t).max())
    assert result.scheme == "sign_flip"


def test_freedman_lane_keeps_the_covariates_in_place(chain_graph):
    rng = np.random.default_rng(13)
    n = 20
    covariate = rng.normal(size=n)
    group = np.r_[np.zeros(10), np.ones(10)]
    data = rng.normal(size=(n, 400)) + 3.0 * covariate[:, None]
    design = np.column_stack([np.ones(n), group, covariate - covariate.mean()])
    contrast = np.array([0.0, 1.0, 0.0])
    fit = fit_glm(data, design, contrast)
    result = permutation_test(data, design, contrast, chain_graph,
                              n_permutations=32, cluster_forming=1.5, seed=0)
    assert result.max_statistic[0] == pytest.approx(np.abs(fit.t).max())
    assert result.scheme == "shuffle"
    assert any("Freedman-Lane" in note for note in result.notes)


def test_p_values_are_never_zero(chain_graph):
    """An effect that beats every rearrangement still gets P = 1/n, not 0.

    The observed arrangement is itself one of the rearrangements -- the first
    one -- so the count of null values at least as extreme can never fall
    below one.  That is what puts the floor under the P value, and it is the
    reason PALM divides by ``n`` rather than by ``n + 1``.
    """
    rng = np.random.default_rng(14)
    data = rng.normal(5.0, 0.1, size=(10, 400))      # an overwhelming effect
    design = np.ones((10, 1))
    result = permutation_test(data, design, np.array([1.0]), chain_graph,
                              n_permutations=64, cluster_forming=2.0, seed=0)
    observed = float(result.max_statistic[0])        # the unpermuted fit
    assert observed == result.max_statistic.max()    # nothing beats the truth
    assert float(result.statistic_pvalue(observed)) == pytest.approx(
        result.resolution()
    )
    assert float(result.statistic_pvalue(observed)) > 0

    # The observed statistic reaches this function from the fit, while the
    # null's first entry was computed inside the loop, so the two agree only
    # to the last few bits. A P value must not fall off its floor over that.
    nudged = observed * (1.0 + 1e-14)
    assert float(result.statistic_pvalue(nudged)) == pytest.approx(
        result.resolution()
    )


def test_sign_flipping_is_exhaustive_when_it_can_be():
    flips, exhaustive = _rearrangements(8, "sign_flip", 5000, 0)
    assert exhaustive and len(flips) == 2 ** 8
    assert np.allclose(flips[0], 1.0)              # the identity leads


def test_group_shuffling_is_exhaustive_when_it_can_be():
    groups = np.r_[np.zeros(6, int), np.ones(7, int)]
    orders, exhaustive = _rearrangements(13, "shuffle", 5000, 0, groups=groups)
    from math import comb

    assert exhaustive and len(orders) == comb(13, 7)
    assert np.array_equal(groups[orders[0]], groups)


def test_large_designs_fall_back_to_sampling():
    groups = np.r_[np.zeros(20, int), np.ones(20, int)]
    orders, exhaustive = _rearrangements(40, "shuffle", 200, 0, groups=groups)
    assert not exhaustive and len(orders) == 200


def test_cluster_labels_finds_the_runs_of_a_chain(chain_graph):
    values = np.zeros(400)
    values[10:25] = 5.0          # 15 vertices
    values[100:104] = 5.0        # 4 vertices
    sizes, masses = cluster_labels(values, chain_graph, 1.0, extent=1)
    assert sorted(sizes.tolist()) == [4.0, 15.0]
    assert sorted(masses.tolist()) == [20.0, 75.0]
    sizes, _ = cluster_labels(values, chain_graph, 1.0, extent=10)
    assert sizes.tolist() == [15.0]


def test_correcting_at_a_different_threshold_than_the_permutation_is_refused():
    """The two must describe the same experiment, or the P values are fiction."""
    from cifti_state.core.cluster import ClusterParams, ClusterResult
    from cifti_state.stats.permutation import PermutationResult
    from cifti_state.stats.ttest import GroupAnalysis

    permutation = PermutationResult(
        max_statistic=np.zeros(10), max_extent=np.zeros(10), max_mass=np.zeros(10),
        n_permutations=10, exhaustive=False, scheme="sign_flip",
        direction="two_sided", cluster_forming=3.1, extent=1,
    )
    analysis = GroupAnalysis.__new__(GroupAnalysis)
    analysis.permutation = permutation
    clusters = ClusterResult(
        labels_left=np.zeros(4, dtype=int), labels_right=np.zeros(4, dtype=int),
        clusters=[],
        params=ClusterParams(threshold_positive=2.3, threshold_negative=None,
                             extent=1, direction="positive"),
        source_name="x",
    )
    with pytest.raises(ValueError, match="cluster-forming threshold"):
        GroupAnalysis.correct_clusters(analysis, clusters)
