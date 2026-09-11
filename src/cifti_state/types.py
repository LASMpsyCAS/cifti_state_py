"""Shared type aliases and small protocol definitions.

Kept dependency-free on purpose: every other module may import this one.
"""

from __future__ import annotations

import threading
from typing import Callable, Literal, Optional, Protocol

__all__ = [
    "ProgressFn",
    "CancelToken",
    "Hemisphere",
    "Direction",
    "Statistic",
    "OperationCancelled",
    "check_cancelled",
    "report_progress",
]

#: ``progress(fraction, message)`` where *fraction* is in ``[0, 1]``.
ProgressFn = Callable[[float, str], None]

#: Anything with ``.is_set()``. ``threading.Event`` satisfies this.
CancelToken = threading.Event

Hemisphere = Literal["left", "right"]
Direction = Literal["positive", "negative", "two_sided"]
Statistic = Literal["z", "t", "other"]


class SupportsIsSet(Protocol):
    def is_set(self) -> bool: ...


class OperationCancelled(RuntimeError):
    """Raised from inside a long running routine when its cancel token is set."""


def check_cancelled(cancel: Optional[SupportsIsSet]) -> None:
    """Raise :class:`OperationCancelled` if *cancel* has been signalled."""
    if cancel is not None and cancel.is_set():
        raise OperationCancelled("operation cancelled by caller")


def report_progress(
    progress: Optional[ProgressFn], fraction: float, message: str
) -> None:
    """Call *progress* defensively.

    A GUI callback that raises must never take down the computation, so
    exceptions from the callback are swallowed.
    """
    if progress is None:
        return
    try:
        progress(max(0.0, min(1.0, float(fraction))), message)
    except Exception:  # pragma: no cover - callback is caller supplied
        pass
