"""Application entry point: ``cifti-state-gui``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from ..config import ConfigError, load_settings
from ..logging_setup import configure_logging, get_logger

log = get_logger(__name__)

__all__ = ["main", "run"]


def run(
    config: Optional[Path] = None,
    *,
    open_path: Optional[Path] = None,
    log_level: str = "INFO",
) -> int:
    """Create the application and show the main window."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..fonts import apply_to_matplotlib, apply_to_qt, configure_stdio

    configure_stdio()
    configure_logging(log_level)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("cifti_state")
    app.setOrganizationName("cifti_state")
    app.setStyle("Fusion")

    # The configuration is read before anything is drawn, because it can pin
    # the font family and size -- and a font applied after the widgets exist
    # leaves half the interface at the old metrics.
    try:
        settings = load_settings(config)
    except ConfigError as exc:
        QMessageBox.critical(
            None,
            "Configuration problem",
            f"{exc}\n\nRun 'cifti-state config --init-machine' to create a "
            f"profile for this machine.",
        )
        return 2

    # Resolve the fonts here rather than inheriting whatever the desktop uses,
    # and give Qt and matplotlib the same answer.
    pixel_size = int(settings.interface.font_size_px)
    choice = apply_to_qt(app, pixel_size=pixel_size, settings=settings)
    apply_to_matplotlib(choice)

    from .theme import PALETTE, stylesheet

    app.setStyleSheet(stylesheet(PALETTE, fonts=choice, font_size_px=pixel_size))

    from .main_window import MainWindow

    window = MainWindow(settings)
    window.show()

    if open_path is not None:
        window.input_panel.set_path(Path(open_path))
        window._load_map(Path(open_path))

    return app.exec()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cifti-state-gui",
        description="Graphical front end for cifti_state",
    )
    parser.add_argument(
        "input", nargs="?", type=Path, help="a map to open on start-up"
    )
    parser.add_argument("--config", type=Path, help="configuration file to use")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)
    return run(args.config, open_path=args.input, log_level=args.log_level)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
