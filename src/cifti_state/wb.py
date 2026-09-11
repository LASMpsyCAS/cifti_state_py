"""Thin, auditable wrapper around ``wb_command`` and ``wb_view``.

Core analysis never calls into this module -- clustering, thresholding, peak
finding and annotation are pure NumPy/SciPy, so the package is fully usable
without Connectome Workbench installed.  Workbench is used for the data
operations it is genuinely better at (smoothing, resampling, structure
separation, label statistics) and for opening results interactively.

Every invocation is logged with its full argument list so a failing call can be
copied straight into a terminal.  ``shell=True`` is never used, which keeps
paths containing spaces (``C:\\Program Files\\workbench``) safe.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import ConfigError, Settings, current_platform
from .logging_setup import get_logger

log = get_logger(__name__)

__all__ = ["WbResult", "WorkbenchError", "run_wb", "probe", "open_in_wb_view",
           "smooth_cifti", "cifti_separate", "surface_vertex_areas"]


class WorkbenchError(RuntimeError):
    """A ``wb_command`` call returned a non-zero exit status."""

    def __init__(self, result: "WbResult"):
        self.result = result
        super().__init__(
            f"wb_command failed (exit {result.returncode})\n"
            f"  command: {result.command_line}\n"
            f"  stderr : {result.stderr.strip()[:2000]}"
        )


@dataclass
class WbResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    outputs: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def command_line(self) -> str:
        return " ".join(_quote(part) for part in self.command)


def _executable(settings: Settings, which: str) -> Path:
    resolved = settings.workbench.resolve()
    path = getattr(resolved, which)
    if path is None:
        raise ConfigError(
            f"{which} not found. Set workbench.{which} in the configuration file "
            f"or put it on PATH."
        )
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{which} does not exist: {path}")
    return path


def run_wb(
    args: Sequence[str | Path],
    settings: Settings,
    *,
    check: bool = True,
    timeout: Optional[float] = None,
    outputs: Optional[Sequence[Path]] = None,
) -> WbResult:
    """Run ``wb_command`` with *args* (the executable itself is prepended)."""
    exe = _executable(settings, "wb_command")
    command = [str(exe)] + [str(a) for a in args]
    log.info("wb_command %s", " ".join(_quote(a) for a in command[1:]))

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = WbResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration=time.perf_counter() - started,
        outputs=[Path(p) for p in (outputs or [])],
    )
    if result.stderr.strip():
        log.debug("wb_command stderr: %s", result.stderr.strip()[:1000])
    if check and not result.ok:
        raise WorkbenchError(result)
    return result


def probe(settings: Settings) -> dict[str, Any]:
    """Report whether Workbench is available and which version."""
    info: dict[str, Any] = {
        "platform": current_platform(),
        "wb_command": None,
        "wb_view": None,
        "version": None,
        "available": False,
    }
    resolved = settings.workbench.resolve()
    info["wb_command"] = str(resolved.wb_command) if resolved.wb_command else None
    info["wb_view"] = str(resolved.wb_view) if resolved.wb_view else None
    if resolved.wb_command and Path(resolved.wb_command).exists():
        try:
            result = run_wb(["-version"], settings, check=False, timeout=30)
            info["available"] = result.ok
            info["version"] = (result.stdout or result.stderr).strip().splitlines()[:4]
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("could not run wb_command -version: %s", exc)
    return info


def open_in_wb_view(
    files: Sequence[Path | str], settings: Settings, *, wait: bool = False
) -> subprocess.Popen | None:
    """Launch ``wb_view`` on the given files. Returns the process handle."""
    exe = _executable(settings, "wb_view")
    command = [str(exe)] + [str(f) for f in files]
    log.info("wb_view %s", " ".join(_quote(a) for a in command[1:]))
    kwargs: dict[str, Any] = {}
    if current_platform() == "windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, **kwargs)
    if wait:
        process.wait()
        return None
    return process


# --------------------------------------------------------------------------- #
# semantic helpers
# --------------------------------------------------------------------------- #


def smooth_cifti(
    in_path: Path | str,
    out_path: Path | str,
    settings: Settings,
    *,
    surface_kernel: float = 2.0,
    volume_kernel: float = 2.0,
    direction: str = "COLUMN",
    surface_kind: str = "midthickness",
    extra: Sequence[str] = (),
) -> WbResult:
    """``wb_command -cifti-smoothing`` using the configured template surfaces."""
    left = settings.resources.surface_path("left", surface_kind)
    right = settings.resources.surface_path("right", surface_kind)
    args = [
        "-cifti-smoothing", str(in_path),
        str(surface_kernel), str(volume_kernel), direction, str(out_path),
        "-left-surface", str(left),
        "-right-surface", str(right),
        *extra,
    ]
    return run_wb(args, settings, outputs=[Path(out_path)])


def cifti_separate(
    in_path: Path | str,
    settings: Settings,
    *,
    left_metric: Optional[Path | str] = None,
    right_metric: Optional[Path | str] = None,
    direction: str = "COLUMN",
) -> WbResult:
    """``wb_command -cifti-separate`` into per-hemisphere metric files."""
    args: list[str] = ["-cifti-separate", str(in_path), direction]
    outputs: list[Path] = []
    if left_metric:
        args += ["-metric", "CORTEX_LEFT", str(left_metric)]
        outputs.append(Path(left_metric))
    if right_metric:
        args += ["-metric", "CORTEX_RIGHT", str(right_metric)]
        outputs.append(Path(right_metric))
    if not outputs:
        raise ValueError("cifti_separate needs at least one output metric path")
    return run_wb(args, settings, outputs=outputs)


def surface_vertex_areas(
    surface_path: Path | str, out_path: Path | str, settings: Settings
) -> WbResult:
    """``wb_command -surface-vertex-areas``.

    The pure-Python :func:`cifti_state.io.surface.vertex_areas` computes the
    same quantity; this exists for cross-checking.
    """
    return run_wb(
        ["-surface-vertex-areas", str(surface_path), str(out_path)],
        settings,
        outputs=[Path(out_path)],
    )


def _quote(value: str) -> str:
    text = str(value)
    return f'"{text}"' if " " in text else text
