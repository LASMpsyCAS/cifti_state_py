"""Step 3 -- anatomical report: atlas, how many regions, layout, export.

Changing any control here only re-runs the annotation step, which is cheap:
the clusters themselves are untouched, so the report can be adjusted freely
without re-clustering.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..widgets import Card, Pill, compact_combo

__all__ = ["ReportPanel"]

REPORT_FILTER = (
    "Excel workbook (*.xlsx);;Comma separated (*.csv);;Tab separated (*.tsv);;"
    "Markdown (*.md);;JSON (*.json)"
)


class ReportPanel(QWidget):
    """Atlas choice and report shape, plus export."""

    report_requested = Signal()
    export_requested = Signal()
    live_update_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = Card(
            "Anatomical report",
            step=3,
            hint="Region names come from each atlas's own label table, so the "
                 "hemispheres are never mixed up.",
        )
        layout.addWidget(self.card)

        self.status_pill = Pill("waiting for clusters", "muted")
        self.card.add_header_widget(self.status_pill)

        self.atlas = QComboBox()
        self.atlas.setToolTip("Atlases found in the configured atlas directory.")
        compact_combo(self.atlas, visible_chars=14, popup_width=340)
        self.card.add_row("Atlas", self.atlas)

        self.top_n = QSpinBox()
        self.top_n.setRange(0, 20)
        self.top_n.setValue(2)
        self.top_n.setSpecialValueText("all regions")
        self.top_n.setToolTip(
            "How many contributing regions to list per cluster, ordered by "
            "share of the cluster. 0 lists every region."
        )
        self.card.add_row("Regions each", self.top_n)

        self.min_percent = QDoubleSpinBox()
        self.min_percent.setRange(0.0, 100.0)
        self.min_percent.setDecimals(1)
        self.min_percent.setSingleStep(1.0)
        self.min_percent.setValue(0.0)
        self.min_percent.setSuffix(" %")
        self.min_percent.setToolTip("Drop regions contributing less than this.")
        self.card.add_row("Minimum share", self.min_percent)

        self.style = QComboBox()
        self.style.addItem("Wide — one row per cluster", "wide")
        self.style.addItem("Long — one row per region", "long")
        self.style.addItem("Legacy — MATLAB column names", "legacy")
        compact_combo(self.style, visible_chars=12, popup_width=250)
        self.card.add_row("Layout", self.style)

        self.live = QCheckBox("Update as I change these")
        self.live.setChecked(True)
        self.live.setToolTip(
            "Annotation is fast, so the table can follow the controls. "
            "Turn off for very large atlases."
        )
        self.card.add_widget(self.live)

        self.build_button = QPushButton("Build report")
        self.build_button.setObjectName("Primary")
        self.build_button.setEnabled(False)
        self.export_button = QPushButton("Export…")
        self.export_button.setObjectName("Ghost")
        self.export_button.setEnabled(False)
        self.card.add_footer_row([self.build_button, self.export_button])

        self.build_button.clicked.connect(self.report_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.live.toggled.connect(self.live_update_changed)
        for widget in (self.atlas, self.style):
            widget.currentIndexChanged.connect(self._maybe_live)
        self.top_n.valueChanged.connect(self._maybe_live)
        self.min_percent.valueChanged.connect(self._maybe_live)

    # -- reading ------------------------------------------------------------ #

    def atlas_name(self) -> str:
        return self.atlas.currentData() or self.atlas.currentText()

    def top_n_value(self) -> int:
        return int(self.top_n.value())

    def min_percent_value(self) -> float:
        return float(self.min_percent.value())

    def style_value(self) -> str:
        return self.style.currentData()

    def live_enabled(self) -> bool:
        return self.live.isChecked()

    # -- writing ------------------------------------------------------------ #

    def set_atlases(self, entries: list[dict], preferred: str = "") -> None:
        """*entries* come from :func:`cifti_state.io.atlas.list_atlases`."""
        self.atlas.blockSignals(True)
        self.atlas.clear()
        for entry in entries:
            label = entry.get("display_name") or entry["name"]
            regions = entry.get("n_regions")
            if regions:
                label = f"{label}  ·  {regions}"
            self.atlas.addItem(label, entry["name"])
        index = self.atlas.findData(preferred)
        if index >= 0:
            self.atlas.setCurrentIndex(index)
        self.atlas.blockSignals(False)
        if not entries:
            self.atlas.addItem("no atlases found", "")
            self.status_pill.set("no atlases", "danger")

    def set_enabled(self, enabled: bool) -> None:
        self.build_button.setEnabled(enabled)
        if not enabled:
            self.status_pill.set("waiting for clusters", "muted")
        elif self.status_pill.text() in ("waiting for clusters", ""):
            self.status_pill.set("ready", "neutral")

    def set_export_enabled(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.atlas, self.top_n, self.min_percent, self.style, self.build_button
        ):
            widget.setEnabled(not busy)
        if busy:
            self.status_pill.set("annotating…", "warn")

    def show_done(self, n_rows: int, atlas_name: str) -> None:
        self.status_pill.set(f"{n_rows} rows · {atlas_name}", "neutral")

    def show_error(self, message: str) -> None:
        self.status_pill.set("failed", "danger")
        self.setToolTip(message)

    # -- internals ---------------------------------------------------------- #

    def _maybe_live(self, *_args) -> None:
        if self.live.isChecked() and self.build_button.isEnabled():
            self.report_requested.emit()
