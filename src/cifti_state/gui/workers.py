"""Background work.

Every long operation runs on a :class:`QThread` so the window stays responsive.
The core functions already take ``progress`` and ``cancel``, so a worker is a
thin adapter: it forwards progress as a Qt signal and sets a
:class:`threading.Event` when the user cancels.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..logging_setup import get_logger
from ..types import OperationCancelled

log = get_logger(__name__)

__all__ = ["Job", "JobRunner"]


class Job(QObject):
    """Runs ``function(progress=..., cancel=...)`` and reports the outcome.

    The function is called with the keyword arguments it declares support for:
    ``progress`` and ``cancel`` are injected only when *accepts_progress* /
    *accepts_cancel* say so, which keeps simple callables simple.
    """

    started = Signal()
    progressed = Signal(float, str)
    finished = Signal(object)        # the return value
    failed = Signal(str, str)        # message, traceback
    cancelled = Signal()
    done = Signal()                  # always, after any of the three above

    def __init__(
        self,
        function: Callable[..., Any],
        *,
        accepts_progress: bool = True,
        accepts_cancel: bool = True,
        description: str = "",
    ):
        super().__init__()
        self._function = function
        self._accepts_progress = accepts_progress
        self._accepts_cancel = accepts_cancel
        self.description = description
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        self.started.emit()
        kwargs: dict[str, Any] = {}
        if self._accepts_progress:
            kwargs["progress"] = self._emit_progress
        if self._accepts_cancel:
            kwargs["cancel"] = self.cancel_event
        try:
            result = self._function(**kwargs)
        except OperationCancelled:
            log.info("%s cancelled", self.description or "job")
            self.cancelled.emit()
        except Exception as exc:  # surfaced in the UI, never swallowed
            detail = traceback.format_exc()
            log.error("%s failed: %s", self.description or "job", exc)
            log.debug(detail)
            self.failed.emit(str(exc), detail)
        else:
            self.finished.emit(result)
        finally:
            self.done.emit()

    def _emit_progress(self, fraction: float, message: str) -> None:
        self.progressed.emit(float(fraction), str(message))


class JobRunner(QObject):
    """Owns one running job at a time and keeps its thread alive until done.

    The runner itself lives in the GUI thread, and every one of the job's
    signals is routed through a slot *on the runner* before reaching the
    caller's callbacks.  That matters: connecting a worker signal straight to a
    plain Python callable gives a **direct** connection, so the callback would
    run on the worker thread — and it would be touching widgets from there.
    Going through the runner makes the connection cross thread affinities,
    which Qt queues, so callbacks always arrive on the GUI thread.
    """

    busy_changed = Signal(bool)
    progressed = Signal(float, str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread: Optional[QThread] = None
        self._job: Optional[Job] = None
        self._on_finished: Optional[Callable[[Any], None]] = None
        self._on_failed: Optional[Callable[[str, str], None]] = None
        self._on_cancelled: Optional[Callable[[], None]] = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(
        self,
        job: Job,
        *,
        on_finished: Optional[Callable[[Any], None]] = None,
        on_failed: Optional[Callable[[str, str], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Start *job*. Returns False if something is already running."""
        if self.busy:
            log.warning("a job is already running; ignoring %s", job.description)
            return False

        thread = QThread()
        job.moveToThread(thread)
        self._thread, self._job = thread, job
        self._on_finished = on_finished
        self._on_failed = on_failed
        self._on_cancelled = on_cancelled

        thread.started.connect(job.run)

        # Auto connections between objects with different thread affinities are
        # queued by Qt, so each of these slots runs in the GUI thread.
        job.progressed.connect(self._handle_progress)
        job.finished.connect(self._handle_finished)
        job.failed.connect(self._handle_failed)
        job.cancelled.connect(self._handle_cancelled)

        job.done.connect(thread.quit)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear)

        self.busy_changed.emit(True)
        thread.start()
        return True

    # -- GUI-thread slots ---------------------------------------------------- #

    def _handle_progress(self, fraction: float, message: str) -> None:
        self.progressed.emit(fraction, message)

    def _handle_finished(self, result: Any) -> None:
        callback, self._on_finished = self._on_finished, None
        if callback is not None:
            callback(result)

    def _handle_failed(self, message: str, detail: str) -> None:
        callback, self._on_failed = self._on_failed, None
        if callback is not None:
            callback(message, detail)

    def _handle_cancelled(self) -> None:
        callback, self._on_cancelled = self._on_cancelled, None
        if callback is not None:
            callback()

    def cancel(self) -> None:
        if self._job is not None:
            self._job.request_cancel()

    def wait(self, timeout_ms: int = 5000) -> None:
        """Block until the current job finishes -- used when closing the window."""
        thread = self._thread
        if thread is not None and thread.isRunning():
            self.cancel()
            thread.quit()
            thread.wait(timeout_ms)

    def _clear(self) -> None:
        self._thread = None
        self._job = None
        self._on_finished = None
        self._on_failed = None
        self._on_cancelled = None
        self.busy_changed.emit(False)
