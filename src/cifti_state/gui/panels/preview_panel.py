"""The surface preview: an interactive 3D view and a publication figure.

Two tabs over the same data:

**3D view** — a live ``pyvistaqt.QtInteractor``.  Drag to rotate, wheel to
zoom, right-drag to pan; it resizes with the window.  This is the working view.

**Figure** — the surfplot figure that :func:`cifti_state.viz.render.render_stat_map`
exports, so what you see here is exactly what gets saved.

The scene and the figure are both built on a worker thread; only handing the
finished objects to the widgets happens on the GUI thread, because VTK and Qt
both insist on it.
"""

from __future__ import annotations

from typing import Any, Optional

import matplotlib

# Agg, not QtAgg: surfplot builds its figure through pyplot, and that happens on
# a worker thread. A Qt-backed pyplot figure would try to create Qt timers off
# the main thread. The canvas below is a Qt canvas regardless of this setting --
# embedding does not go through pyplot.
matplotlib.use("Agg")

from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...logging_setup import get_logger  # noqa: E402
from ...viz.interactive import CAMERA_VIEWS, check_pyvista  # noqa: E402
from ...viz.layouts import LAYOUTS  # noqa: E402
from ..theme import PALETTE, matplotlib_rc  # noqa: E402
from ..widgets import compact_combo  # noqa: E402

log = get_logger(__name__)

__all__ = ["PreviewPanel"]

SURFACES = ("inflated", "very_inflated", "midthickness", "pial", "white", "flat")

_MODES = (
    ("Statistic in clusters", "masked"),
    ("Whole statistic map", "full"),
    ("Cluster colours", "clusters"),
)


class PreviewPanel(QWidget):
    """Controls, a live 3D view, and the exportable figure."""

    render_requested = Signal()          # rebuild whichever tab is showing
    save_figure_requested = Signal()
    screenshot_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        matplotlib.rcParams.update(matplotlib_rc())
        self._enabled = False
        self._busy = False
        self._plotter = None
        self._scene = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_toolbar())

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_three_d_tab(), "3D view")
        self.tabs.addTab(self._build_figure_tab(), "Figure")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self._sync_control_availability()

    # ------------------------------------------------------------ building -- #

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)

        self.surface = QComboBox()
        for kind in SURFACES:
            self.surface.addItem(kind.replace("_", " "), kind)
        row.addWidget(QLabel("Surface"))
        row.addWidget(self.surface)

        self.mode = QComboBox()
        for label, key in _MODES:
            self.mode.addItem(label, key)
        row.addWidget(QLabel("Show"))
        row.addWidget(self.mode)

        self.layout_box = QComboBox()
        for name, spec in LAYOUTS.items():
            self.layout_box.addItem(name.replace("_", " "), name)
            self.layout_box.setItemData(
                self.layout_box.count() - 1,
                spec.description,
                Qt.ItemDataRole.ToolTipRole,
            )
        index = self.layout_box.findData("grid_4")
        if index >= 0:
            self.layout_box.setCurrentIndex(index)
        self.layout_label = QLabel("Views")
        row.addWidget(self.layout_label)
        row.addWidget(self.layout_box)

        self.underlay = QComboBox()
        self.underlay.addItem("Sulci", "sulc")
        self.underlay.addItem("Curvature", "curvature")
        self.underlay.addItem("None", "none")
        self.underlay.setToolTip(
            "Greyscale folding pattern drawn under the statistic, so you can "
            "tell a gyral crown from a sulcal fundus."
        )
        compact_combo(self.underlay, visible_chars=8, popup_width=160)
        row.addWidget(QLabel("Underlay"))
        row.addWidget(self.underlay)

        self.outline = QCheckBox("Outlines")
        self.outline.setChecked(True)
        self.outline.setToolTip(
            "Draw cluster borders over the statistic map.\n"
            "Not applicable when showing cluster colours — those are filled."
        )
        row.addWidget(self.outline)

        self.split = QDoubleSpinBox()
        self.split.setRange(0.0, 120.0)
        self.split.setDecimals(0)
        self.split.setSingleStep(5.0)
        self.split.setValue(15.0)
        self.split.setSuffix(" mm")
        self.split.setToolTip(
            "Push the hemispheres apart in the 3D view so neither hides the "
            "other. 0 keeps their true anatomical positions."
        )
        row.addSpacing(6)
        self.split_label = QLabel("Split")
        row.addWidget(self.split_label)
        row.addWidget(self.split)

        row.addStretch(1)

        self.render_button = QPushButton("Render")
        self.render_button.setObjectName("Ghost")
        self.render_button.setEnabled(False)
        row.addWidget(self.render_button)

        self.save_button = QPushButton("Save…")
        self.save_button.setObjectName("IconButton")
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)

        self.render_button.clicked.connect(self.render_requested)
        self.save_button.clicked.connect(self._on_save)
        for widget in (self.surface, self.mode, self.layout_box, self.underlay):
            widget.currentIndexChanged.connect(self._auto_render)
        self.mode.currentIndexChanged.connect(self._sync_control_availability)
        self.outline.toggled.connect(self._auto_render)
        self.split.valueChanged.connect(self._auto_render)
        return bar

    def _build_three_d_tab(self) -> QWidget:
        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.three_d_stack = QStackedWidget(holder)
        self.three_d_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.three_d_placeholder = _placeholder(
            "Load a map and run Find clusters,\nthen press Render."
        )
        self.three_d_stack.addWidget(self.three_d_placeholder)

        backend = check_pyvista()
        if backend.get("pyvistaqt"):
            from pyvistaqt import QtInteractor

            self._plotter = QtInteractor(holder)
            self._plotter.set_background(PALETTE.surface)
            self._plotter.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self.three_d_stack.addWidget(self._plotter.interactor)
        else:
            self.three_d_placeholder.setText(
                "The interactive view needs pyvistaqt.\n\n"
                "    pip install pyvista pyvistaqt"
            )
            log.warning("pyvistaqt is not available; the 3D tab is disabled")

        column.addWidget(self.three_d_stack, 1)
        column.addWidget(self._build_camera_row(holder))
        return holder

    def _build_camera_row(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)

        hint = QLabel("Drag to rotate · wheel to zoom · right-drag to pan")
        hint.setObjectName("Muted")
        row.addWidget(hint)
        row.addStretch(1)

        self._camera_buttons: list[QPushButton] = []
        for key, label in CAMERA_VIEWS.items():
            button = QPushButton(label)
            button.setObjectName("IconButton")
            button.setToolTip(f"Look from the {label.lower()}")
            button.clicked.connect(lambda _=False, k=key: self.set_camera(k))
            row.addWidget(button)
            self._camera_buttons.append(button)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("IconButton")
        self.reset_button.clicked.connect(self.reset_camera)
        row.addWidget(self.reset_button)
        self._camera_buttons.append(self.reset_button)

        for button in self._camera_buttons:
            button.setEnabled(False)
        return bar

    def _build_figure_tab(self) -> QWidget:
        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.figure_stack = QStackedWidget(holder)
        self.figure_placeholder = _placeholder(
            "The publication figure is drawn on request.\nPress Render."
        )
        self.figure_stack.addWidget(self.figure_placeholder)

        canvas_holder = QWidget(holder)
        canvas_layout = QVBoxLayout(canvas_holder)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self.figure = Figure(figsize=(7.4, 5.2), dpi=100)
        self.figure.patch.set_facecolor(PALETTE.surface)
        self.canvas = FigureCanvasQTAgg(self.figure)
        canvas_layout.addWidget(self.canvas, 1)

        self.toolbar = NavigationToolbar2QT(self.canvas, canvas_holder)
        self.toolbar.setStyleSheet(f"background: transparent; color: {PALETTE.muted};")
        canvas_layout.addWidget(self.toolbar)

        self.figure_stack.addWidget(canvas_holder)
        column.addWidget(self.figure_stack, 1)
        return holder

    # ------------------------------------------------------------- reading -- #

    def active_view(self) -> str:
        """``"3d"`` or ``"figure"`` -- which tab the Render button targets."""
        return "3d" if self.tabs.currentIndex() == 0 else "figure"

    def surface_kind(self) -> str:
        return self.surface.currentData()

    def layout_name(self) -> str:
        return self.layout_box.currentData()

    def display_mode(self) -> str:
        return self.mode.currentData()

    def outlines_enabled(self) -> bool:
        """Only meaningful for the statistic modes; cluster colours are filled."""
        return self.display_mode() != "clusters" and self.outline.isChecked()

    def split_mm(self) -> float:
        return float(self.split.value())

    def underlay_name(self) -> str:
        return self.underlay.currentData()

    def has_three_d(self) -> bool:
        return self._plotter is not None

    # ------------------------------------------------------------- writing -- #

    def set_enabled(self, enabled: bool) -> None:
        """Whether rendering is possible at all (i.e. there are clusters)."""
        self._enabled = enabled
        self.render_button.setEnabled(enabled and not self._busy)

    def set_busy(self, busy: bool) -> None:
        # Track "can render" and "is rendering" separately. Reading the button's
        # own enabled state here would latch it off after the first render.
        self._busy = busy
        self.render_button.setEnabled(self._enabled and not busy)
        for widget in (self.surface, self.mode, self.layout_box, self.outline,
                       self.split, self.underlay):
            widget.setEnabled(not busy)
        if not busy:
            self._sync_control_availability()
        else:
            self.three_d_placeholder.setText("Building the scene…")
            self.figure_placeholder.setText("Rendering…")

    def show_scene(self, scene) -> bool:
        """Put a :class:`~cifti_state.viz.interactive.Scene` into the 3D view.

        Returns False (and explains itself in the tab) when VTK cannot get a
        render window -- a headless session, a remote desktop, or a graphics
        driver that will not give an OpenGL context. The Figure tab still works
        in that case, so this must degrade rather than raise.
        """
        if self._plotter is None:
            self.three_d_placeholder.setText(
                "The interactive view needs pyvistaqt.\n\n"
                "    pip install pyvista pyvistaqt\n\n"
                "The Figure tab works without it."
            )
            self.three_d_stack.setCurrentIndex(0)
            return False

        self._scene = scene
        try:
            self._plotter.clear()
            self._plotter.set_background(scene.background or PALETTE.surface)
            scene.add_to(self._plotter)
            self._plotter.render()
        except Exception as exc:
            log.warning("the 3D view could not render: %s", exc)
            self._scene = None
            self.three_d_placeholder.setText(
                "This machine could not give VTK an OpenGL context.\n\n"
                f"{exc}\n\n"
                "Remote Desktop and headless sessions often cannot; a local "
                "session with an up-to-date graphics driver can.\n"
                "The Figure tab does not need one."
            )
            self.three_d_stack.setCurrentIndex(0)
            for button in self._camera_buttons:
                button.setEnabled(False)
            self.save_button.setEnabled(False)
            return False

        self.three_d_stack.setCurrentIndex(1)
        for button in self._camera_buttons:
            button.setEnabled(True)
        self.save_button.setEnabled(True)
        return True

    def show_figure(self, figure: Figure) -> None:
        """Replace the figure tab's canvas with a freshly rendered figure."""
        old = self.canvas
        holder = old.parentWidget()
        holder_layout = holder.layout()

        self.figure = figure
        figure.patch.set_facecolor(PALETTE.surface)
        self.canvas = FigureCanvasQTAgg(figure)
        holder_layout.insertWidget(0, self.canvas, 1)

        new_toolbar = NavigationToolbar2QT(self.canvas, holder)
        new_toolbar.setStyleSheet(f"background: transparent; color: {PALETTE.muted};")
        holder_layout.replaceWidget(self.toolbar, new_toolbar)
        self.toolbar.deleteLater()
        self.toolbar = new_toolbar

        old.setParent(None)
        old.deleteLater()

        self.figure_stack.setCurrentIndex(1)
        self.save_button.setEnabled(True)
        self.canvas.draw_idle()

    def show_message(self, message: str) -> None:
        target = (
            self.three_d_placeholder if self.active_view() == "3d"
            else self.figure_placeholder
        )
        target.setText(message)
        (self.three_d_stack if self.active_view() == "3d"
         else self.figure_stack).setCurrentIndex(0)

    def clear(self) -> None:
        if self._plotter is not None:
            self._plotter.clear()
        self._scene = None
        self.three_d_placeholder.setText(
            "Load a map and run Find clusters,\nthen press Render."
        )
        self.figure_placeholder.setText(
            "The publication figure is drawn on request.\nPress Render."
        )
        self.three_d_stack.setCurrentIndex(0)
        self.figure_stack.setCurrentIndex(0)
        for button in self._camera_buttons:
            button.setEnabled(False)
        self.save_button.setEnabled(False)

    # -- camera ------------------------------------------------------------- #

    def set_camera(self, view: str) -> None:
        if self._plotter is None or self._scene is None:
            return
        try:
            self._scene.apply_view(self._plotter, view)
            self._plotter.render()
        except Exception as exc:  # pragma: no cover - renderer dependent
            log.warning("could not move the camera: %s", exc)

    def reset_camera(self) -> None:
        if self._plotter is None:
            return
        try:
            self._plotter.reset_camera()
            self._plotter.render()
        except Exception as exc:  # pragma: no cover - renderer dependent
            log.warning("could not reset the camera: %s", exc)

    def screenshot(self, path, *, scale: int = 2) -> Any:
        """Save the 3D view as an image at *scale* times the on-screen size."""
        if self._plotter is None:
            raise RuntimeError("the interactive view is not available")
        return self._plotter.screenshot(str(path), scale=scale)

    def close_plotter(self) -> None:
        """Release the VTK render window. Call this before the window closes."""
        if self._plotter is not None:
            try:
                self._plotter.close()
            except Exception:  # pragma: no cover - shutdown ordering
                pass
            self._plotter = None

    # ----------------------------------------------------------- internals -- #

    def _sync_control_availability(self, *_args) -> None:
        three_d = self.active_view() == "3d"
        is_clusters = self.display_mode() == "clusters"
        self.outline.setEnabled(not three_d and not is_clusters)
        self.layout_box.setEnabled(not three_d)
        self.layout_label.setEnabled(not three_d)
        self.split.setEnabled(three_d)
        self.split_label.setEnabled(three_d)
        self.save_button.setText("Screenshot…" if three_d else "Save figure…")

    def _on_tab_changed(self, *_args) -> None:
        self._sync_control_availability()
        showing = (
            self.three_d_stack.currentIndex() if self.active_view() == "3d"
            else self.figure_stack.currentIndex()
        )
        self.save_button.setEnabled(showing == 1)
        if showing == 0 and self._enabled and not self._busy:
            self.render_requested.emit()

    def _on_save(self) -> None:
        if self.active_view() == "3d":
            self.screenshot_requested.emit()
        else:
            self.save_figure_requested.emit()

    def _auto_render(self, *_args) -> None:
        """Re-render when a view control changes, once something is on screen."""
        if not self._enabled or self._busy:
            return
        showing = (
            self.three_d_stack.currentIndex() if self.active_view() == "3d"
            else self.figure_stack.currentIndex()
        )
        if showing == 1:
            self.render_requested.emit()


def _placeholder(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setObjectName("Muted")
    label.setStyleSheet(
        f"background: {PALETTE.surface}; border: 1px solid {PALETTE.border};"
        f"border-radius: 8px; color: {PALETTE.faint}; font-size: 13px;"
    )
    return label
