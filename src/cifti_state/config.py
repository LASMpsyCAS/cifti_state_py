"""Configuration: platform detection, YAML loading, path resolution.

Nothing in :mod:`cifti_state.core` reads configuration.  A :class:`Settings`
instance is created once (by the CLI, a notebook, or the GUI) and passed
explicitly to the IO and pipeline layers.

**One configuration file per machine.**  Workbench, the template pack and the
data live in different places on every machine, so each one gets its own file
under ``configs/machines/<hostname>.yaml``.  It is picked up automatically from
the hostname, and it only needs to state what differs, because ``extends:``
pulls in the platform template:

.. code-block:: yaml

    extends: ../settings.windows.yaml
    machine:
      name: laptop-ghlod870
    workbench:
      wb_command: D:/workbench-windows64-v1.5.0/workbench/bin_windows64/wb_command.exe

Resolution order, first hit wins::

    1. --config PATH                          explicit
    2. $CIFTI_STATE_CONFIG                    environment
    3. configs/machines/<hostname>.yaml       this machine
    4. <user config dir>/settings.yaml        this user
    5. configs/settings.<platform>.yaml       platform fallback

``cifti-state config --show-chain`` prints the chain and marks the winner;
``cifti-state config --init-machine`` scaffolds a file for the current machine.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from .logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "Settings",
    "InterfaceSettings",
    "MachineInfo",
    "WorkbenchSettings",
    "ResourceSettings",
    "DefaultSettings",
    "RenderSettings",
    "RuntimeSettings",
    "ConfigError",
    "load_settings",
    "default_config_path",
    "machine_config_path",
    "machine_config_dir",
    "user_config_path",
    "current_platform",
    "current_machine",
    "resolution_chain",
    "init_machine_config",
]

PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE_ROOT.parent.parent / "configs"

#: How deep an ``extends:`` chain may go before we assume a cycle.
MAX_EXTENDS_DEPTH = 8


class ConfigError(RuntimeError):
    """Raised when a configuration file is missing, malformed or inconsistent."""


def current_platform() -> str:
    """Return ``"windows"`` or ``"linux"`` (macOS is treated as linux-like)."""
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def current_machine() -> str:
    """A filesystem-safe identifier for this machine.

    ``$CIFTI_STATE_MACHINE`` overrides it, which is what you want on a cluster
    where every compute node has a different hostname but the same paths.
    """
    name = os.environ.get("CIFTI_STATE_MACHINE") or socket.gethostname()
    name = name.split(".")[0]                       # strip the domain
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return slug or "unknown"


def machine_config_dir() -> Path:
    """Directory holding the per-machine configuration files."""
    return CONFIG_DIR / "machines"


def machine_config_path(machine: Optional[str] = None) -> Path:
    """Path of this machine's configuration file (whether or not it exists)."""
    return machine_config_dir() / f"{machine or current_machine()}.yaml"


def default_config_path(plat: Optional[str] = None) -> Path:
    """Path to the bundled platform template for *plat*."""
    plat = plat or current_platform()
    if plat == "macos":
        plat = "linux"
    return CONFIG_DIR / f"settings.{plat}.yaml"


def user_config_path() -> Path:
    """Per-user override location."""
    if current_platform() == "windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "cifti_state" / "settings.yaml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "cifti_state" / "settings.yaml"


def resolution_chain(explicit: Optional[Path | str] = None) -> list[dict[str, Any]]:
    """The candidate configuration files, in priority order.

    Each entry is ``{"source", "path", "exists"}``.  The first entry whose
    ``exists`` is True is the one :func:`load_settings` will use.
    """
    chain: list[dict[str, Any]] = []
    if explicit:
        chain.append({"source": "--config", "path": Path(explicit)})
    env = os.environ.get("CIFTI_STATE_CONFIG")
    if env:
        chain.append({"source": "$CIFTI_STATE_CONFIG", "path": Path(env)})
    chain.append(
        {"source": f"machine ({current_machine()})", "path": machine_config_path()}
    )
    chain.append({"source": "user", "path": user_config_path()})
    chain.append(
        {"source": f"platform ({current_platform()})", "path": default_config_path()}
    )
    for entry in chain:
        entry["exists"] = entry["path"].exists()
    return chain


# --------------------------------------------------------------------------- #
# dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class MachineInfo:
    """Which machine profile is in force, for display and troubleshooting."""

    name: str = ""
    description: str = ""
    notes: str = ""

    def label(self) -> str:
        return self.description or self.name or "(unnamed)"


@dataclass
class WorkbenchSettings:
    """Location of the Connectome Workbench executables.

    Both may be empty; they are then looked up on ``PATH``.  Core analysis
    never needs them -- clustering, thresholding, peak finding and annotation
    are pure NumPy/SciPy.  They are required only for the ``wb_command``
    helpers and for launching ``wb_view``.
    """

    wb_command: Optional[Path] = None
    wb_view: Optional[Path] = None

    def resolve(self) -> "WorkbenchSettings":
        exe = ".exe" if current_platform() == "windows" else ""
        cmd = self.wb_command
        view = self.wb_view
        if cmd is None:
            found = shutil.which(f"wb_command{exe}") or shutil.which("wb_command")
            cmd = Path(found) if found else None
        if view is None:
            found = shutil.which(f"wb_view{exe}") or shutil.which("wb_view")
            view = Path(found) if found else None
        return replace(self, wb_command=cmd, wb_view=view)


@dataclass
class ResourceSettings:
    """Template surfaces, neighbour tables and atlases.

    ``surfaces`` names the surfaces of the *working* mesh explicitly -- the one
    ``defaults.mesh`` points at, and the only one most analyses ever touch.
    Every other mesh is found by convention instead: ``mesh_dirs`` lists the
    directories holding template packs, and
    :class:`cifti_state.mesh.MeshLibrary` recognises the standard HCP and
    FreeSurfer filenames inside them.  That keeps a configuration file short
    while still letting a study work at several densities;
    ``mesh_files`` is the escape hatch for a file whose name does not follow
    the conventions.
    """

    root: Optional[Path] = None
    surfaces: dict[str, dict[str, str]] = field(default_factory=dict)
    neighbors: dict[str, dict[str, str]] = field(default_factory=dict)
    underlays: dict[str, str] = field(default_factory=dict)
    atlas_dir: Optional[Path] = None
    atlas_csv_dir: Optional[Path] = None
    #: Directories to search for mesh templates (spheres, area metrics, ROIs,
    #: anatomical surfaces). Empty means "root and its siblings".
    mesh_dirs: list[Path] = field(default_factory=list)
    #: ``{mesh: {role: {hemi: path}}}`` overrides for oddly-named files.
    mesh_files: dict[str, dict[str, Any]] = field(default_factory=dict)

    def surface_path(self, hemi: str, kind: str) -> Path:
        """Absolute path of a template surface, e.g. ``("left", "inflated")``."""
        hemi = _normalise_hemi(hemi)
        try:
            name = self.surfaces[hemi][kind]
        except KeyError as exc:
            available = sorted(self.surfaces.get(hemi, {}))
            raise ConfigError(
                f"no {hemi} surface named {kind!r} in the configuration "
                f"(available: {available})"
            ) from exc
        return self._resolve(name)

    def underlay_path(self, name: str) -> Path:
        """Absolute path of a greyscale underlay, e.g. ``"sulc"``."""
        try:
            filename = self.underlays[str(name)]
        except KeyError as exc:
            raise ConfigError(
                f"no underlay named {name!r} in the configuration "
                f"(available: {sorted(self.underlays)})"
            ) from exc
        return self._resolve(filename)

    def neighbor_path(self, mesh: str, hemi: str) -> Path:
        """Absolute path of a neighbour table, e.g. ``("32k", "left")``.

        Configuration files have always keyed these by a bare density
        (``"32k"``), while the pipeline now names meshes in full
        (``"fsLR:32k"``).  Both spellings are accepted, so no existing
        configuration has to change.
        """
        hemi = _normalise_hemi(hemi)
        for key in _mesh_keys(mesh):
            entry = self.neighbors.get(key)
            if entry and hemi in entry:
                return self._resolve(entry[hemi])
        raise ConfigError(
            f"no neighbour table for mesh {mesh!r} hemisphere {hemi!r}; "
            f"configured meshes: {sorted(self.neighbors)}"
        )

    def _resolve(self, name: str) -> Path:
        p = Path(name)
        if _is_absolute(name):
            return p
        if self.root is None:
            raise ConfigError(
                f"resources.root is not set, cannot resolve relative path {name!r}"
            )
        return self.root / p


@dataclass
class ThresholdDefaults:
    method: str = "fdr"          # fixed | fdr | percentile
    value: Optional[float] = None  # used when method == "fixed"
    q: float = 0.05              # used when method == "fdr"
    percentile: float = 95.0     # used when method == "percentile"


@dataclass
class DefaultSettings:
    mesh: str = "32k"
    statistic: str = "z"          # z | t | other
    df: Optional[float] = None    # required when statistic == "t"
    direction: str = "positive"   # positive | negative | two_sided
    threshold: ThresholdDefaults = field(default_factory=ThresholdDefaults)
    extent: int = 20
    atlas: str = "Glasser_2016"
    neighbor_source: str = "txt"  # txt | surface
    legacy_mode: bool = True      # reproduce the original MATLAB behaviour exactly


@dataclass
class RenderSettings:
    backend: str = "offscreen"     # offscreen | interactive
    surface: str = "inflated"
    layout: str = "grid_4"
    colormap: str = "workbench_hot"
    dpi: int = 300
    format: str = "png"
    size: tuple[int, int] = (1200, 800)
    zoom: float = 1.25
    label_size: int = 12
    underlay: str = "sulc"         # sulc | curvature | none
    underlay_style: str = "binary" # binary | continuous
    underlay_orient: str = "auto"  # auto | negative | positive
                                   # which sign of the underlay is a sulcus;
                                   # auto measures it against the surface
    underlay_dark: float = 0.38
    underlay_light: float = 0.72


@dataclass
class InterfaceSettings:
    """Typography, pinned in configuration rather than left to the desktop.

    ``ui_font`` / ``mono_font`` override the automatic resolution outright --
    set them when a machine renders digits badly with whatever was picked.
    """

    ui_font: str = ""              # "" = resolve automatically
    mono_font: str = ""
    font_size_px: int = 13         # pixels, so DPI settings cannot rescale it
    numeric_font: str = "mono"     # mono | ui -- what number fields use


@dataclass
class RuntimeSettings:
    tmp_dir: Optional[Path] = None
    n_jobs: int = 4
    log_level: str = "INFO"


@dataclass
class Settings:
    """Everything the IO / pipeline / viz layers need to find files."""

    workbench: WorkbenchSettings = field(default_factory=WorkbenchSettings)
    resources: ResourceSettings = field(default_factory=ResourceSettings)
    defaults: DefaultSettings = field(default_factory=DefaultSettings)
    render: RenderSettings = field(default_factory=RenderSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    interface: InterfaceSettings = field(default_factory=InterfaceSettings)
    machine: MachineInfo = field(default_factory=MachineInfo)
    source_path: Optional[Path] = None
    inherited_from: list[Path] = field(default_factory=list)

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        source_path: Optional[Path] = None,
        base_dir: Optional[Path] = None,
        inherited_from: Optional[list[Path]] = None,
    ) -> "Settings":
        base = base_dir or (Path(source_path).resolve().parent if source_path else None)

        wb_raw = dict(data.get("workbench") or {})
        workbench = WorkbenchSettings(
            wb_command=_opt_path(wb_raw.get("wb_command"), base),
            wb_view=_opt_path(wb_raw.get("wb_view"), base),
        )

        res_raw = dict(data.get("resources") or {})
        resources = ResourceSettings(
            root=_opt_path(res_raw.get("root"), base),
            surfaces={
                _normalise_hemi(k): dict(v)
                for k, v in (res_raw.get("surfaces") or {}).items()
            },
            neighbors={
                str(k): {_normalise_hemi(hk): hv for hk, hv in (v or {}).items()}
                for k, v in (res_raw.get("neighbors") or {}).items()
            },
            underlays={
                str(k): str(v) for k, v in (res_raw.get("underlays") or {}).items()
                if v
            },
            atlas_dir=_opt_path(res_raw.get("atlas_dir"), base),
            atlas_csv_dir=_opt_path(res_raw.get("atlas_csv_dir"), base),
            mesh_dirs=[
                p for p in (
                    _opt_path(d, base) for d in (res_raw.get("mesh_dirs") or [])
                ) if p is not None
            ],
            mesh_files={
                str(mesh): {
                    str(role): (
                        {_normalise_hemi(hk): str(_opt_path(hv, base))
                         for hk, hv in value.items() if hv}
                        if isinstance(value, Mapping) else str(_opt_path(value, base))
                    )
                    for role, value in (roles or {}).items() if value
                }
                for mesh, roles in (res_raw.get("mesh_files") or {}).items()
            },
        )
        if resources.atlas_dir is None:
            resources.atlas_dir = resources.root

        def_raw = dict(data.get("defaults") or {})
        thr_raw = dict(def_raw.get("threshold") or {})
        defaults = DefaultSettings(
            mesh=str(def_raw.get("mesh", "32k")),
            statistic=str(def_raw.get("statistic", "z")),
            df=_opt_float(def_raw.get("df")),
            direction=str(def_raw.get("direction", "positive")),
            threshold=ThresholdDefaults(
                method=str(thr_raw.get("method", "fdr")),
                value=_opt_float(thr_raw.get("value")),
                q=float(thr_raw.get("q", 0.05)),
                percentile=float(thr_raw.get("percentile", 95.0)),
            ),
            extent=int(def_raw.get("extent", 20)),
            atlas=str(def_raw.get("atlas", "Glasser_2016")),
            neighbor_source=str(def_raw.get("neighbor_source", "txt")),
            legacy_mode=bool(def_raw.get("legacy_mode", True)),
        )

        ren_raw = dict(data.get("render") or {})
        size = ren_raw.get("size", (1200, 800))
        render = RenderSettings(
            backend=str(ren_raw.get("backend", "offscreen")),
            surface=str(ren_raw.get("surface", "inflated")),
            layout=str(ren_raw.get("layout", "grid_4")),
            colormap=str(ren_raw.get("colormap", "workbench_hot")),
            dpi=int(ren_raw.get("dpi", 300)),
            format=str(ren_raw.get("format", "png")),
            size=(int(size[0]), int(size[1])),
            zoom=float(ren_raw.get("zoom", 1.25)),
            label_size=int(ren_raw.get("label_size", 12)),
            underlay=str(ren_raw.get("underlay", "sulc") or "none"),
            underlay_style=str(ren_raw.get("underlay_style", "binary")),
            underlay_orient=str(ren_raw.get("underlay_orient", "auto") or "auto"),
            underlay_dark=float(ren_raw.get("underlay_dark", 0.38)),
            underlay_light=float(ren_raw.get("underlay_light", 0.72)),
        )

        run_raw = dict(data.get("runtime") or {})
        runtime = RuntimeSettings(
            tmp_dir=_opt_path(run_raw.get("tmp_dir"), base),
            n_jobs=int(run_raw.get("n_jobs", 4)),
            log_level=str(run_raw.get("log_level", "INFO")),
        )

        ui_raw = dict(data.get("interface") or {})
        interface = InterfaceSettings(
            ui_font=str(ui_raw.get("ui_font", "") or ""),
            mono_font=str(ui_raw.get("mono_font", "") or ""),
            font_size_px=int(ui_raw.get("font_size_px", 13)),
            numeric_font=str(ui_raw.get("numeric_font", "mono")),
        )

        machine_raw = dict(data.get("machine") or {})
        machine = MachineInfo(
            name=str(machine_raw.get("name", "")),
            description=str(machine_raw.get("description", "")),
            notes=str(machine_raw.get("notes", "")),
        )

        return cls(
            workbench=workbench,
            resources=resources,
            defaults=defaults,
            render=render,
            runtime=runtime,
            interface=interface,
            machine=machine,
            source_path=Path(source_path) if source_path else None,
            inherited_from=list(inherited_from or []),
        )

    # -- validation --------------------------------------------------------- #

    #: Surfaces the pipeline actually needs. ``midthickness`` gives cluster
    #: areas and peak coordinates; the render surface is what figures are drawn
    #: on. Everything else in ``resources.surfaces`` is optional.
    def required_surfaces(self) -> tuple[str, ...]:
        return tuple({"midthickness", self.render.surface})

    def advisories(self) -> list[str]:
        """Non-blocking notes: things that are absent but not needed to run."""
        notes: list[str] = []
        required = self.required_surfaces()
        for hemi in ("left", "right"):
            for kind in self.resources.surfaces.get(hemi, {}):
                if kind in required:
                    continue
                try:
                    p = self.resources.surface_path(hemi, kind)
                except ConfigError:
                    continue
                if not p.exists():
                    notes.append(f"optional surface {hemi}/{kind} not present: {p}")
        return notes

    def validate(self, *, require_workbench: bool = False) -> list[str]:
        """Return a list of human readable, blocking problems.

        Optional resources (surfaces the current settings do not use, the temp
        directory, Workbench when it is not required) are reported by
        :meth:`advisories` instead, so ``check`` stays quiet about things that
        do not stop an analysis.
        """
        problems: list[str] = []

        root = self.resources.root
        if root is None:
            problems.append("resources.root is not set")
        elif not root.exists():
            problems.append(f"resources.root does not exist: {root}")

        required = self.required_surfaces()
        for hemi in ("left", "right"):
            kinds = self.resources.surfaces.get(hemi, {})
            if not kinds:
                problems.append(f"no {hemi} surfaces configured")
                continue
            for kind in required:
                try:
                    p = self.resources.surface_path(hemi, kind)
                except ConfigError as exc:
                    problems.append(str(exc))
                    continue
                if not p.exists():
                    problems.append(f"missing required surface {hemi}/{kind}: {p}")

        mesh = self.defaults.mesh
        if self.defaults.neighbor_source == "txt":
            for hemi in ("left", "right"):
                try:
                    p = self.resources.neighbor_path(mesh, hemi)
                except ConfigError as exc:
                    problems.append(str(exc))
                    continue
                if not p.exists():
                    problems.append(f"missing neighbour table {hemi}: {p}")

        if self.resources.atlas_dir and not self.resources.atlas_dir.exists():
            problems.append(f"atlas_dir does not exist: {self.resources.atlas_dir}")

        if self.defaults.statistic == "t" and self.defaults.df is None:
            problems.append(
                "defaults.statistic is 't' but defaults.df is not set; "
                "a t map cannot be converted to p without degrees of freedom"
            )

        if require_workbench:
            wb = self.workbench.resolve()
            if wb.wb_command is None or not Path(wb.wb_command).exists():
                problems.append("wb_command not found (set workbench.wb_command)")

        return problems

    def require_valid(self, *, require_workbench: bool = False) -> None:
        problems = self.validate(require_workbench=require_workbench)
        if problems:
            raise ConfigError(
                "configuration problems:\n  - " + "\n  - ".join(problems)
            )

    # -- persistence -------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source_path", None)
        data.pop("inherited_from", None)
        return _stringify_paths(data)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False, allow_unicode=True)
        log.info("wrote configuration to %s", path)
        return path

    def temp_dir(self) -> Path:
        import tempfile

        d = self.runtime.tmp_dir or Path(tempfile.gettempdir()) / "cifti_state"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- meshes ------------------------------------------------------------- #

    def mesh_library(self):
        """The template files found for every mesh, built once and cached.

        Imported lazily: :mod:`cifti_state.mesh` reaches back into this module
        through :mod:`cifti_state.wb`, and a module-level import here would
        close that loop.
        """
        library = getattr(self, "_mesh_library", None)
        if library is None:
            from .mesh.templates import MeshLibrary

            library = MeshLibrary.from_settings(self)
            object.__setattr__(self, "_mesh_library", library)
        return library

    def mesh_of(self, n_vertices: int):
        """Which mesh a hemisphere of *n_vertices* vertices is on.

        ``defaults.mesh`` breaks the ties -- 10242 vertices is both fs_LR 10k
        and fsaverage5, and the configured working mesh is the best evidence
        available about which family the study is in.
        """
        from .mesh.spaces import identify_mesh

        return identify_mesh(n_vertices, prefer=self.defaults.mesh)

    def surface_for(
        self, hemi: str, kind: str, mesh: Optional[str] = None
    ) -> Path:
        """A template surface, for the working mesh or any other.

        ``resources.surfaces`` names the working mesh's surfaces explicitly and
        wins whenever it applies, so an existing configuration keeps behaving
        exactly as it did.  Any other mesh is resolved through
        :class:`~cifti_state.mesh.MeshLibrary`, which finds the standard HCP
        and FreeSurfer filenames in ``resources.mesh_dirs`` -- so working at a
        second density costs a directory, not a second surfaces block.
        """
        from .mesh.spaces import MeshError, parse_mesh

        hemi = _normalise_hemi(hemi)
        explicit_applies = True
        if mesh is not None:
            try:
                explicit_applies = parse_mesh(mesh) == parse_mesh(self.defaults.mesh)
            except MeshError:
                explicit_applies = str(mesh) == str(self.defaults.mesh)

        if explicit_applies and kind in self.resources.surfaces.get(hemi, {}):
            return self.resources.surface_path(hemi, kind)

        try:
            files = self.mesh_library().files_for(mesh or self.defaults.mesh)
            return files.surface(hemi, kind)
        except Exception as exc:
            if kind in self.resources.surfaces.get(hemi, {}):
                return self.resources.surface_path(hemi, kind)
            raise ConfigError(
                f"no {kind} surface for the {hemi} hemisphere of mesh "
                f"{mesh or self.defaults.mesh!r}.\n{exc}"
            ) from exc


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def load_settings(
    path: Optional[Path | str] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Settings:
    """Load settings, following the resolution chain and any ``extends:``.

    See the module docstring for the chain.  ``overrides`` is deep-merged last,
    on top of everything the files supplied.
    """
    chosen: Optional[Path] = None
    if path is not None:
        chosen = Path(path)
        if not chosen.exists():
            raise ConfigError(f"configuration file not found: {chosen}")
    else:
        for entry in resolution_chain():
            if entry["exists"]:
                chosen = entry["path"]
                log.debug("configuration selected from %s", entry["source"])
                break

    inherited: list[Path] = []
    if chosen is None:
        log.warning(
            "no configuration file found; using built-in defaults. Run "
            "'cifti-state config --init-machine' to create one for this machine."
        )
        data: dict[str, Any] = {}
        base_dir: Optional[Path] = None
    else:
        data, base_dir, inherited = _load_with_extends(Path(chosen))
        if inherited:
            log.info(
                "loaded configuration from %s (extends %s)",
                chosen,
                " <- ".join(p.name for p in inherited),
            )
        else:
            log.info("loaded configuration from %s", chosen)

    if overrides:
        data = _deep_merge(data, overrides)

    settings = Settings.from_mapping(
        data, source_path=chosen, base_dir=base_dir, inherited_from=inherited
    )
    if not settings.machine.name and chosen is not None:
        settings.machine.name = Path(chosen).stem
    return settings


def _load_with_extends(
    path: Path, _depth: int = 0, _seen: Optional[set[Path]] = None
) -> tuple[dict[str, Any], Path, list[Path]]:
    """Read *path*, recursively applying ``extends:``.

    Returns ``(merged_data, base_dir_for_relative_paths, parents)``.  Relative
    paths inside a config are resolved against the file that *declared* them,
    so a machine file may say ``root: ../../data`` while the template it
    extends keeps its own relative paths -- each is anchored to its own
    directory before merging.
    """
    _seen = _seen or set()
    resolved = path.resolve()
    if resolved in _seen:
        raise ConfigError(f"circular 'extends' chain at {path}")
    if _depth > MAX_EXTENDS_DEPTH:
        raise ConfigError(f"'extends' nested more than {MAX_EXTENDS_DEPTH} deep at {path}")
    _seen.add(resolved)

    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}: top level of a config file must be a mapping")
    raw = dict(raw)

    parent_ref = raw.pop("extends", None)
    child = _anchor_paths(raw, resolved.parent)

    if not parent_ref:
        return child, resolved.parent, []

    child = _prune_empty(child)

    parent_path = Path(str(parent_ref)).expanduser()
    if not parent_path.is_absolute():
        parent_path = (resolved.parent / parent_path).resolve()
    if not parent_path.exists():
        raise ConfigError(
            f"{path}: 'extends' points at a file that does not exist: {parent_path}"
        )

    parent_data, _parent_base, grandparents = _load_with_extends(
        parent_path, _depth + 1, _seen
    )
    merged = _deep_merge(parent_data, child)
    return merged, resolved.parent, [parent_path] + grandparents


def init_machine_config(
    machine: Optional[str] = None,
    *,
    template: Optional[Path] = None,
    description: str = "",
    overwrite: bool = False,
) -> Path:
    """Scaffold ``configs/machines/<machine>.yaml`` extending the platform template."""
    machine = machine or current_machine()
    target = machine_config_path(machine)
    if target.exists() and not overwrite:
        raise ConfigError(
            f"{target} already exists; pass overwrite=True to replace it"
        )
    template = Path(template) if template else default_config_path()
    try:
        relative = os.path.relpath(template, target.parent).replace(os.sep, "/")
    except ValueError:                               # different drive on Windows
        relative = template.as_posix()

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _MACHINE_TEMPLATE.format(
            machine=machine,
            description=description or f"{machine} ({current_platform()})",
            extends=relative,
            platform=current_platform(),
        ),
        encoding="utf-8",
    )
    log.info("created machine configuration %s", target)
    return target


_MACHINE_TEMPLATE = """\
# Machine profile: {machine}
#
# Picked up automatically on this machine (hostname -> {machine}). Everything
# not listed here comes from the template named in 'extends', so keep this file
# to the handful of paths that are actually machine-specific.
#
# Check it with:  cifti-state check --workbench --render

extends: {extends}

machine:
  name: {machine}
  description: {description}
  notes: ""

# ---- Workbench ----------------------------------------------------------- #
# Point at the bin_{platform}64 folder's executables. Leave blank to search PATH.
workbench:
  wb_command:
  wb_view:

# ---- Where the template pack and data live ------------------------------- #
resources:
  root:
  atlas_dir:
  atlas_csv_dir:
  neighbors:
    "32k":
      left:
      right:

runtime:
  tmp_dir:
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

#: A Windows drive path (``D:/x``, ``D:\x``) or a UNC path (``\\server\share``).
#: ``Path("D:/x").is_absolute()`` is False on Linux, so a Windows machine
#: profile inspected from Linux -- or validated in CI -- would otherwise have
#: its paths silently re-anchored to the config directory.
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_absolute(value: str) -> bool:
    text = str(value)
    return bool(_WINDOWS_ABSOLUTE.match(text)) or Path(text).is_absolute()


_HEMI_ALIASES = {
    "l": "left", "lh": "left", "left": "left", "cortex_left": "left",
    "r": "right", "rh": "right", "right": "right", "cortex_right": "right",
}


def _mesh_keys(mesh: str) -> list[str]:
    """Every spelling of *mesh* a configuration file might have used."""
    keys = [str(mesh)]
    try:
        from .mesh.spaces import parse_mesh

        space = parse_mesh(mesh)
    except Exception:
        return keys
    for candidate in (space.name, space.density, *space.aliases):
        if candidate not in keys:
            keys.append(candidate)
    return keys


def _normalise_hemi(value: str) -> str:
    key = str(value).strip().lower()
    if key not in _HEMI_ALIASES:
        raise ConfigError(f"unknown hemisphere {value!r}")
    return _HEMI_ALIASES[key]


def _opt_path(value: Any, base: Optional[Path]) -> Optional[Path]:
    if value in (None, "", False):
        return None
    if _is_absolute(str(value)):
        return Path(str(value))
    p = Path(str(value)).expanduser()
    if not _is_absolute(str(p)) and base is not None:
        p = (base / p).resolve()
    return p


def _opt_float(value: Any) -> Optional[float]:
    return None if value in (None, "") else float(value)


#: Keys whose values are paths relative to the *config file*, not to
#: ``resources.root``.  They are made absolute before any merge, so that a
#: machine file and the template it extends can each use their own relative
#: paths without one silently re-anchoring the other.
_FILE_RELATIVE_KEYS = (
    ("workbench", "wb_command"),
    ("workbench", "wb_view"),
    ("resources", "root"),
    ("resources", "atlas_dir"),
    ("resources", "atlas_csv_dir"),
    ("runtime", "tmp_dir"),
)


def _anchor_paths(data: Mapping[str, Any], base: Path) -> dict[str, Any]:
    """Resolve file-relative path keys against the directory of their own file."""
    out = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in data.items()}
    for section, key in _FILE_RELATIVE_KEYS:
        block = out.get(section)
        if not isinstance(block, dict):
            continue
        value = block.get(key)
        if value in (None, "", False):
            continue
        text = str(value)
        if _is_absolute(text):
            block[key] = text
            continue
        candidate = Path(text).expanduser()
        if _is_absolute(str(candidate)):          # ~ expanded to an absolute path
            block[key] = str(candidate)
            continue
        block[key] = str((base / candidate).resolve())
    return out


def _prune_empty(data: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys the child left blank, so they inherit instead of blanking out.

    A scaffolded machine file lists every overridable key with an empty value;
    without this, extending a template would erase everything the template set.
    ``False`` and ``0`` are kept -- only ``None`` and ``""`` count as "unset".
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, Mapping):
            nested = _prune_empty(value)
            if nested:
                out[key] = nested
        elif value is None or (isinstance(value, str) and not value.strip()):
            continue
        else:
            out[key] = value
    return out


def _deep_merge(base: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in other.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _stringify_paths(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _stringify_paths(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_stringify_paths(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj
