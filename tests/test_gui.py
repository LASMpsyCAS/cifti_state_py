"""Interface tests.

These run headless (``QT_QPA_PLATFORM=offscreen``) and are skipped when PySide6
is not installed.  They check the things that are easy to get wrong and hard to
notice: which thread a callback lands on, whether a control latches itself into
a disabled state, and whether the mask that comes out matches the cluster that
went in.
"""

from __future__ import annotations

import os
import threading

import numpy as np
import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cifti_state.gui.theme import PALETTE, stylesheet  # noqa: E402
from cifti_state.gui.workers import Job, JobRunner  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(app, predicate, timeout_s: float = 10.0) -> bool:
    import time

    end = time.time() + timeout_s
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# theme
# --------------------------------------------------------------------------- #


def test_stylesheet_has_no_unresolved_placeholders():
    css = stylesheet(PALETTE)
    assert "{" not in css.replace("{{", "").replace("}}", "") or "QWidget" in css
    for token in ("#HeaderBar", "#Card", "QTableView", "QPushButton#Primary"):
        assert token in css


def test_every_palette_colour_is_a_hex_triplet():
    import dataclasses
    import re

    for field in dataclasses.fields(PALETTE):
        value = getattr(PALETTE, field.name)
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", value), f"{field.name} = {value!r}"


def test_chevron_icon_is_written_to_disk():
    from pathlib import Path

    from cifti_state.gui.theme import _chevron

    path = Path(_chevron("#189E97"))
    assert path.exists() and path.read_text().startswith("<svg")


# --------------------------------------------------------------------------- #
# workers -- the thread-affinity contract
# --------------------------------------------------------------------------- #


def test_callbacks_run_on_the_gui_thread(qapp):
    """A direct signal connection would run these on the worker thread.

    Widgets may only be touched from the GUI thread, so this is the property the
    whole interface depends on.
    """
    main_thread = threading.current_thread()
    seen: dict[str, object] = {}

    def work(progress=None, cancel=None):
        seen["worker_thread"] = threading.current_thread()
        progress(0.5, "halfway")
        return 42

    runner = JobRunner()
    job = Job(work, description="test")

    runner.progressed.connect(
        lambda f, m: seen.setdefault("progress_thread", threading.current_thread())
    )
    runner.start(
        job,
        on_finished=lambda result: seen.update(
            result=result, finished_thread=threading.current_thread()
        ),
    )

    assert pump(qapp, lambda: "result" in seen and not runner.busy)
    assert seen["result"] == 42
    assert seen["worker_thread"] is not main_thread, "work did not leave the GUI thread"
    assert seen["finished_thread"] is main_thread
    assert seen["progress_thread"] is main_thread


def test_failure_is_reported_not_raised(qapp):
    seen: dict[str, object] = {}

    def work(progress=None, cancel=None):
        raise ValueError("deliberate")

    runner = JobRunner()
    runner.start(
        Job(work, description="failing"),
        on_failed=lambda message, detail: seen.update(message=message, detail=detail),
    )
    assert pump(qapp, lambda: "message" in seen and not runner.busy)
    assert seen["message"] == "deliberate"
    assert "ValueError" in seen["detail"]


def test_cancel_is_honoured(qapp):
    import time

    seen: dict[str, object] = {}

    def work(progress=None, cancel=None):
        from cifti_state.types import check_cancelled

        for _ in range(500):
            check_cancelled(cancel)
            time.sleep(0.005)
        return "should not finish"

    runner = JobRunner()
    runner.start(
        Job(work, description="long"),
        on_finished=lambda result: seen.update(result=result),
        on_cancelled=lambda: seen.update(cancelled=True),
    )
    pump(qapp, lambda: runner.busy, timeout_s=2)
    runner.cancel()
    assert pump(qapp, lambda: not runner.busy, timeout_s=10)
    assert seen.get("cancelled") is True
    assert "result" not in seen


def test_second_job_is_refused_while_one_runs(qapp):
    import time

    def slow(progress=None, cancel=None):
        time.sleep(0.3)
        return 1

    runner = JobRunner()
    assert runner.start(Job(slow, description="first")) is True
    assert runner.start(Job(slow, description="second")) is False
    assert pump(qapp, lambda: not runner.busy, timeout_s=10)


# --------------------------------------------------------------------------- #
# panels
# --------------------------------------------------------------------------- #


def test_render_button_does_not_latch_disabled(qapp):
    """set_busy(False) must restore the button, not read its own state back."""
    from cifti_state.gui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel()
    panel.set_enabled(True)
    assert panel.render_button.isEnabled()
    panel.set_busy(True)
    assert not panel.render_button.isEnabled()
    panel.set_busy(False)
    assert panel.render_button.isEnabled(), "the button latched itself off"


def test_outlines_do_not_apply_to_cluster_colour_mode(qapp):
    from cifti_state.gui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel()
    panel.outline.setChecked(True)
    index = panel.mode.findData("masked")
    panel.mode.setCurrentIndex(index)
    assert panel.outlines_enabled() is True

    panel.mode.setCurrentIndex(panel.mode.findData("clusters"))
    assert panel.outlines_enabled() is False
    assert not panel.outline.isEnabled()


def test_legacy_mode_forces_positive_direction(qapp):
    from cifti_state.gui.panels.cluster_panel import ClusterPanel

    panel = ClusterPanel()
    panel.legacy.setChecked(False)
    panel.direction.setCurrentIndex(panel.direction.findData("two_sided"))
    assert panel.direction_value() == "two_sided"

    panel.legacy.setChecked(True)
    assert panel.direction_value() == "positive"
    assert not panel.direction.isEnabled()


def test_table_selection_reports_cluster_ids(qapp):
    import pandas as pd

    from cifti_state.gui.panels.table_panel import TablePanel

    panel = TablePanel()
    panel.set_frame(
        pd.DataFrame({"cluster_id": [1, 2, 3], "hemi": ["L", "L", "R"]})
    )
    assert not panel.save_mask_button.isEnabled()

    panel.select_cluster(2)
    assert panel.selected_cluster_ids() == [2]
    assert panel.save_mask_button.isEnabled()


def test_table_filter_narrows_rows(qapp):
    import pandas as pd

    from cifti_state.gui.panels.table_panel import TablePanel

    panel = TablePanel()
    panel.set_frame(
        pd.DataFrame(
            {
                "cluster_id": [1, 2, 3],
                "peak_region": ["Area_V1", "Area_PF", "Area_V2"],
            }
        )
    )
    assert panel.model.rowCount() == 3
    panel.filter_box.setText("area_v")
    assert panel.model.rowCount() == 2
    panel.filter_box.setText("")
    assert panel.model.rowCount() == 3


# --------------------------------------------------------------------------- #
# masks
# --------------------------------------------------------------------------- #


def test_mask_matches_the_cluster_it_came_from():
    from cifti_state.core.cluster import ClusterInfo, ClusterParams, ClusterResult
    from cifti_state.core.mask import cluster_mask, clusters_mask

    labels_left = np.zeros(20, dtype=np.int32)
    labels_left[2:7] = 1
    labels_right = np.zeros(20, dtype=np.int32)
    labels_right[10:13] = 2
    clusters = ClusterResult(
        labels_left=labels_left,
        labels_right=labels_right,
        clusters=[
            ClusterInfo(1, "left", 1, 5, 2),
            ClusterInfo(2, "right", 1, 3, 10),
        ],
        params=ClusterParams(1.0, None, 2, "positive"),
    )

    left, right = cluster_mask(clusters, 1)
    assert int(left.sum()) == 5 and int(right.sum()) == 0
    assert np.array_equal(np.flatnonzero(left), [2, 3, 4, 5, 6])

    left, right = clusters_mask(clusters, [1, 2], label_values=True)
    assert set(np.unique(left)) == {0, 1}
    assert set(np.unique(right)) == {0, 2}

    with pytest.raises(KeyError):
        cluster_mask(clusters, 9)


# --------------------------------------------------------------------------- #
# fonts and encoding
# --------------------------------------------------------------------------- #


def test_fonts_resolve_to_something_present():
    from cifti_state.fonts import resolve_fonts

    choice = resolve_fonts(force=True)
    assert choice.ui and choice.ui != ""
    assert choice.mono and choice.mono != ""
    assert choice.source in ("qt", "matplotlib", "fallback")
    # the CSS stack always ends in a generic family so Qt has a last resort
    assert choice.ui_css.endswith('"sans-serif"')
    assert choice.mono_css.endswith('"monospace"')


def test_matplotlib_minus_sign_is_ascii():
    """U+2212 is missing from many CJK fonts and renders as a box."""
    import matplotlib

    from cifti_state.fonts import apply_to_matplotlib

    matplotlib.rcParams["axes.unicode_minus"] = True
    apply_to_matplotlib()
    assert matplotlib.rcParams["axes.unicode_minus"] is False


def test_resolved_family_reaches_matplotlib():
    import matplotlib

    from cifti_state.fonts import apply_to_matplotlib, resolve_fonts

    choice = resolve_fonts()
    apply_to_matplotlib(choice)
    assert matplotlib.rcParams["font.sans-serif"][0] == choice.ui


def test_stylesheet_uses_the_resolved_families():
    from cifti_state.fonts import resolve_fonts
    from cifti_state.gui.theme import stylesheet

    choice = resolve_fonts()
    css = stylesheet(fonts=choice)
    assert f'font-family: {choice.ui_css}' in css
    assert choice.mono in css
    # no hard-coded wish-list left behind
    assert '"Segoe UI", "Microsoft YaHei UI"' not in css


def test_configure_stdio_is_safe_to_call_twice():
    from cifti_state.fonts import configure_stdio

    configure_stdio()
    configure_stdio()


def test_font_report_has_the_diagnostic_rows():
    from cifti_state.fonts import font_report

    keys = {key for key, _ in font_report()}
    assert {"interface", "monospace", "CJK fallback", "stdout encoding"} <= keys


# --------------------------------------------------------------------------- #
# the interactive 3D scene
# --------------------------------------------------------------------------- #


def test_scene_builds_polydata_with_scalars(qapp, settings):
    pytest.importorskip("pyvista")
    from cifti_state.core.cluster import ClusterInfo, ClusterParams, ClusterResult
    from cifti_state.viz.interactive import build_scene

    n = 32492
    labels_left = np.zeros(n, dtype=np.int32)
    labels_left[100:400] = 1
    clusters = ClusterResult(
        labels_left=labels_left,
        labels_right=np.zeros(n, dtype=np.int32),
        clusters=[ClusterInfo(1, "left", 1, 300, 100)],
        params=ClusterParams(1.0, None, 20, "positive"),
    )
    scene = build_scene(None, settings, clusters=clusters, mode="clusters",
                        surface="inflated", split_mm=10)
    assert len(scene.layers) == 2
    assert scene.discrete is True
    layer = scene.layers[0]
    assert layer.n_vertices == n
    assert "value" in layer.mesh.point_data
    # vertices outside a cluster are NaN so they take the neutral mesh colour
    assert np.isnan(layer.scalars[0])
    assert int(np.isfinite(layer.scalars).sum()) == 300


def test_scene_rejects_a_mesh_that_does_not_match(settings):
    pytest.importorskip("pyvista")
    from cifti_state.core.cluster import ClusterInfo, ClusterParams, ClusterResult
    from cifti_state.viz.interactive import build_scene

    small = np.zeros(10, dtype=np.int32)
    small[2:6] = 1
    clusters = ClusterResult(
        labels_left=small,
        labels_right=small,
        clusters=[ClusterInfo(1, "left", 1, 4, 2)],
        params=ClusterParams(1.0, None, 2, "positive"),
    )
    with pytest.raises(ValueError, match="vertices"):
        build_scene(None, settings, clusters=clusters, mode="clusters",
                    surface="inflated")


def test_camera_views_are_all_known():
    pytest.importorskip("pyvista")
    from cifti_state.viz.interactive import CAMERA_VIEWS, _VIEW_VECTORS

    assert set(CAMERA_VIEWS) == set(_VIEW_VECTORS)
    for direction, up in _VIEW_VECTORS.values():
        assert len(direction) == 3 and len(up) == 3


def test_preview_panel_switches_controls_per_tab(qapp):
    from cifti_state.gui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel()
    panel.tabs.setCurrentIndex(0)                 # 3D
    assert panel.active_view() == "3d"
    assert panel.split.isEnabled()
    assert not panel.layout_box.isEnabled()       # view layouts are figure-only
    assert panel.save_button.text() == "Screenshot…"

    panel.tabs.setCurrentIndex(1)                 # figure
    assert panel.active_view() == "figure"
    assert not panel.split.isEnabled()
    assert panel.layout_box.isEnabled()
    assert panel.save_button.text() == "Save figure…"


# --------------------------------------------------------------------------- #
# typography
#
# A window that displays 0.1160 as "O.ıı ϗ OO" is broken in a way no assertion
# on the model can catch: the value is right, only the glyphs are wrong. These
# tests hold the two defences in place -- one size system, and a font whose
# digits have been checked.
# --------------------------------------------------------------------------- #


def test_stylesheet_sizes_follow_the_requested_pixel_size():
    small = stylesheet(PALETTE, font_size_px=11)
    large = stylesheet(PALETTE, font_size_px=16)
    assert "font-size: 11px" in small
    assert "font-size: 16px" in large
    # nothing may be pinned to the old hard-coded 13px ladder
    assert "font-size: 13px" not in large


def test_the_stylesheet_does_not_restyle_the_spin_buttons():
    """Styling a sub-control switches the whole widget to the stylesheet
    drawing path, where the text can be laid out against a different box than
    the one painted. Fusion's own arrows are safer."""
    css = stylesheet(PALETTE)
    assert "QSpinBox::up-button" not in css
    assert "QDoubleSpinBox::down-button" not in css
    assert "padding-right" in css          # but room is still reserved for them


def test_digits_are_sane_accepts_a_normal_family(qapp):
    from cifti_state.fonts import digits_are_sane, resolve_fonts

    choice = resolve_fonts(force=True)
    assert digits_are_sane(choice.ui)
    assert digits_are_sane(choice.mono)


def test_resolution_never_returns_a_variable_inter_by_default():
    """An installed Inter is skipped: Qt's DirectWrite backend picks the wrong
    named instance from some variable fonts, and digits are what break."""
    from cifti_state.fonts import UI_FAMILIES

    assert "Inter" not in UI_FAMILIES


def test_a_configured_family_overrides_the_search(qapp):
    from cifti_state.config import InterfaceSettings
    from cifti_state.fonts import resolve_fonts

    class Stub:
        interface = InterfaceSettings(ui_font="Carlito", mono_font="Courier New")

    choice = resolve_fonts(force=True, settings=Stub())
    assert choice.ui == "Carlito"
    assert choice.mono == "Courier New"
    resolve_fonts(force=True)                       # leave the cache clean


def test_every_spin_box_in_a_tree_gets_the_numeric_font(qapp):
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QVBoxLayout, QWidget

    from cifti_state.fonts import apply_numeric_fonts, resolve_fonts

    root = QWidget()
    column = QVBoxLayout(root)
    value = QDoubleSpinBox()
    value.setDecimals(4)
    value.setValue(0.1160)
    count = QSpinBox()
    count.setValue(10)
    for widget in (value, count):
        column.addWidget(widget)

    touched = apply_numeric_fonts(root)
    assert touched == 2

    mono = resolve_fonts().mono
    assert value.font().family() == mono
    assert count.font().family() == mono
    # C locale, so no regional digit substitution or decimal comma
    assert value.locale().language() == QLocale.Language.C
    assert value.text() == "0.1160"
    assert count.text() == "10"


def test_the_specimen_renders_to_a_png(qapp, tmp_path):
    from cifti_state.fonts import render_specimen

    path = render_specimen(tmp_path / "specimen.png", include_widgets=False)
    assert path.exists()
    assert path.stat().st_size > 2000
