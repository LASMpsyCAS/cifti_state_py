"""Regression against the MATLAB pipeline's own output.

``Example_test`` holds two input/output pairs written by the original
``get_clusters_fsLR32k.m`` run:

    group_mean_thresh_fdr_E_C.dscalar.nii
    group_mean_thresh_fdr_E_C_cluster_extent20_thr1.039.dscalar.nii
    group_mean_thresh_fdr_E_D.dscalar.nii
    group_mean_thresh_fdr_E_D_cluster_extent20_thr1.09.dscalar.nii

In legacy mode the Python port must reproduce the cluster label maps
vertex for vertex, including the cluster numbering.
"""

from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

from cifti_state.core.cluster import find_clusters
from cifti_state.core.threshold import compute_threshold
from cifti_state.io.cifti import load_surface_stat_map
from cifti_state.pipeline import build_adjacency
from cifti_state.results import AnalysisSpec

CASES = [("E_C", 1.039, 32), ("E_D", 1.09, 76)]


@pytest.mark.requires_data
@pytest.mark.parametrize("tag, threshold_value, n_expected", CASES)
def test_cluster_labels_match_matlab(
    settings, examples_dir, tag, threshold_value, n_expected
):
    stat_path = examples_dir / f"group_mean_thresh_fdr_{tag}.dscalar.nii"
    reference_path = (
        examples_dir
        / f"group_mean_thresh_fdr_{tag}_cluster_extent20_thr{threshold_value}.dscalar.nii"
    )
    if not stat_path.exists() or not reference_path.exists():
        pytest.skip(f"reference pair for {tag} not present")

    spec = AnalysisSpec(
        input_path=stat_path,
        threshold_method="fixed",
        threshold_value=threshold_value,
        direction="positive",
        extent=20,
        legacy_mode=True,
    )
    stat_map = load_surface_stat_map(stat_path)
    adjacency = build_adjacency(stat_map, settings, spec)
    threshold = compute_threshold(
        stat_map.finite_values(), method="fixed", value=threshold_value
    )
    result = find_clusters(
        stat_map, adjacency, threshold, extent=20, legacy_mode=True
    )

    assert result.n_clusters == n_expected

    reference = nib.load(str(reference_path))
    ref_row = reference.get_fdata()[0]
    brain_axis = reference.header.get_axis(1)
    for name, sl, model in brain_axis.iter_structures():
        if name == "CIFTI_STRUCTURE_CORTEX_LEFT":
            mine = result.labels_left[np.asarray(model.vertex)]
        elif name == "CIFTI_STRUCTURE_CORTEX_RIGHT":
            mine = result.labels_right[np.asarray(model.vertex)]
        else:
            continue
        assert np.array_equal(mine, ref_row[sl].astype(int)), f"{tag}: {name} differs"


@pytest.mark.requires_data
@pytest.mark.parametrize("tag, threshold_value, n_expected", CASES)
def test_full_pipeline_writes_matching_cluster_map(
    settings, examples_dir, tmp_path, tag, threshold_value, n_expected
):
    from cifti_state.pipeline import run_analysis

    stat_path = examples_dir / f"group_mean_thresh_fdr_{tag}.dscalar.nii"
    reference_path = (
        examples_dir
        / f"group_mean_thresh_fdr_{tag}_cluster_extent20_thr{threshold_value}.dscalar.nii"
    )
    if not stat_path.exists() or not reference_path.exists():
        pytest.skip(f"reference pair for {tag} not present")

    spec = AnalysisSpec(
        input_path=stat_path,
        threshold_method="fixed",
        threshold_value=threshold_value,
        direction="positive",
        extent=20,
        legacy_mode=True,
        atlas=settings.defaults.atlas,
        output_dir=tmp_path,
        report_formats=("csv",),
    )
    result = run_analysis(spec, settings)

    assert result.n_clusters == n_expected
    assert len(result.peaks) == n_expected
    assert set(result.peaks["cluster_id"]) == set(range(1, n_expected + 1))
    assert result.annotations["cluster_id"].nunique() == n_expected

    written = nib.load(str(result.outputs["cluster_map"])).get_fdata()[0]
    expected = nib.load(str(reference_path)).get_fdata()[0]
    assert np.array_equal(written, expected)


@pytest.mark.requires_data
def test_peak_values_are_inside_their_clusters(settings, examples_dir):
    from cifti_state.core.peaks import cluster_peaks

    stat_path = examples_dir / "group_mean_thresh_fdr_E_D.dscalar.nii"
    if not stat_path.exists():
        pytest.skip("example not present")

    spec = AnalysisSpec(input_path=stat_path, threshold_value=1.09, extent=20)
    stat_map = load_surface_stat_map(stat_path)
    adjacency = build_adjacency(stat_map, settings, spec)
    threshold = compute_threshold(stat_map.finite_values(), method="fixed", value=1.09)
    clusters = find_clusters(stat_map, adjacency, threshold, extent=20, legacy_mode=True)
    peaks = cluster_peaks(stat_map, clusters)

    for row in peaks.itertuples():
        labels = clusters.labels(row.hemisphere)
        assert labels[row.peak_vertex] == row.cluster_id
        values = stat_map.hemi(row.hemisphere).values
        member_values = values[labels == row.cluster_id]
        assert row.peak_value == pytest.approx(np.nanmax(member_values))
        assert row.peak_value > threshold.positive
