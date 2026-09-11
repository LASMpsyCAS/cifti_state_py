"""Step 2 -- threshold and cluster."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..widgets import Card, KeyValueList, Pill, compact_combo

__all__ = ["ClusterPanel"]


class ClusterPanel(QWidget):
    """Threshold method, direction, extent, and the Run button."""

    cluster_requested = Signal()
    cancel_requested = Signal()
    settings_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = Card(
            "Cluster",
            step=2,
            hint="Connected supra-threshold vertices, using the fs_LR "
                 "adjacency. Legacy mode reproduces the MATLAB result exactly.",
        )
        layout.addWidget(self.card)

        self.status_pill = Pill("waiting for data", "muted")
        self.card.add_header_widget(self.status_pill)

        # -- threshold method ------------------------------------------------ #
        self.method = QComboBox()
        self.method.addItem("Fixed value", "fixed")
        self.method.addItem("FDR (Benjamini-Hochberg)", "fdr")
        self.method.addItem("Percentile", "percentile")
        compact_combo(self.method, visible_chars=12, popup_width=260)
        self.card.add_row("Threshold", self.method)

        self.method_stack = QStackedWidget()
        self.method_stack.setMinimumHeight(30)

        self.fixed_value = QDoubleSpinBox()
        self.fixed_value.setRange(-1000.0, 1000.0)
        self.fixed_value.setDecimals(4)
        self.fixed_value.setSingleStep(0.05)
        self.fixed_value.setValue(1.039)
        self.method_stack.addWidget(_wrap(self.fixed_value))

        self.fdr_q = QDoubleSpinBox()
        self.fdr_q.setRange(0.0001, 0.5)
        self.fdr_q.setDecimals(4)
        self.fdr_q.setSingleStep(0.01)
        self.fdr_q.setValue(0.05)
        self.fdr_q.setPrefix("q = ")
        self.method_stack.addWidget(_wrap(self.fdr_q))

        self.percentile = QDoubleSpinBox()
        self.percentile.setRange(50.0, 99.999)
        self.percentile.setDecimals(2)
        self.percentile.setValue(95.0)
        self.percentile.setSuffix(" %")
        self.method_stack.addWidget(_wrap(self.percentile))

        self.card.add_row("Value", self.method_stack)

        # -- direction and extent -------------------------------------------- #
        self.direction = QComboBox()
        self.direction.addItem("Positive only", "positive")
        self.direction.addItem("Negative only", "negative")
        self.direction.addItem("Both signs", "two_sided")
        self.direction.setToolTip(
            "The MATLAB version could only find positive clusters.\n"
            "Negative and two-sided work here."
        )
        compact_combo(self.direction, visible_chars=10, popup_width=200)
        self.card.add_row("Direction", self.direction)

        self.extent = QSpinBox()
        self.extent.setRange(1, 100000)
        self.extent.setValue(20)
        self.extent.setSuffix("  vertices")
        self.extent.setToolTip("Minimum cluster size; smaller clusters are dropped.")
        self.card.add_row("Extent", self.extent)

        self.neighbor_source = QComboBox()
        self.neighbor_source.addItem("Neighbour tables (txt)", "txt")
        self.neighbor_source.addItem("Surface topology", "surface")
        self.neighbor_source.setToolTip(
            "Both give the identical graph on fs_LR 32k — verified vertex by "
            "vertex. 'Surface topology' also works at other mesh densities."
        )
        compact_combo(self.neighbor_source, visible_chars=12, popup_width=230)
        self.card.add_row("Adjacency", self.neighbor_source)

        self.legacy = QCheckBox("Legacy mode (match MATLAB exactly)")
        self.legacy.setChecked(True)
        self.legacy.setToolTip(
            "Forces positive-only and the original asymmetric handling of "
            "infinities, so the cluster map matches the MATLAB output "
            "vertex for vertex."
        )
        self.card.add_widget(self.legacy)

        self.facts = KeyValueList()
        self.card.add_widget(self.facts)

        # -- actions ---------------------------------------------------------- #
        self.run_button = QPushButton("Find clusters")
        self.run_button.setObjectName("Primary")
        self.run_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.setVisible(False)
        self.card.add_footer_row([self.run_button, self.cancel_button])

        # -- wiring ----------------------------------------------------------- #
        self.method.currentIndexChanged.connect(self.method_stack.setCurrentIndex)
        self.method.currentIndexChanged.connect(lambda _: self.settings_changed.emit())
        self.legacy.toggled.connect(self._on_legacy_toggled)
        self.direction.currentIndexChanged.connect(lambda _: self.settings_changed.emit())
        for spin in (self.fixed_value, self.fdr_q, self.percentile):
            spin.valueChanged.connect(lambda _: self.settings_changed.emit())
        self.extent.valueChanged.connect(lambda _: self.settings_changed.emit())
        self.neighbor_source.currentIndexChanged.connect(
            lambda _: self.settings_changed.emit()
        )
        self.run_button.clicked.connect(self.cluster_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)

        self._on_legacy_toggled(True)

    # -- reading ------------------------------------------------------------ #

    def threshold_method(self) -> str:
        return self.method.currentData()

    def threshold_value(self) -> float:
        return float(self.fixed_value.value())

    def q(self) -> float:
        return float(self.fdr_q.value())

    def percentile_value(self) -> float:
        return float(self.percentile.value())

    def direction_value(self) -> str:
        return "positive" if self.legacy.isChecked() else self.direction.currentData()

    def extent_value(self) -> int:
        return int(self.extent.value())

    def neighbor_source_value(self) -> str:
        return self.neighbor_source.currentData()

    def legacy_mode(self) -> bool:
        return self.legacy.isChecked()

    # -- writing ------------------------------------------------------------ #

    def apply_defaults(self, defaults) -> None:
        """Seed the controls from ``settings.defaults``."""
        index = self.method.findData(defaults.threshold.method)
        if index >= 0:
            self.method.setCurrentIndex(index)
        if defaults.threshold.value is not None:
            self.fixed_value.setValue(float(defaults.threshold.value))
        self.fdr_q.setValue(float(defaults.threshold.q))
        self.percentile.setValue(float(defaults.threshold.percentile))
        index = self.direction.findData(defaults.direction)
        if index >= 0:
            self.direction.setCurrentIndex(index)
        self.extent.setValue(int(defaults.extent))
        index = self.neighbor_source.findData(defaults.neighbor_source)
        if index >= 0:
            self.neighbor_source.setCurrentIndex(index)
        self.legacy.setChecked(bool(defaults.legacy_mode))

    def set_enabled(self, enabled: bool) -> None:
        self.run_button.setEnabled(enabled)
        self.status_pill.set(
            "ready" if enabled else "waiting for data",
            "neutral" if enabled else "muted",
        )

    def set_busy(self, busy: bool) -> None:
        self.run_button.setVisible(not busy)
        self.cancel_button.setVisible(busy)
        for widget in (
            self.method, self.method_stack, self.direction, self.extent,
            self.neighbor_source, self.legacy,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.status_pill.set("clustering…", "warn")

    def show_facts(self, facts: list[tuple[str, str]], n_clusters: int) -> None:
        self.facts.set_items(facts)
        if n_clusters:
            self.status_pill.set(f"{n_clusters} clusters", "neutral")
        else:
            self.status_pill.set("no clusters", "warn")

    def show_error(self, message: str) -> None:
        self.facts.set_items([("error", message)])
        self.status_pill.set("failed", "danger")

    def clear_facts(self) -> None:
        self.facts.clear()

    # -- internals ---------------------------------------------------------- #

    def _on_legacy_toggled(self, checked: bool) -> None:
        self.direction.setEnabled(not checked)
        if checked:
            index = self.direction.findData("positive")
            if index >= 0:
                self.direction.setCurrentIndex(index)
        self.settings_changed.emit()


def _wrap(widget: QWidget) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    return holder
