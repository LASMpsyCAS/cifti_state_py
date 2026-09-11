"""PySide6 interface.

Importing this package pulls in PySide6, so the analysis side of
``cifti_state`` never imports it.  Start the window with::

    cifti-state-gui
    cifti-state-gui path/to/map.dscalar.nii

The window is a thin shell: every button calls the same functions the CLI
calls, on a worker thread, with the progress and cancel hooks the core layer
already provides.
"""

from .theme import PALETTE, stylesheet

__all__ = ["PALETTE", "stylesheet", "main", "run"]


def main(argv=None) -> int:
    """Entry point for the ``cifti-state-gui`` console script."""
    from .app import main as _main

    return _main(argv)


def run(*args, **kwargs) -> int:
    from .app import run as _run

    return _run(*args, **kwargs)
