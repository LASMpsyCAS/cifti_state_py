"""Central logging configuration.

Library code never calls ``print``; it logs through ``logging.getLogger(__name__)``.
The CLI calls :func:`configure_logging`; a GUI attaches its own handler to the
``cifti_state`` logger instead.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

ROOT_LOGGER_NAME = "cifti_state"

_DEFAULT_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the package root."""
    if name.startswith(ROOT_LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def configure_logging(
    level: str | int = "INFO",
    *,
    log_file: Optional[Path] = None,
    fmt: str = _DEFAULT_FORMAT,
) -> logging.Logger:
    """Attach a stream handler (and optionally a file handler) to the package root."""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(fmt, datefmt=_DATE_FORMAT)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Keep the library quiet unless the host application configures logging.
logging.getLogger(ROOT_LOGGER_NAME).addHandler(logging.NullHandler())
