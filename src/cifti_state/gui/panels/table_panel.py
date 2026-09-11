"""The cluster table and the per-cluster actions.

Selecting a row selects a cluster: that is what "save as mask" and the
wb_view button act on.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..theme import PALETTE

__all__ = ["TablePanel", "DataFrameModel"]


class DataFrameModel(QAbstractTableModel):
    """A read-only view of a pandas DataFrame, with sign-aware colouring."""

    def __init__(self, frame: Optional[pd.DataFrame] = None):
        super().__init__()
        self._frame = frame if frame is not None else pd.DataFrame()

    def set_frame(self, frame: pd.DataFrame) -> None:
        self.beginResetModel()
        self._frame = frame.reset_index(drop=True)
        self.endResetModel()

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame.columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self._frame.iat[index.row(), index.column()]
        column = str(self._frame.columns[index.column()])

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if pd.isna(value):
                return ""
            if isinstance(value, float):
                if column.endswith(("_x", "_y", "_z")) or column == "size_mm2":
                    return f"{value:.0f}"
                if column.endswith("percent"):
                    return f"{value:.1f}"
                return f"{value:.4g}"
            return str(value)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if pd.api.types.is_numeric_dtype(type(value)) and not isinstance(value, bool):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if column in ("direction", "sign"):
                negative = (value == "negative") or (value == -1)
                return QColor(PALETTE.negative if negative else PALETTE.positive)
            if column in ("cluster_id", "hemi"):
                return QColor(PALETTE.teal_deep)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self._frame.columns[section]).replace("_", " ")
        return str(section + 1)

    def cluster_id_at(self, row: int) -> Optional[int]:
        if "cluster_id" not in self._frame.columns or row < 0 or row >= len(self._frame):
            return None
        try:
            return int(self._frame.iloc[row]["cluster_id"])
        except (TypeError, ValueError):
            return None


class TablePanel(QWidget):
    """Filter box, table, and the actions that depend on the selection."""

    selection_changed = Signal(object)        # list[int] of cluster ids
    save_mask_requested = Signal()
    open_wb_view_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # -- filter row ------------------------------------------------------ #
        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(2, 0, 2, 0)
        top_layout.setSpacing(8)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter rows — region name, hemisphere, …")
        self.filter_box.setClearButtonEnabled(True)
        top_layout.addWidget(self.filter_box, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        top_layout.addWidget(self.count_label)

        layout.addWidget(top)

        # -- table ----------------------------------------------------------- #
        self.model = DataFrameModel()
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        layout.addWidget(self.table, 1)

        # -- actions --------------------------------------------------------- #
        actions = QWidget(self)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 0, 2, 0)
        actions_layout.setSpacing(8)

        self.selection_label = QLabel("no cluster selected")
        self.selection_label.setObjectName("Muted")
        actions_layout.addWidget(self.selection_label)
        actions_layout.addStretch(1)

        self.label_values = QCheckBox("keep cluster ids")
        self.label_values.setToolTip(
            "Write each cluster's own id into the mask instead of 1s — useful "
            "when saving several clusters at once."
        )
        actions_layout.addWidget(self.label_values)

        self.save_mask_button = QPushButton("Save selection as mask…")
        self.save_mask_button.setObjectName("Ghost")
        self.save_mask_button.setEnabled(False)
        self.save_mask_button.setToolTip(
            "Write the selected cluster(s) as a binary dscalar in the same "
            "greyordinate layout as the input."
        )
        actions_layout.addWidget(self.save_mask_button)

        self.wb_view_button = QPushButton("Open in wb_view")
        self.wb_view_button.setObjectName("Primary")
        self.wb_view_button.setEnabled(False)
        self.wb_view_button.setToolTip(
            "Launch Connectome Workbench with the cluster map, the statistic "
            "map and the template surfaces loaded."
        )
        actions_layout.addWidget(self.wb_view_button)

        layout.addWidget(actions)

        # -- wiring ---------------------------------------------------------- #
        self.table.selectionModel().selectionChanged.connect(self._on_selection)
        self.filter_box.textChanged.connect(self._apply_filter)
        self.save_mask_button.clicked.connect(self.save_mask_requested)
        self.wb_view_button.clicked.connect(self.open_wb_view_requested)

        self._full_frame = pd.DataFrame()

    # -- reading ------------------------------------------------------------ #

    def selected_cluster_ids(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        ids = {self.model.cluster_id_at(row) for row in rows}
        return sorted(i for i in ids if i is not None)

    # -- writing ------------------------------------------------------------ #

    def set_frame(self, frame: pd.DataFrame) -> None:
        self._full_frame = frame
        self._apply_filter(self.filter_box.text())
        self._resize_columns()

    def clear(self) -> None:
        self._full_frame = pd.DataFrame()
        self.model.set_frame(pd.DataFrame())
        self.count_label.setText("")
        self.selection_label.setText("no cluster selected")
        self.save_mask_button.setEnabled(False)

    def set_wb_view_enabled(self, enabled: bool, reason: str = "") -> None:
        self.wb_view_button.setEnabled(enabled)
        if reason:
            self.wb_view_button.setToolTip(reason)

    def select_cluster(self, cluster_id: int) -> None:
        for row in range(self.model.rowCount()):
            if self.model.cluster_id_at(row) == cluster_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.model.index(row, 0))
                return

    # -- internals ---------------------------------------------------------- #

    def _apply_filter(self, text: str) -> None:
        text = (text or "").strip().lower()
        if not text or self._full_frame.empty:
            frame = self._full_frame
        else:
            haystack = self._full_frame.astype(str).apply(
                lambda column: column.str.lower()
            )
            mask = haystack.apply(
                lambda column: column.str.contains(text, regex=False, na=False)
            ).any(axis=1)
            frame = self._full_frame[mask]
        self.model.set_frame(frame)
        total = len(self._full_frame)
        shown = len(frame)
        self.count_label.setText(
            f"{shown} of {total} rows" if shown != total else f"{total} rows"
        )

    def _resize_columns(self) -> None:
        self.table.resizeColumnsToContents()
        header = self.table.horizontalHeader()
        for column in range(self.model.columnCount()):
            width = header.sectionSize(column)
            header.resizeSection(column, min(max(width, 60), 260))

    def _on_selection(self, *_args) -> None:
        ids = self.selected_cluster_ids()
        self.save_mask_button.setEnabled(bool(ids))
        if not ids:
            self.selection_label.setText("no cluster selected")
        elif len(ids) == 1:
            self.selection_label.setText(f"cluster {ids[0]} selected")
        else:
            self.selection_label.setText(f"{len(ids)} clusters selected")
        self.selection_changed.emit(ids)
