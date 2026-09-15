"""A desktop window for tuning a network figure: controls left, brain right.

The bare PyVista window in :mod:`.interactive` puts its sliders *inside* the
3D scene, which works but gives you three dials and nothing else -- no way to
toggle labels, change a colour, or switch the edge rule without going back to
the command line.  This is the real thing: every option the renderer has, as a
widget, beside a view you can drag.

Two decisions worth stating, because both could reasonably have gone the other
way:

**Refresh is a button, not a keystroke.** Every control could redraw on change,
and for the cheap ones it would feel better. But rebuilding a dense network is
a second or two, and a panel that redraws on every spin-box click while you are
setting four numbers spends most of its time drawing states you did not want to
see. So changes accumulate, the button lights up to say so, and one click
applies them. ``Auto`` turns the other behaviour on for when you are nudging a
single value.

**The command line is always visible.** Whatever you tune here is shown at the
bottom as the ``cifti-state network`` line that reproduces it, and copying that
line is how a figure leaves this window for a script or a methods section. A
window that can only be operated by hand makes every figure it produces
unreproducible.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import Settings
from ..logging_setup import get_logger
from .data import NetworkData
from .interactive import command_line_for
from .scene import CAMERA_VIEWS, build_network_scene, resolve_view
from .style import EDGE_COLOR_MODES, EDGE_DIRECTION_MODES, NetworkStyle
from .threshold import max_positive_edge_count, pair_maxima

log = get_logger(__name__)

__all__ = ["NetworkWindow", "open_window", "QtMissing"]

_SURFACES = ("midthickness", "inflated", "very_inflated", "white", "pial")


class QtMissing(RuntimeError):
    """PySide6 or pyvistaqt is not installed."""


def _require_qt() -> None:
    """Check both optional dependencies are here, and say which is missing."""
    import importlib

    for module, hint in (
        ("PySide6.QtWidgets", 'pip install -e ".[gui]"'),
        ("pyvistaqt", "pip install pyvista pyvistaqt"),
    ):
        try:
            importlib.import_module(module)
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise QtMissing(
                f"The control window needs {module.split('.')[0]}:\n    {hint}"
            ) from exc


def _build_window_class():
    """Define the window only once Qt is known to be importable.

    Subclassing ``QMainWindow`` at module scope would make importing this
    module fail on a machine without PySide6 -- and this module is imported by
    the CLI, which has to keep working there.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication
    from PySide6.QtWidgets import (
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QSplitter,
        QVBoxLayout,
        QWidget,
    )
    from pyvistaqt import QtInteractor

    class ColorButton(QPushButton):
        """A button that shows a colour and opens a picker."""

        def __init__(self, color: str, parent=None):
            super().__init__(parent)
            self._color = color
            self.setFixedHeight(24)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._paint()
            self.clicked.connect(self._choose)
            self.changed = None          # set by the owner

        def color(self) -> str:
            return self._color

        def set_color(self, color: str) -> None:
            self._color = color
            self._paint()

        def _paint(self) -> None:
            text_light = QColor(self._color).lightness() < 140
            self.setText(self._color)
            self.setStyleSheet(
                f"QPushButton {{ background: {self._color}; "
                f"color: {'#ffffff' if text_light else '#16242c'}; "
                "border: 1px solid rgba(0,0,0,0.25); border-radius: 3px; "
                "font-family: monospace; }"
            )

        def _choose(self) -> None:
            chosen = QColorDialog.getColor(QColor(self._color), self,
                                           "Pick a colour")
            if chosen.isValid():
                self.set_color(chosen.name())
                if callable(self.changed):
                    self.changed()

    class NetworkWindow(QMainWindow):
        """Controls on the left, a rotatable brain on the right."""

        def __init__(
            self,
            data: NetworkData,
            settings: Settings,
            style: NetworkStyle,
            *,
            edge_count: Optional[int] = None,
            view: str = "left",
            source: Path | str = "NETWORK_DIR",
            output: str = "figure.png",
            parent=None,
        ):
            super().__init__(parent)
            self.data = data
            self.settings = settings
            self.style = style
            self.source = source
            self.output = output
            self._dirty = False
            self._building = True
            self._scene = None

            self.n_pairs = int(pair_maxima(data.matrix).size)
            self.positive_limit = max_positive_edge_count(data.matrix)

            self.setWindowTitle(f"cifti_state — network: {data.name or 'network'}")
            self.resize(1420, 920)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(self._make_controls(edge_count, view))
            splitter.addWidget(self._make_view())
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setSizes([390, 1030])
            self.setCentralWidget(splitter)

            self._building = False
            self.refresh()

        # -- construction --------------------------------------------------- #

        def _make_view(self) -> QWidget:
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            self.plotter = QtInteractor(holder)
            layout.addWidget(self.plotter.interactor, 1)

            bar = QHBoxLayout()
            self.command_box = QLineEdit()
            self.command_box.setReadOnly(True)
            self.command_box.setStyleSheet("font-family: monospace;")
            bar.addWidget(QLabel("Command:"))
            bar.addWidget(self.command_box, 1)

            copy = QPushButton("Copy")
            copy.setToolTip("Copy the command line that reproduces this figure")
            copy.clicked.connect(self._copy_command)
            bar.addWidget(copy)

            save = QPushButton("Save figure…")
            save.clicked.connect(self._save_figure)
            bar.addWidget(save)

            wrapper = QWidget()
            wrapper.setLayout(bar)
            layout.addWidget(wrapper)
            return holder

        def _make_controls(self, edge_count, view) -> QWidget:
            page = QWidget()
            column = QVBoxLayout(page)
            column.setContentsMargins(10, 10, 10, 10)
            column.setSpacing(10)

            attributes = list(self.data.attribute_names)
            fixed = "(fixed)"

            # -- which edges ------------------------------------------------ #
            box = QGroupBox("Which edges")
            form = QFormLayout(box)
            self.rule = QComboBox()
            self.rule.addItems(["edge count k", "weight threshold",
                                "strongest N"])
            self.rule.currentIndexChanged.connect(self._rule_changed)
            form.addRow("Rule", self.rule)

            self.k_spin = QSpinBox()
            self.k_spin.setRange(1, self.n_pairs)
            self.k_spin.setValue(int(edge_count or self.style.edge_count
                                     or min(16, self.n_pairs)))
            self.k_spin.valueChanged.connect(self._touch)
            form.addRow("k", self.k_spin)

            self.threshold_spin = QDoubleSpinBox()
            self.threshold_spin.setDecimals(5)
            self.threshold_spin.setRange(-1e6, 1e6)
            self.threshold_spin.setSingleStep(0.01)
            self.threshold_spin.setValue(0.05)
            self.threshold_spin.valueChanged.connect(self._touch)
            form.addRow("Threshold", self.threshold_spin)

            self.top_spin = QSpinBox()
            self.top_spin.setRange(1, max(1, self.data.n_nodes ** 2))
            self.top_spin.setValue(min(24, self.data.n_nodes ** 2))
            self.top_spin.valueChanged.connect(self._touch)
            form.addRow("N", self.top_spin)

            self.rank_abs = QCheckBox("rank by |weight|")
            self.rank_abs.stateChanged.connect(self._touch)
            form.addRow("", self.rank_abs)
            self.drop_negative = QCheckBox("drop negative edges")
            self.drop_negative.stateChanged.connect(self._touch)
            form.addRow("", self.drop_negative)

            self.readout = QLabel("")
            self.readout.setWordWrap(True)
            form.addRow("", self.readout)
            column.addWidget(box)

            # -- nodes ------------------------------------------------------ #
            box = QGroupBox("Nodes")
            form = QFormLayout(box)
            self.size_by = QComboBox()
            self.size_by.addItems([fixed] + attributes)
            if self.style.node.size_by in attributes:
                self.size_by.setCurrentText(self.style.node.size_by)
            self.size_by.currentIndexChanged.connect(self._touch)
            form.addRow("Size by", self.size_by)

            self.size_min = QDoubleSpinBox()
            self.size_max = QDoubleSpinBox()
            for spin, value in ((self.size_min, self.style.node.size_range[0]),
                                (self.size_max, self.style.node.size_range[1])):
                spin.setRange(0.1, 30.0)
                spin.setSingleStep(0.1)
                spin.setDecimals(2)
                spin.setSuffix(" mm")
                spin.setValue(float(value))
                spin.valueChanged.connect(self._touch)
            radii = QHBoxLayout()
            radii.setContentsMargins(0, 0, 0, 0)
            radii.addWidget(self.size_min)
            radii.addWidget(self.size_max)
            holder = QWidget()
            holder.setLayout(radii)
            form.addRow("Radius", holder)

            self.color_by = QComboBox()
            self.color_by.addItems([fixed] + attributes)
            if self.style.node.color_by in attributes:
                self.color_by.setCurrentText(self.style.node.color_by)
            self.color_by.currentIndexChanged.connect(self._color_by_changed)
            form.addRow("Colour by", self.color_by)

            self.group_colors: list[ColorButton] = []
            self.group_row = QWidget()
            self.group_layout = QHBoxLayout(self.group_row)
            self.group_layout.setContentsMargins(0, 0, 0, 0)
            self.group_layout.setSpacing(4)
            form.addRow("Groups", self.group_row)

            self.labels_on = QCheckBox("show node labels")
            self.labels_on.setChecked(bool(self.style.node.labels))
            self.labels_on.stateChanged.connect(self._touch)
            form.addRow("", self.labels_on)

            self.label_size = QSpinBox()
            self.label_size.setRange(6, 48)
            self.label_size.setValue(int(self.style.node.label_size))
            self.label_size.valueChanged.connect(self._touch)
            form.addRow("Label size", self.label_size)
            column.addWidget(box)

            # -- edges ------------------------------------------------------ #
            box = QGroupBox("Edges")
            form = QFormLayout(box)
            self.edge_color = QComboBox()
            self.edge_color.addItems(list(EDGE_COLOR_MODES))
            self.edge_color.setCurrentText(self.style.edge.color_mode)
            self.edge_color.currentIndexChanged.connect(self._touch)
            form.addRow("Colour", self.edge_color)

            self.negative_color = ColorButton(self.style.edge.negative_color)
            self.positive_color = ColorButton(self.style.edge.positive_color)
            for button in (self.negative_color, self.positive_color):
                button.changed = self._touch
            pair = QHBoxLayout()
            pair.setContentsMargins(0, 0, 0, 0)
            pair.addWidget(self.negative_color)
            pair.addWidget(self.positive_color)
            holder = QWidget()
            holder.setLayout(pair)
            form.addRow("− / +", holder)

            self.direction = QComboBox()
            self.direction.addItems(list(EDGE_DIRECTION_MODES))
            self.direction.setCurrentText(self.style.edge.direction_mode)
            self.direction.currentIndexChanged.connect(self._touch)
            form.addRow("Direction", self.direction)

            self.width_by_weight = QCheckBox("width follows |weight|")
            self.width_by_weight.setChecked(bool(self.style.edge.width_by_weight))
            self.width_by_weight.stateChanged.connect(self._touch)
            form.addRow("", self.width_by_weight)

            self.width_min = QDoubleSpinBox()
            self.width_max = QDoubleSpinBox()
            for spin, value in ((self.width_min, self.style.edge.width_range[0]),
                                (self.width_max, self.style.edge.width_range[1])):
                spin.setRange(0.02, 10.0)
                spin.setSingleStep(0.05)
                spin.setDecimals(2)
                spin.setSuffix(" mm")
                spin.setValue(float(value))
                spin.valueChanged.connect(self._touch)
            widths = QHBoxLayout()
            widths.setContentsMargins(0, 0, 0, 0)
            widths.addWidget(self.width_min)
            widths.addWidget(self.width_max)
            holder = QWidget()
            holder.setLayout(widths)
            form.addRow("Width", holder)

            self.fixed_width = QDoubleSpinBox()
            self.fixed_width.setRange(0.02, 10.0)
            self.fixed_width.setSingleStep(0.05)
            self.fixed_width.setDecimals(2)
            self.fixed_width.setSuffix(" mm")
            self.fixed_width.setValue(float(self.style.edge.width))
            self.fixed_width.valueChanged.connect(self._touch)
            form.addRow("Fixed width", self.fixed_width)

            self.arrow_size = QDoubleSpinBox()
            self.arrow_size.setRange(0.0, 30.0)
            self.arrow_size.setSingleStep(0.5)
            self.arrow_size.setDecimals(1)
            self.arrow_size.setSuffix(" mm")
            self.arrow_size.setValue(float(self.style.edge.arrow_size))
            self.arrow_size.valueChanged.connect(self._touch)
            form.addRow("Arrow length", self.arrow_size)
            column.addWidget(box)

            # -- the shell -------------------------------------------------- #
            box = QGroupBox("Glass brain")
            form = QFormLayout(box)
            self.surface = QComboBox()
            self.surface.addItems(list(_SURFACES))
            self.surface.setCurrentText(self.style.brain.surface)
            self.surface.currentIndexChanged.connect(self._touch)
            form.addRow("Surface", self.surface)

            self.hemispheres = QComboBox()
            self.hemispheres.addItems(["both", "left", "right"])
            if len(self.style.brain.hemispheres) == 1:
                self.hemispheres.setCurrentText(self.style.brain.hemispheres[0])
            self.hemispheres.currentIndexChanged.connect(self._touch)
            form.addRow("Hemispheres", self.hemispheres)

            self.brain_opacity = QDoubleSpinBox()
            self.brain_opacity.setRange(0.0, 1.0)
            self.brain_opacity.setSingleStep(0.05)
            self.brain_opacity.setDecimals(2)
            self.brain_opacity.setValue(float(self.style.brain.opacity))
            self.brain_opacity.valueChanged.connect(self._touch)
            form.addRow("Opacity", self.brain_opacity)

            self.split_mm = QDoubleSpinBox()
            self.split_mm.setRange(0.0, 120.0)
            self.split_mm.setSingleStep(2.0)
            self.split_mm.setDecimals(0)
            self.split_mm.setSuffix(" mm")
            self.split_mm.setValue(float(self.style.brain.split_mm))
            self.split_mm.valueChanged.connect(self._touch)
            form.addRow("Split", self.split_mm)

            self.view = QComboBox()
            self.view.addItems(list(CAMERA_VIEWS))
            self.view.setCurrentText(resolve_view(view))
            self.view.currentIndexChanged.connect(self._view_changed)
            form.addRow("View", self.view)
            column.addWidget(box)

            column.addStretch(1)
            self._rule_changed()
            self._rebuild_group_swatches()

            scroll = QScrollArea()
            scroll.setWidget(page)
            scroll.setWidgetResizable(True)
            scroll.setMinimumWidth(360)

            # The action row is pinned below the scroll area rather than inside
            # it.  There are more controls than fit on a laptop screen, and a
            # Refresh button you have to scroll down to find is a Refresh
            # button you will forget to press.
            panel = QWidget()
            outer = QVBoxLayout(panel)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(scroll, 1)

            actions = QHBoxLayout()
            actions.setContentsMargins(10, 8, 10, 10)
            self.refresh_button = QPushButton("Refresh")
            self.refresh_button.setDefault(True)
            self.refresh_button.setMinimumHeight(30)
            self.refresh_button.setToolTip(
                "Apply the changes you have made to the view"
            )
            self.refresh_button.clicked.connect(self.refresh)
            actions.addWidget(self.refresh_button, 1)

            self.auto = QCheckBox("Auto")
            self.auto.setToolTip(
                "Redraw on every change instead of waiting for Refresh"
            )
            actions.addWidget(self.auto)

            reset_camera = QPushButton("Reset camera")
            reset_camera.setToolTip("Point the camera back at the named view")
            reset_camera.clicked.connect(self._reset_camera)
            actions.addWidget(reset_camera)

            bar = QWidget()
            bar.setLayout(actions)
            outer.addWidget(bar)
            return panel

        # -- reacting ------------------------------------------------------- #

        def _touch(self, *args) -> None:
            """Something changed: mark it, and redraw now if Auto is on."""
            if self._building:
                return
            self._dirty = True
            self.refresh_button.setText("Refresh •")
            if self.auto.isChecked():
                self.refresh()

        def _rule_changed(self, *args) -> None:
            index = self.rule.currentIndex()
            self.k_spin.setEnabled(index == 0)
            self.threshold_spin.setEnabled(index == 1)
            self.top_spin.setEnabled(index == 2)
            self._touch()

        def _view_changed(self, *args) -> None:
            # The camera is cheap, so it moves immediately -- waiting for
            # Refresh to look at the other side would be absurd.
            if self._building:
                return
            if self._scene is not None:
                self._scene.apply_view(self.plotter, self.view.currentText())
                self.plotter.render()
            self._update_command()

        def _reset_camera(self) -> None:
            if self._scene is not None:
                self._scene.apply_view(self.plotter, self.view.currentText())
                self.plotter.render()

        def _color_by_changed(self, *args) -> None:
            self._rebuild_group_swatches()
            self._touch()

        def _rebuild_group_swatches(self) -> None:
            """One colour button per level of the colour attribute."""
            while self.group_layout.count():
                item = self.group_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.group_colors = []

            name = self.color_by.currentText()
            if name not in self.data.attribute_names:
                self.group_row.setVisible(False)
                return
            values = self.data.attribute(name)
            levels = np.unique(values[np.isfinite(values)])
            if levels.size > 12:
                # A continuous attribute: it gets a colour map, not swatches.
                self.group_row.setVisible(False)
                return
            palette = list(self.style.node.discrete_colors)
            for index in range(int(levels.size)):
                button = ColorButton(palette[index % len(palette)]
                                     if palette else "#888888")
                button.changed = self._touch
                self.group_layout.addWidget(button)
                self.group_colors.append(button)
            self.group_row.setVisible(True)

        # -- the current state ---------------------------------------------- #

        def current_style(self) -> NetworkStyle:
            fixed = "(fixed)"
            size_by = self.size_by.currentText()
            color_by = self.color_by.currentText()
            node = replace(
                self.style.node,
                size_by=None if size_by == fixed else size_by,
                size_range=(self.size_min.value(), self.size_max.value()),
                color_by=None if color_by == fixed else color_by,
                discrete_colors=tuple(b.color() for b in self.group_colors)
                or self.style.node.discrete_colors,
                labels=self.labels_on.isChecked(),
                label_size=self.label_size.value(),
            )
            edge = replace(
                self.style.edge,
                color_mode=self.edge_color.currentText(),
                direction_mode=self.direction.currentText(),
                negative_color=self.negative_color.color(),
                positive_color=self.positive_color.color(),
                width=self.fixed_width.value(),
                width_by_weight=self.width_by_weight.isChecked(),
                width_range=(self.width_min.value(), self.width_max.value()),
                arrow_size=self.arrow_size.value(),
            )
            hemis = self.hemispheres.currentText()
            brain = replace(
                self.style.brain,
                surface=self.surface.currentText(),
                hemispheres=(("left", "right") if hemis == "both" else (hemis,)),
                opacity=self.brain_opacity.value(),
                show=self.brain_opacity.value() > 0.01,
                split_mm=self.split_mm.value(),
            )
            return replace(
                self.style, node=node, edge=edge, brain=brain,
                views=(self.view.currentText(),),
                edge_count=(self.k_spin.value() if self.rule.currentIndex() == 0
                            else None),
            )

        def selection_kwargs(self) -> dict[str, Any]:
            index = self.rule.currentIndex()
            kwargs: dict[str, Any] = {
                "absolute": self.rank_abs.isChecked(),
                "drop_negative": self.drop_negative.isChecked(),
            }
            if index == 0:
                kwargs["edge_count"] = self.k_spin.value()
            elif index == 1:
                kwargs["threshold"] = self.threshold_spin.value()
            else:
                kwargs["top"] = self.top_spin.value()
            return kwargs

        # -- drawing -------------------------------------------------------- #

        def refresh(self) -> None:
            style = self.current_style()
            view = self.view.currentText()
            camera = self.plotter.camera_position if self._scene is not None \
                else None
            try:
                scene = build_network_scene(
                    self.data, self.settings, style, view=view,
                    **self.selection_kwargs(),
                )
            except Exception as exc:  # a bad combination should not kill the app
                log.exception("could not build the scene")
                QMessageBox.warning(self, "Could not draw that", str(exc))
                return

            self.plotter.clear_actors()
            scene.add_to(self.plotter, reset_camera=False)
            if camera is not None:
                self.plotter.camera_position = camera
            else:
                scene.apply_view(self.plotter, view)
            self.plotter.render()

            self._scene = scene
            self._dirty = False
            self.refresh_button.setText("Refresh")
            self._update_readout(scene)
            self._update_command()

        def _update_readout(self, scene) -> None:
            edges = scene.edges
            if edges is None:
                self.readout.setText("")
                return
            cutoff = edges.threshold
            lines = [f"<b>{len(edges)}</b> arrows"]
            if cutoff is not None and np.isfinite(cutoff):
                lines.append(f"cutoff {cutoff:.5f}")
            if edges.n_negative:
                lines.append(f"{edges.n_negative} negative")
            text = " &middot; ".join(lines)
            past = (self.rule.currentIndex() == 0
                    and self.k_spin.value() > self.positive_limit)
            if past:
                text += (
                    f"<br><span style='color:#b3261e'>k is past {self.positive_limit}: "
                    "the cutoff is negative, so negative weights are being "
                    "drawn as ordinary edges.</span>"
                )
            elif self.rule.currentIndex() == 0:
                text += (f"<br><span style='color:#5a6a73'>positive cutoff up "
                         f"to k = {self.positive_limit}</span>")
            self.readout.setText(text)

        def _update_command(self) -> None:
            index = self.rule.currentIndex()
            line = command_line_for(
                self.current_style(), source=self.source,
                edge_count=self.k_spin.value() if index == 0 else None,
                view=self.view.currentText(), output=self.output,
            )
            if index == 1:
                line = line.replace("--views", f"--threshold "
                                    f"{self.threshold_spin.value():.5f} --views", 1)
            elif index == 2:
                line = line.replace("--views",
                                    f"--top {self.top_spin.value()} --views", 1)
            if self.rank_abs.isChecked():
                line = line.replace("--views", "--rank-abs --views", 1)
            if self.drop_negative.isChecked():
                line = line.replace("--views", "--drop-negative --views", 1)
            self.command_box.setText(line)
            self.command_box.setCursorPosition(0)

        # -- actions -------------------------------------------------------- #

        def _copy_command(self) -> None:
            QGuiApplication.clipboard().setText(self.command_box.text())
            self.statusBar().showMessage("Command copied to the clipboard", 3000)

        def _save_figure(self) -> None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save the figure", self.output,
                "Images (*.png *.pdf *.svg);;All files (*)",
            )
            if not path:
                return
            from .render import render_network

            try:
                figure = render_network(
                    self.data, self.settings, style=self.current_style(),
                    out=path, views=[self.view.currentText()],
                    **self.selection_kwargs(),
                )
            except Exception as exc:
                log.exception("could not write the figure")
                QMessageBox.warning(self, "Could not save that", str(exc))
                return
            self.statusBar().showMessage(f"Wrote {figure.path}", 6000)

        def closeEvent(self, event):  # noqa: N802 - Qt's name
            try:
                self.plotter.close()
            except Exception:  # pragma: no cover - teardown is best effort
                pass
            super().closeEvent(event)

    return NetworkWindow


_WINDOW_CLASS = None


def NetworkWindow(*args, **kwargs):  # noqa: N802 - it stands in for a class
    """Construct the window, defining its class on first use."""
    global _WINDOW_CLASS
    _require_qt()
    if _WINDOW_CLASS is None:
        _WINDOW_CLASS = _build_window_class()
    return _WINDOW_CLASS(*args, **kwargs)


def open_window(
    data: NetworkData,
    settings: Settings,
    style: Optional[NetworkStyle] = None,
    *,
    edge_count: Optional[int] = None,
    view: str = "left",
    source: Path | str = "NETWORK_DIR",
    output: str = "figure.png",
    block: bool = True,
) -> Any:
    """Open the control window.  Blocks until it is closed unless told not to."""
    _require_qt()
    import sys

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ..fonts import apply_to_matplotlib, apply_to_qt

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv)
        app.setApplicationName("cifti_state")
        app.setStyle("Fusion")
        choice = apply_to_qt(app, pixel_size=int(settings.interface.font_size_px),
                             settings=settings)
        apply_to_matplotlib(choice)

    window = NetworkWindow(
        data, settings, style or NetworkStyle(), edge_count=edge_count,
        view=view, source=source, output=output,
    )
    window.show()
    if block and owns_app:
        app.exec()
        print("\nThe figure you were looking at:\n")
        print("  " + window.command_box.text() + "\n")
    return window
