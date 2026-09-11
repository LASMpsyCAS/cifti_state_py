"""Step 1 -- choose the statistic map and declare what its values are."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..widgets import Card, KeyValueList, Pill, compact_combo

__all__ = ["InputPanel"]

CIFTI_FILTER = (
    "CIFTI surface maps (*.dscalar.nii *.dtseries.nii *.dlabel.nii);;"
    "All files (*)"
)


class InputPanel(QWidget):
    """File picker plus the statistic declaration.

    ``load_requested`` carries the chosen path; the window does the loading on
    a worker thread and calls :meth:`show_facts` when it lands.
    """

    load_requested = Signal(object)      # Path
    settings_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = Card(
            "Data",
            step=1,
            hint="A dscalar / dtseries / dlabel on the fs_LR surface. "
                 "91k, 59k and 64k layouts are all read the same way.",
        )
        layout.addWidget(self.card)

        self.status_pill = Pill("no file", "muted")
        self.card.add_header_widget(self.status_pill)

        from ..widgets import FilePicker

        self.picker = FilePicker(
            caption="Open a surface statistic map",
            filters=CIFTI_FILTER,
            placeholder="choose a .dscalar.nii …",
        )
        self.card.add_row("File", self.picker)

        self.statistic = QComboBox()
        self.statistic.addItem("z score", "z")
        self.statistic.addItem("t value", "t")
        self.statistic.addItem("other (no p-values)", "other")
        self.statistic.setToolTip(
            "What the values in the map are.\n"
            "FDR thresholding converts them to p-values, so this has to be "
            "declared rather than guessed — reading t values as z overstates "
            "significance."
        )
        compact_combo(self.statistic, visible_chars=10, popup_width=220)
        self.card.add_row("Values are", self.statistic)

        self.df = QDoubleSpinBox()
        self.df.setRange(1.0, 100000.0)
        self.df.setDecimals(1)
        self.df.setValue(30.0)
        self.df.setEnabled(False)
        self.df.setToolTip("Degrees of freedom — required for a t map.")
        self.card.add_row("df", self.df)

        self.column = QSpinBox()
        self.column.setRange(0, 9999)
        self.column.setToolTip("Which map to take when the file holds several.")
        self.card.add_row("Column", self.column)

        self.facts = KeyValueList()
        self.card.add_widget(self.facts)

        self.load_button = QPushButton("Load map")
        self.load_button.setObjectName("Primary")
        self.load_button.setEnabled(False)
        self.card.add_footer(self.load_button)

        self.picker.changed.connect(self._on_path_changed)
        self.statistic.currentIndexChanged.connect(self._on_statistic_changed)
        self.load_button.clicked.connect(self._request_load)
        self.column.valueChanged.connect(lambda _: self.settings_changed.emit())
        self.df.valueChanged.connect(lambda _: self.settings_changed.emit())

    # -- reading ------------------------------------------------------------ #

    def path(self) -> Optional[Path]:
        return self.picker.path()

    def statistic_kind(self) -> str:
        return self.statistic.currentData()

    def degrees_of_freedom(self) -> Optional[float]:
        return float(self.df.value()) if self.statistic_kind() == "t" else None

    def column_index(self) -> int:
        return int(self.column.value())

    # -- writing ------------------------------------------------------------ #

    def set_path(self, path: Path) -> None:
        self.picker.set_path(path)

    def show_facts(self, facts: list[tuple[str, str]]) -> None:
        self.facts.set_items(facts)
        self.status_pill.set("loaded", "neutral")

    def show_error(self, message: str) -> None:
        self.facts.set_items([("error", message)])
        self.status_pill.set("failed", "danger")

    def clear_facts(self) -> None:
        self.facts.clear()
        self.status_pill.set("no file" if not self.path() else "not loaded", "muted")

    def set_busy(self, busy: bool) -> None:
        self.load_button.setEnabled(not busy and self.path() is not None)
        self.picker.setEnabled(not busy)
        self.statistic.setEnabled(not busy)
        self.df.setEnabled(not busy and self.statistic_kind() == "t")
        self.column.setEnabled(not busy)
        if busy:
            self.status_pill.set("loading…", "warn")

    # -- internals ---------------------------------------------------------- #

    def _on_path_changed(self, path: Optional[Path]) -> None:
        self.load_button.setEnabled(path is not None)
        self.clear_facts()
        self.settings_changed.emit()

    def _on_statistic_changed(self) -> None:
        self.df.setEnabled(self.statistic_kind() == "t")
        self.settings_changed.emit()

    def _request_load(self) -> None:
        path = self.path()
        if path is not None:
            self.load_requested.emit(path)
