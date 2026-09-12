"""Finding the template files a mesh needs, without writing them all down.

Resampling a surface map is a three-file question per hemisphere: the sphere
the source mesh is registered on, the sphere the target mesh is registered on,
and -- for the area-corrected method everyone actually uses -- the average
vertex-area metric of each.  Add the medial-wall ROI and the anatomical
surfaces for drawing, and a configuration that named every file explicitly
would run to sixty lines per mesh before anyone had analysed anything.

So this module does not ask for them by name.  The files that ship with HCP
and FreeSurfer already have stable, self-describing names --
``fs_LR.32k.L.sphere.surf.gii``, ``fsaverage5_std_sphere.L.10k_fsavg_L.surf.gii``,
``S900.L.midthickness_MSMAll.10k_fs_LR_va.shape.gii`` -- and this module knows
those patterns.  Point it at the directories the files live in and it finds
them:

.. code-block:: yaml

    resources:
      root: fs_LR_32k
      mesh_dirs: [fs_LR_32k, 10k, resample_fsaverage]

Anything it cannot find is reported by name, with what the file is for, rather
than surfacing later as a confusing ``wb_command`` error.  A file whose name
does not match the conventions can always be pinned explicitly in
``resources.mesh_files``.

Two rules keep the search honest.  It never recurses: the HCP
``resample_fsaverage`` pack keeps superseded spheres in a ``misc``
subdirectory, and quietly resampling through ``old_fs_LR-deformed_to-...``
would be a silent wrong answer.  And it skips any basename beginning ``old_``
or ``fix_``, for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..logging_setup import get_logger
from .spaces import KNOWN_MESHES, MeshError, MeshSpace, parse_mesh

log = get_logger(__name__)

__all__ = [
    "MeshFiles",
    "MeshLibrary",
    "SURFACE_KINDS",
    "common_registration",
]

#: Anatomical surfaces a mesh may carry, in the order a caller usually wants
#: them when it just needs "a surface".
SURFACE_KINDS: tuple[str, ...] = ("midthickness", "inflated", "very_inflated", "flat")

#: Basenames beginning with these are superseded copies shipped alongside the
#: real ones. Never match them.
_SKIP_PREFIXES = ("old_", "fix_")

# --------------------------------------------------------------------------- #
# the filename conventions
# --------------------------------------------------------------------------- #
#
# ``{H}`` is the hemisphere letter (L/R), ``{h}`` its lower case.  Patterns are
# tried in order and the first hit wins, so the exact HCP name always beats the
# permissive glob under it.


def _fslr_patterns(density: str) -> dict[str, tuple[str, ...]]:
    d = density
    return {
        "sphere": (
            f"fs_LR.{d}.{{H}}.sphere.surf.gii",
            f"{{H}}.sphere.{d}_fs_LR.surf.gii",
            f"*.{{H}}.sphere.{d}_fs_LR.surf.gii",
            f"Sphere.{d}_fs_LR.{{H}}.surf.gii",
        ),
        # the same mesh with its vertices moved onto the fsaverage sphere --
        # the only way to cross between the two families
        "sphere@fsaverage": (
            f"fs_LR-deformed_to-fsaverage.{{H}}.sphere.{d}_fs_LR.surf.gii",
        ),
        "area": (
            f"fs_LR.{{H}}.midthickness_va_avg.{d}_fs_LR.shape.gii",
            f"*.{{H}}.midthickness*{d}_fs_LR_va.shape.gii",
            f"*.{{H}}.midthickness_va_avg.{d}_fs_LR.shape.gii",
        ),
        "roi": (
            f"{{H}}.atlasroi.{d}_fs_LR.shape.gii",
            f"*.{{H}}.atlasroi.{d}_fs_LR.shape.gii",
        ),
        "midthickness": (
            f"fs_LR.{d}.{{H}}.midthickness.surf.gii",
            f"*.{{H}}.midthickness_MSMAll.{d}_fs_LR.surf.gii",
            f"*.{{H}}.midthickness.{d}_fs_LR.surf.gii",
        ),
        "inflated": (
            f"fs_LR.{d}.{{H}}.inflated.surf.gii",
            f"*.{{H}}.inflated_MSMAll.{d}_fs_LR.surf.gii",
            f"*.{{H}}.inflated.{d}_fs_LR.surf.gii",
        ),
        "very_inflated": (
            f"fs_LR.{d}.{{H}}.very_inflated.surf.gii",
            f"*.{{H}}.very_inflated_MSMAll.{d}_fs_LR.surf.gii",
            f"*.{{H}}.very_inflated.{d}_fs_LR.surf.gii",
        ),
        "flat": (
            f"*.{{H}}.flat.{d}_fs_LR.surf.gii",
        ),
    }


def _fsaverage_patterns(name: str, density: str) -> dict[str, tuple[str, ...]]:
    d = density
    return {
        "sphere": (
            f"{name}_std_sphere.{{H}}.{d}_fsavg_{{H}}.surf.gii",
            f"{name}.{{H}}.sphere.{d}_fsavg_{{H}}.surf.gii",
            f"{{h}}.sphere",
        ),
        "area": (
            f"{name}.{{H}}.midthickness_va_avg.{d}_fsavg_{{H}}.shape.gii",
            f"{name}.{{H}}.midthickness*{d}_fsavg_{{H}}.shape.gii",
        ),
        "roi": (
            f"{name}.{{H}}.atlasroi.{d}_fsavg_{{H}}.shape.gii",
        ),
        "midthickness": (
            f"{name}.{{H}}.midthickness.{d}_fsavg_{{H}}.surf.gii",
            f"{{h}}.midthickness",
        ),
        "inflated": (
            f"{name}.{{H}}.inflated.{d}_fsavg_{{H}}.surf.gii",
            f"{{h}}.inflated",
        ),
        "very_inflated": (),
        "flat": (),
    }


def _patterns_for(mesh: MeshSpace) -> dict[str, tuple[str, ...]]:
    if mesh.family == "fsLR":
        return _fslr_patterns(mesh.density)
    return _fsaverage_patterns(mesh.name, mesh.density)


#: What each role is for, quoted back at the user when it is missing.
_ROLE_PURPOSE = {
    "sphere": "the registration sphere -- resampling cannot happen without it",
    "sphere@fsaverage": (
        "the fs_LR mesh deformed onto the fsaverage sphere -- needed only to "
        "convert between the fs_LR and fsaverage families"
    ),
    "area": (
        "the average midthickness vertex areas -- ADAP_BARY_AREA weights by "
        "these, and without them the resampling silently changes to plain "
        "barycentric"
    ),
    "roi": "the medial-wall mask; optional, but it keeps the wall out of the data",
    "midthickness": "geometry: vertex areas, adjacency, cluster surface area",
    "inflated": "geometry for drawing",
    "very_inflated": "geometry for drawing",
    "flat": "geometry for drawing",
}


def common_registration(source: MeshSpace, target: MeshSpace) -> Optional[str]:
    """The sphere space a direct *source* -> *target* resampling would use.

    ``None`` when there is no single space the pair can meet in, which is the
    signal to route through an intermediate mesh (see
    :func:`cifti_state.mesh.resample.plan_route`).
    """
    if source.registration == target.registration:
        return source.registration
    # An fs_LR mesh can be expressed on the fsaverage sphere; the reverse pack
    # does not exist, so fsaverage is the only meeting point.
    if {source.family, target.family} == {"fsLR", "fsaverage"}:
        return "fsaverage"
    return None


# --------------------------------------------------------------------------- #
# what one mesh's files look like once found
# --------------------------------------------------------------------------- #


@dataclass
class MeshFiles:
    """The template files located for a single mesh."""

    mesh: MeshSpace
    #: role -> {"left": Path, "right": Path}; only roles that were found
    files: dict[str, dict[str, Path]] = field(default_factory=dict)
    searched: tuple[Path, ...] = ()

    # -- lookup ------------------------------------------------------------- #

    def get(self, role: str, hemi: str) -> Path:
        """The file for *role* and *hemi*, or a message saying what is missing."""
        hemi = _hemi(hemi)
        try:
            return self.files[role][hemi]
        except KeyError:
            raise MeshError(self.explain_missing(role, hemi)) from None

    def has(self, role: str, hemi: Optional[str] = None) -> bool:
        entry = self.files.get(role)
        if not entry:
            return False
        if hemi is None:
            return len(entry) == 2
        return _hemi(hemi) in entry

    def sphere(self, hemi: str, registration: Optional[str] = None) -> Path:
        """The sphere of this mesh, expressed in *registration* space.

        With *registration* left out, or equal to the mesh's own, this is the
        mesh's native sphere.  Asking an fs_LR mesh for its sphere in
        fsaverage space returns the ``fs_LR-deformed_to-fsaverage`` variant --
        the same mesh, different coordinates.
        """
        if registration is None or registration == self.mesh.registration:
            return self.get("sphere", hemi)
        role = f"sphere@{registration}"
        if role not in _ROLE_PURPOSE:
            raise MeshError(
                f"{self.mesh.name} has no sphere in {registration!r} space"
            )
        return self.get(role, hemi)

    def area(self, hemi: str) -> Path:
        return self.get("area", hemi)

    def roi(self, hemi: str) -> Optional[Path]:
        return self.files.get("roi", {}).get(_hemi(hemi))

    def surface(self, hemi: str, kind: str = "midthickness") -> Path:
        if kind not in SURFACE_KINDS:
            raise MeshError(
                f"unknown surface kind {kind!r}; known: {', '.join(SURFACE_KINDS)}"
            )
        return self.get(kind, hemi)

    def surface_kinds(self) -> list[str]:
        return [k for k in SURFACE_KINDS if self.has(k)]

    # -- reporting ---------------------------------------------------------- #

    @property
    def can_resample(self) -> bool:
        """Enough to be one end of an area-corrected resampling."""
        return self.has("sphere") and self.has("area")

    def explain_missing(self, role: str, hemi: Optional[str] = None) -> str:
        mesh = self.mesh
        patterns = _patterns_for(mesh).get(role, ())
        wanted = ", ".join(
            _expand(p, h) for p in patterns[:2]
            for h in (("left", "right") if hemi is None else (hemi,))
        )
        where = "\n    ".join(str(d) for d in self.searched) or "(no directories)"
        return (
            f"{mesh.name} has no {role} file"
            + (f" for the {hemi} hemisphere" if hemi else "")
            + f".\n  It is {_ROLE_PURPOSE.get(role, 'needed')}.\n"
            f"  Expected a file named like: {wanted or '(no pattern known)'}\n"
            f"  Looked in:\n    {where}\n"
            f"  Add the directory holding it to resources.mesh_dirs, or name "
            f"the file directly in resources.mesh_files."
        )

    def describe(self) -> dict:
        return {
            "mesh": self.mesh.name,
            "n_vertices": self.mesh.n_vertices,
            "found": {
                role: {h: str(p) for h, p in sides.items()}
                for role, sides in sorted(self.files.items())
            },
            "can_resample": self.can_resample,
            "surfaces": self.surface_kinds(),
        }

    def summary(self) -> str:
        roles = sorted(r for r, s in self.files.items() if len(s) == 2)
        half = sorted(r for r, s in self.files.items() if len(s) == 1)
        line = f"{self.mesh.name:<12} {', '.join(roles) if roles else 'nothing found'}"
        if half:
            line += f"  [one hemisphere only: {', '.join(half)}]"
        return line


# --------------------------------------------------------------------------- #
# the library
# --------------------------------------------------------------------------- #


class MeshLibrary:
    """Every mesh whose files can be found in a set of directories.

    Construct it from :class:`~cifti_state.config.Settings` with
    :meth:`from_settings`, which is what the rest of the package does; the
    directory list is a plain argument here so tests and one-off scripts do not
    need a configuration file.
    """

    def __init__(
        self,
        directories: Iterable[Path | str],
        *,
        overrides: Optional[dict] = None,
        meshes: Optional[Iterable[MeshSpace]] = None,
    ):
        self.directories = tuple(
            Path(d) for d in directories if d is not None and Path(d).is_dir()
        )
        self.overrides = dict(overrides or {})
        self._meshes = tuple(meshes) if meshes is not None else tuple(KNOWN_MESHES.values())
        self._index = _index_directories(self.directories)
        self._cache: dict[str, MeshFiles] = {}

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_settings(cls, settings) -> "MeshLibrary":
        """Build from ``resources.mesh_dirs`` (falling back to sensible guesses)."""
        resources = settings.resources
        directories = list(getattr(resources, "mesh_dirs", ()) or ())
        if not directories:
            directories = _default_directories(resources.root)
        return cls(
            directories,
            overrides=dict(getattr(resources, "mesh_files", {}) or {}),
        )

    # -- lookup ------------------------------------------------------------- #

    def __getitem__(self, mesh: "str | MeshSpace") -> MeshFiles:
        return self.files_for(mesh)

    def files_for(self, mesh: "str | MeshSpace") -> MeshFiles:
        """Locate (and cache) the template files of one mesh."""
        space = parse_mesh(mesh)
        cached = self._cache.get(space.name)
        if cached is not None:
            return cached

        found: dict[str, dict[str, Path]] = {}
        patterns = _patterns_for(space)
        override = self.overrides.get(space.name, {}) or {}
        for role, role_patterns in patterns.items():
            for hemi, letter in (("left", "L"), ("right", "R")):
                explicit = _override_path(override, role, hemi)
                if explicit is not None:
                    if explicit.exists():
                        found.setdefault(role, {})[hemi] = explicit
                    else:
                        log.warning(
                            "resources.mesh_files names %s for %s %s, but that "
                            "file does not exist", explicit, space.name, role,
                        )
                    continue
                hit = self._match(role_patterns, letter)
                if hit is not None:
                    found.setdefault(role, {})[hemi] = hit

        files = MeshFiles(mesh=space, files=found, searched=self.directories)
        self._cache[space.name] = files
        return files

    def _match(self, patterns: Iterable[str], letter: str) -> Optional[Path]:
        from fnmatch import fnmatch

        for pattern in patterns:
            expanded = pattern.replace("{H}", letter).replace("{h}", letter.lower())
            if "*" not in expanded and "?" not in expanded:
                hit = self._index.get(expanded.lower())
                if hit is not None:
                    return hit
                continue
            lowered = expanded.lower()
            for name, path in self._index.items():
                if fnmatch(name, lowered):
                    return path
        return None

    # -- reporting ---------------------------------------------------------- #

    def available(self) -> list[MeshSpace]:
        """Meshes with enough files to take part in a resampling."""
        return [m for m in self._meshes if self.files_for(m).can_resample]

    def report(self) -> str:
        lines = [f"mesh templates found in {len(self.directories)} director"
                 f"{'y' if len(self.directories) == 1 else 'ies'}:"]
        for directory in self.directories:
            lines.append(f"  {directory}")
        for space in sorted(self._meshes, key=lambda m: (m.family, m.n_vertices)):
            files = self.files_for(space)
            mark = "ok  " if files.can_resample else "    "
            lines.append(f"  {mark}{files.summary()}")
        return "\n".join(lines)

    def describe(self) -> dict:
        return {
            "directories": [str(d) for d in self.directories],
            "meshes": {
                space.name: self.files_for(space).describe()
                for space in self._meshes
            },
        }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _default_directories(root: Optional[Path]) -> list[Path]:
    """Where to look when the configuration does not say.

    The template packs are conventionally siblings: a ``fs_LR_32k`` beside a
    ``10k`` beside a ``resample_fsaverage``.  So the default is the configured
    resources root and every directory next to it -- one level, never deeper,
    because the deeper level is where superseded copies live.
    """
    if root is None:
        return []
    root = Path(root)
    out = [root]
    parent = root.parent
    if parent.is_dir():
        out += sorted(p for p in parent.iterdir() if p.is_dir() and p != root)
    return out


def _index_directories(directories: Iterable[Path]) -> dict[str, Path]:
    """Lower-cased basename -> path, first directory wins, no recursion."""
    index: dict[str, Path] = {}
    for directory in directories:
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:  # pragma: no cover - unreadable directory
            log.debug("cannot list %s: %s", directory, exc)
            continue
        for path in entries:
            if not path.is_file():
                continue
            name = path.name
            if name.startswith(_SKIP_PREFIXES):
                continue
            index.setdefault(name.lower(), path)
    return index


def _override_path(override: dict, role: str, hemi: str) -> Optional[Path]:
    entry = override.get(role)
    if entry is None:
        return None
    if isinstance(entry, (str, Path)):
        return None if hemi == "right" else Path(entry)
    value = entry.get(hemi) if isinstance(entry, dict) else None
    return None if value is None else Path(value)


def _expand(pattern: str, hemi: str) -> str:
    letter = "L" if _hemi(hemi) == "left" else "R"
    return pattern.replace("{H}", letter).replace("{h}", letter.lower())


def _hemi(value: str) -> str:
    text = str(value).lower()
    if text in ("l", "lh", "left", "cortex_left"):
        return "left"
    if text in ("r", "rh", "right", "cortex_right"):
        return "right"
    raise MeshError(f"unknown hemisphere {value!r}")
