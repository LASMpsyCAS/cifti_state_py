"""The greyscale sulcal underlay.

The thing worth testing here is the orientation: which sign of the underlay
counts as a sulcus.  Getting it backwards produces a picture that looks fine
at a glance -- a folded brain, plausibly shaded -- but has every gyrus painted
as a sulcus.  Nothing in the numbers gives it away, so the automatic
orientation is checked against a mesh whose geometry is known.
"""

from __future__ import annotations

import numpy as np
import pytest

from cifti_state.viz.underlay import (
    DEFAULT_DARK,
    DEFAULT_LIGHT,
    _resolve_orientation,
    greyscale,
)


# --------------------------------------------------------------------------- #
# greyscale
# --------------------------------------------------------------------------- #


def test_binary_puts_sulci_dark_for_the_fs_lr_convention():
    # fs_LR: negative is sulcal.
    values = np.array([-2.0, -0.5, 0.5, 2.0])
    grey = greyscale(values, style="binary", sulcal_sign=-1)
    assert grey[0] == pytest.approx(DEFAULT_DARK)
    assert grey[1] == pytest.approx(DEFAULT_DARK)
    assert grey[2] == pytest.approx(DEFAULT_LIGHT)
    assert grey[3] == pytest.approx(DEFAULT_LIGHT)


def test_the_freesurfer_convention_is_the_mirror_image():
    values = np.array([-2.0, 2.0])
    fs_lr = greyscale(values, style="binary", sulcal_sign=-1)
    freesurfer = greyscale(values, style="binary", sulcal_sign=+1)
    assert list(fs_lr) == list(freesurfer[::-1])


def test_continuous_stays_within_the_grey_range_and_keeps_the_ordering():
    values = np.linspace(-3, 3, 200)
    grey = greyscale(values, style="continuous", sulcal_sign=-1)
    assert grey.min() >= DEFAULT_DARK - 1e-9
    assert grey.max() <= DEFAULT_LIGHT + 1e-9
    # most negative (deepest sulcus) is darkest, most positive is lightest
    assert grey[0] < grey[-1]
    assert np.all(np.diff(grey) >= -1e-12)


def test_non_finite_values_get_the_mid_grey_rather_than_a_hole():
    grey = greyscale(np.array([np.nan, np.inf, 1.0]), style="binary")
    mid = (DEFAULT_DARK + DEFAULT_LIGHT) / 2
    assert grey[0] == pytest.approx(mid)
    assert grey[1] == pytest.approx(mid)


def test_an_unknown_style_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="binary or continuous"):
        greyscale(np.zeros(4), style="rainbow")


# --------------------------------------------------------------------------- #
# orientation
# --------------------------------------------------------------------------- #


def test_an_explicit_orientation_is_honoured_without_touching_the_surface():
    # settings is None, so anything that tried to measure would raise.
    assert _resolve_orientation("positive", None, np.zeros(4), "sulc") == 1
    assert _resolve_orientation("negative", None, np.zeros(4), "sulc") == -1


def test_auto_falls_back_to_negative_when_it_cannot_measure():
    class Broken:
        @property
        def resources(self):
            raise RuntimeError("no surfaces configured")

    assert _resolve_orientation("auto", Broken(), np.zeros(4), "sulc") == -1


def test_auto_measures_the_sign_against_the_real_surface(settings):
    """On the fs_LR template pack the answer must come out negative.

    Measured directly: the correlation between fs_LR.32k.LR.sulc and vertex
    concavity on the midthickness mesh is about -0.63.
    """
    from cifti_state.io.cifti import load_surface_stat_map
    from cifti_state.viz.underlay import _left_concavity

    try:
        path = settings.resources.underlay_path("sulc")
    except Exception:
        pytest.skip("no sulc underlay configured")
    if not path.exists():
        pytest.skip("the sulc underlay file is not available here")

    values = load_surface_stat_map(str(path), statistic="other", fill=0.0)
    left = np.asarray(values.left.values, dtype=float)

    concavity = _left_concavity(settings)
    assert concavity is not None
    assert concavity.size == left.size

    assert _resolve_orientation("auto", settings, left, "sulc") == -1
