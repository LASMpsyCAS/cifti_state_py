"""The log view: the package's own logging, shown in the window."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...logging_setup import ROOT_LOGGER_NAME
from ..theme import PALETTE

__all__ = ["LogPanel", "QtLogHandler"]

_COLOURS = {
    "DEBUG": PALETTE.faint,
    "INFO": PALETTE.ink_soft,
    "WARNING": PALETTE.warn,
    "ERROR": PALETTE.danger,
    "CRITICAL": PALETTE.danger,
}


class QtLogHandler(logging.Handler, QObject):
    """A logging handler that emits a Qt signal, so records cross threads safely."""

    record_emitted = Signal(str, str)      # level name, formatted message

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.record_emitted.emit(record.levelname, self.format(record))
        except RuntimeError:
            pass          # the widget went away during shutdown


class LogPanel(QWidget):
    """A read-only, colour-coded log with a clear button."""

    def __init__(self, parent: Optional[QWidget] = None, *, max_lines: int = 2000):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.view = QPlainTextEdit(self)
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(max_lines)
        layout.addWidget(self.view, 1)

        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("IconButton")
        self.clear_button.clicked.connect(self.view.clear)
        button_layout.addWidget(self.clear_button)
        layout.addWidget(buttons)

        self.handler = QtLogHandler()
        self.handler.record_emitted.connect(self.append)

    def attach(self, level: int = logging.INFO) -> None:
        """Route the package logger into this widget."""
        logger = logging.getLogger(ROOT_LOGGER_NAME)
        logger.setLevel(min(logger.level or logging.INFO, level))
        self.handler.setLevel(level)
        logger.addHandler(self.handler)

    def detach(self) -> None:
        logging.getLogger(ROOT_LOGGER_NAME).removeHandler(self.handler)

    def append(self, level: str, message: str) -> None:
        colour = _COLOURS.get(level, PALETTE.ink_soft)
        weight = "600" if level in ("ERROR", "CRITICAL", "WARNING") else "400"
        safe = (
            message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.view.appendHtml(
            f'<span style="color:{colour}; font-weight:{weight};">{safe}</span>'
        )
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
