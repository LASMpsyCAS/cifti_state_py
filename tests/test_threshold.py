"""Thresholding: BH correctness, tail handling, and the legacy port."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from cifti_state.core.threshold import (
    benjamini_hochberg,
    compute_threshold,
    fdr_threshold,
    suprathreshold_mask,
    to_pvalues,
)


def test_bh_matches_the_textbook_definition():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205])
    adjusted = benjamini_hochberg(p)
    n = p.size
    expected = np.minimum.accumulate(
        (np.sort(p) * n / np.arange(1, n + 1))[::-1]
    )[::-1]
    assert np.allclose(adjusted[np.argsort(p)], expected)
    assert np.all(np.diff(adjusted[np.argsort(p)]) >= -1e-12)   # monotone


def test_bh_is_identity_for_a_single_value():
    assert benjamini_hochberg(np.array([0.03]))[0] == pytest.approx(0.03)


def test_bh_handles_empty_input():
    assert benjamini_hochberg(np.array([])).size == 0


def test_legacy_fdr_reproduces_the_matlab_recipe():
    rng = np.random.default_rng(0)
    values = np.concatenate([rng.normal(0, 1, 5000), rng.normal(4.5, 0.5, 200)])

    result = fdr_threshold(values, q=0.05, direction="positive", legacy=True)

    # Recompute exactly as w_find_fdr_p_z.m does.
    pool = values[values > 0]
    p_adj = benjamini_hochberg(1.0 - stats.norm.cdf(pool))
    expected = pool[(p_adj < 0.05) & (p_adj != 0)].min()
    assert result.positive == pytest.approx(expected)
    assert result.negative is None


def test_t_statistic_requires_df():
    with pytest.raises(ValueError, match="requires df"):
        to_pvalues(np.array([2.0]), statistic="t")


def test_t_and_z_give_different_thresholds():
    rng = np.random.default_rng(1)
    values = np.abs(rng.normal(3.0, 1.0, 2000))
    as_z = fdr_threshold(values, q=0.05, statistic="z")
    as_t = fdr_threshold(values, q=0.05, statistic="t", df=20)
    assert as_z.positive is not None and as_t.positive is not None
    # A t map read as z is over-confident: its threshold comes out lower.
    assert as_z.positive < as_t.positive


def test_fixed_threshold_is_symmetric_for_two_sided():
    result = compute_threshold(
        np.array([-3.0, 0.0, 3.0]), method="fixed", value=2.0, direction="two_sided"
    )
    assert result.positive == 2.0 and result.negative == -2.0
    assert result.n_suprathreshold == 2


def test_percentile_threshold():
    values = np.arange(1, 101, dtype=float)
    result = compute_threshold(values, method="percentile", percentile=90)
    assert result.positive == pytest.approx(np.percentile(values, 90))


def test_suprathreshold_mask_excludes_nan():
    from cifti_state.core.threshold import ThresholdResult

    thr = ThresholdResult(1.0, None, "fixed", "positive", "z")
    values = np.array([2.0, np.nan, 0.5, np.inf])
    mask = suprathreshold_mask(values, thr)
    assert mask.tolist() == [True, False, False, False]


def test_fdr_returns_none_when_nothing_survives():
    rng = np.random.default_rng(2)
    values = rng.normal(0, 1, 500)
    result = fdr_threshold(values, q=1e-6, direction="positive")
    assert result.positive is None
    assert result.n_suprathreshold == 0
