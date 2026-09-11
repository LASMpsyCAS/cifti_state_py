"""Small reusable widgets: cards, field rows, pills, file pickers.

Everything here is presentation only -- no analysis logic, no file IO beyond
the file dialogs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "compact_combo",
    "Card",
    "Divider",
    "FieldLabel",
    "Pill",
    "FilePicker",
    "KeyValueList",
    "hline",
]


def compact_combo(combo, *, visible_chars: int = 12, popup_width: int = 300):
    """Stop a long item from dictating the widget's width.

    A QComboBox sizes itself to its longest entry, so one item like
    "Schaefer 2018, 17 networks, 1000 parcels" would push the whole sidebar
    wider than it is allowed to be. The closed box stays narrow and elides;
    the popup keeps its full width so the names are still readable.
    """
    from PySide6.QtWidgets import QComboBox

    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(visible_chars)
    combo.setMinimumWidth(0)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    view = combo.view()
    if view is not None:
        view.setMinimumWidth(popup_width)
    return combo


class Divider(QFrame):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(1)


def hline() -> Divider:
    return Divider()


class FieldLabel(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("FieldLabel")


class Pill(QLabel):
    """A small rounded status chip. ``tone`` is neutral | warn | danger | muted."""

    _NAMES = {
        "neutral": "Pill",
        "warn": "PillWarn",
        "danger": "PillDanger",
        "muted": "PillMuted",
    }

    def __init__(self, text: str = "", tone: str = "neutral",
                 parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.set_tone(tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setObjectName(self._NAMES.get(tone, "Pill"))
        self.style().unpolish(self)
        self.style().polish(self)

    def set(self, text: str, tone: str = "neutral") -> None:
        self.setText(text)
        self.set_tone(tone)


class Card(QFrame):
    """A titled panel. Add content with :meth:`add_row` or :meth:`add_widget`."""

    def __init__(
        self,
        title: str,
        *,
        step: Optional[int] = None,
        hint: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("Card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(9)

        header = QWidget(self)
        header.setObjectName("CardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        if step is not None:
            badge = QLabel(str(step), header)
            badge.setObjectName("CardStep")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(badge)

        self.title_label = QLabel(title, header)
        self.title_label.setObjectName("CardTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.header_extra = QHBoxLayout()
        self.header_extra.setContentsMargins(0, 0, 0, 0)
        self.header_extra.setSpacing(6)
        header_layout.addLayout(self.header_extra)

        outer.addWidget(header)

        if hint:
            hint_label = QLabel(hint, self)
            hint_label.setObjectName("CardHint")
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)

        outer.addWidget(Divider(self))

        self.body = QGridLayout()
        self.body.setContentsMargins(0, 2, 0, 0)
        self.body.setHorizontalSpacing(10)
        self.body.setVerticalSpacing(8)
        self.body.setColumnStretch(1, 1)
        outer.addLayout(self.body)

        self.footer = QVBoxLayout()
        self.footer.setContentsMargins(0, 4, 0, 0)
        self.footer.setSpacing(7)
        outer.addLayout(self.footer)

        self._row = 0

    # -- content ------------------------------------------------------------ #

    def add_header_widget(self, widget: QWidget) -> QWidget:
        self.header_extra.addWidget(widget)
        return widget

    def add_row(self, label: str, widget: QWidget) -> QWidget:
        self.body.addWidget(FieldLabel(label), self._row, 0,
                            Qt.AlignmentFlag.AlignVCenter)
        self.body.addWidget(widget, self._row, 1)
        self._row += 1
        return widget

    def add_widget(self, widget: QWidget, *, span: bool = True) -> QWidget:
        if span:
            self.body.addWidget(widget, self._row, 0, 1, 2)
        else:
            self.body.addWidget(widget, self._row, 1)
        self._row += 1
        return widget

    def add_note(self, text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        self.body.addWidget(label, self._row, 0, 1, 2)
        self._row += 1
        return label

    def add_footer(self, widget: QWidget) -> QWidget:
        self.footer.addWidget(widget)
        return widget

    def add_footer_row(self, widgets: Iterable[QWidget], *, spacing: int = 8) -> QWidget:
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for widget in widgets:
            layout.addWidget(widget)
        self.footer.addWidget(holder)
        return holder


class FilePicker(QWidget):
    """A read-only path field with a Browse button."""

    changed = Signal(object)          # Path or None

    def __init__(
        self,
        *,
        caption: str = "Choose a file",
        filters: str = "All files (*)",
        placeholder: str = "no file selected",
        save_mode: bool = False,
        default_suffix: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._caption = caption
        self._filters = filters
        self._save_mode = save_mode
        self._default_suffix = default_suffix
        self._path: Optional[Path] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.field = QLineEdit(self)
        self.field.setReadOnly(True)
        self.field.setPlaceholderText(placeholder)
        self.field.setMinimumWidth(0)
        self.field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.field, 1)

        self.button = QPushButton("Browse…", self)
        self.button.setObjectName("IconButton")
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.button)

    def path(self) -> Optional[Path]:
        return self._path

    def set_path(self, path: Optional[Path | str]) -> None:
        self._path = Path(path) if path else None
        # The name is what identifies the file at a glance; the full path is
        # long enough to be useless in a narrow field, so it goes to the tooltip.
        self.field.setText(self._path.name if self._path else "")
        self.field.setToolTip(str(self._path) if self._path else "")
        self.field.setCursorPosition(0)
        self.changed.emit(self._path)

    def _browse(self) -> None:
        start = str(self._path.parent) if self._path else ""
        if self._save_mode:
            name, _ = QFileDialog.getSaveFileName(
                self, self._caption, start, self._filters
            )
        else:
            name, _ = QFileDialog.getOpenFileName(
                self, self._caption, start, self._filters
            )
        if not name:
            return
        chosen = Path(name)
        if self._save_mode and self._default_suffix and not chosen.name.endswith(
            self._default_suffix
        ):
            chosen = chosen.with_name(chosen.name + self._default_suffix)
        self.set_path(chosen)


class KeyValueList(QWidget):
    """A compact two-column read-out for file/analysis facts."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(12)
        self._layout.setVerticalSpacing(4)
        self._layout.setColumnStretch(1, 1)
        self._rows: dict[str, QLabel] = {}

    def set_items(self, items: Iterable[tuple[str, str]]) -> None:
        self.clear()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        for row, (key, value) in enumerate(items):
            key_label = QLabel(key, self)
            key_label.setObjectName("Muted")
            value_label = QLabel(str(value), self)
            value_label.setObjectName("Mono")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._layout.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            self._layout.addWidget(value_label, row, 1)
            self._rows[key] = value_label

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
