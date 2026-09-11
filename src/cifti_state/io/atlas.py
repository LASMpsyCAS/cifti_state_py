"""Anatomical atlases on the fs_LR 32k surface.

Region names come from each ``label.gii``'s own GIFTI LabelTable, which every
atlas in the fs_LR template pack carries.  That covers all of them, not only
the ones that happen to have a CSV, and it removes the need for the
hemisphere-offset arithmetic the MATLAB code used (``+1``/``+36`` for Desikan,
``+max(L)`` for Destrieux, nothing for Glasser): each hemisphere is looked up
in its own table and regions are keyed by ``(hemisphere, label_id)``.

A CSV, when configured, is applied on top as a *display-name override*.
"""

from __future__ import annotations

import csv as _csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import nibabel as nib
import numpy as np
import yaml

from ..config import CONFIG_DIR, ConfigError, Settings
from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "Atlas",
    "AtlasHemisphere",
    "AtlasRegistry",
    "load_atlas",
    "load_registry",
    "list_atlases",
]

_DEFAULT_UNKNOWN = {
    "???", "unknown", "empty", "none", "medial wall", "medial_wall",
    "l_???", "r_???", "background", "non-cortex",
}


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AtlasEntry:
    name: str
    display_name: str
    pattern: str
    csv: Optional[str] = None
    csv_scope: str = "per_hemi"          # per_hemi | merged
    csv_left_offset: int = 0
    csv_right_offset: Optional[int] = None
    strip_prefix: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    n_regions: Optional[int] = None

    def file_for(self, hemi: str) -> str:
        return self.pattern.format(name=self.name, HEMI="L" if hemi == "left" else "R")


@dataclass
class AtlasRegistry:
    entries: dict[str, AtlasEntry] = field(default_factory=dict)
    default_pattern: str = "{name}.32k.{HEMI}.label.gii"
    unknown_labels: set[str] = field(default_factory=lambda: set(_DEFAULT_UNKNOWN))
    _alias_map: dict[str, str] = field(default_factory=dict, repr=False)

    def resolve(self, name: str) -> AtlasEntry:
        """Look up by registry key, alias, or fall back to the naming convention."""
        if name in self.entries:
            return self.entries[name]
        key = self._alias_map.get(name.lower())
        if key:
            return self.entries[key]
        # Not registered: assume the standard file naming convention.
        return AtlasEntry(
            name=name, display_name=name, pattern=self.default_pattern
        )

    def names(self) -> list[str]:
        return sorted(self.entries)


def load_registry(path: Optional[Path | str] = None) -> AtlasRegistry:
    """Load ``configs/atlases.yaml`` (or a custom registry file)."""
    path = Path(path) if path else CONFIG_DIR / "atlases.yaml"
    if not path.exists():
        log.warning("atlas registry %s not found; using naming convention only", path)
        return AtlasRegistry()

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    defaults = data.get("defaults") or {}
    default_pattern = str(defaults.get("pattern", "{name}.32k.{HEMI}.label.gii"))
    unknown = {str(s).strip().lower() for s in (defaults.get("unknown_labels") or [])}
    unknown |= _DEFAULT_UNKNOWN

    entries: dict[str, AtlasEntry] = {}
    alias_map: dict[str, str] = {}
    for name, raw in (data.get("atlases") or {}).items():
        raw = raw or {}
        aliases = tuple(str(a) for a in (raw.get("aliases") or ()))
        entry = AtlasEntry(
            name=str(name),
            display_name=str(raw.get("label", name)),
            pattern=str(raw.get("pattern", default_pattern)),
            csv=raw.get("csv"),
            csv_scope=str(raw.get("csv_scope", "per_hemi")),
            csv_left_offset=int(raw.get("csv_left_offset", 0)),
            csv_right_offset=(
                None if raw.get("csv_right_offset") is None
                else int(raw["csv_right_offset"])
            ),
            strip_prefix=tuple(str(s) for s in (raw.get("strip_prefix") or ())),
            aliases=aliases,
            n_regions=(None if raw.get("n_regions") is None else int(raw["n_regions"])),
        )
        entries[entry.name] = entry
        alias_map[entry.name.lower()] = entry.name
        for alias in aliases:
            alias_map[alias.lower()] = entry.name

    return AtlasRegistry(
        entries=entries,
        default_pattern=default_pattern,
        unknown_labels=unknown,
        _alias_map=alias_map,
    )


def list_atlases(
    settings: Settings,
    *,
    registry: Optional[AtlasRegistry] = None,
    include_unregistered: bool = False,
) -> list[dict[str, Any]]:
    """Report which atlases are actually available in ``resources.atlas_dir``."""
    registry = registry or load_registry()
    atlas_dir = settings.resources.atlas_dir
    if atlas_dir is None or not Path(atlas_dir).exists():
        raise ConfigError(f"atlas_dir is missing or unset: {atlas_dir}")
    atlas_dir = Path(atlas_dir)

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in registry.entries.values():
        left = atlas_dir / entry.file_for("left")
        right = atlas_dir / entry.file_for("right")
        if left.exists() and right.exists():
            found.append(
                {
                    "name": entry.name,
                    "display_name": entry.display_name,
                    "n_regions": entry.n_regions,
                    "registered": True,
                    "left": left,
                    "right": right,
                }
            )
            seen.add(entry.name)

    if include_unregistered:
        rx = re.compile(r"^(?P<name>.+)\.32k\.L\.label\.gii$")
        for candidate in sorted(atlas_dir.glob("*.32k.L.label.gii")):
            match = rx.match(candidate.name)
            if not match:
                continue
            name = match.group("name")
            if name in seen:
                continue
            right = atlas_dir / f"{name}.32k.R.label.gii"
            if right.exists():
                found.append(
                    {
                        "name": name,
                        "display_name": name,
                        "n_regions": None,
                        "registered": False,
                        "left": candidate,
                        "right": right,
                    }
                )
    return found


# --------------------------------------------------------------------------- #
# atlas objects
# --------------------------------------------------------------------------- #


@dataclass
class AtlasHemisphere:
    labels: np.ndarray            #: (n_vertices,) int label id per vertex
    names: dict[int, str]         #: label id -> region name
    hemisphere: str
    path: Optional[Path] = None

    @property
    def n_vertices(self) -> int:
        return int(self.labels.size)


@dataclass
class Atlas:
    """A cortical parcellation with per-hemisphere label tables."""

    name: str
    display_name: str
    left: AtlasHemisphere
    right: AtlasHemisphere
    unknown_labels: set[str] = field(default_factory=lambda: set(_DEFAULT_UNKNOWN))

    def hemi(self, which: str) -> AtlasHemisphere:
        which = which.lower()
        if which in ("l", "lh", "left"):
            return self.left
        if which in ("r", "rh", "right"):
            return self.right
        raise KeyError(f"unknown hemisphere {which!r}")

    def region_name(self, hemisphere: str, label_id: int) -> str:
        names = self.hemi(hemisphere).names
        return names.get(int(label_id), f"label_{int(label_id)}")

    def is_unknown(self, hemisphere: str, label_id: int) -> bool:
        """True for medial wall / background labels that should not be reported."""
        if int(label_id) == 0:
            return True
        name = self.region_name(hemisphere, label_id).strip().lower()
        return name in self.unknown_labels

    def n_regions(self) -> int:
        ids = set()
        for hemi in ("left", "right"):
            for label_id in np.unique(self.hemi(hemi).labels):
                if not self.is_unknown(hemi, int(label_id)):
                    ids.add((hemi, int(label_id)))
        return len(ids)


def load_atlas(
    name: str,
    settings: Settings,
    *,
    registry: Optional[AtlasRegistry] = None,
    use_csv: bool = True,
) -> Atlas:
    """Load an atlas by registry name, alias, or bare filename stem."""
    registry = registry or load_registry()
    entry = registry.resolve(name)

    atlas_dir = settings.resources.atlas_dir
    if atlas_dir is None:
        raise ConfigError("resources.atlas_dir is not set")
    atlas_dir = Path(atlas_dir)

    hemis: dict[str, AtlasHemisphere] = {}
    for hemi in ("left", "right"):
        path = atlas_dir / entry.file_for(hemi)
        if not path.exists():
            raise FileNotFoundError(
                f"atlas {entry.name!r}: missing {hemi} file {path}"
            )
        labels, name_pairs = _read_label_gii(str(path))
        names = dict(name_pairs)
        if entry.strip_prefix:
            names = {
                k: _strip_prefixes(v, entry.strip_prefix) for k, v in names.items()
            }
        hemis[hemi] = AtlasHemisphere(
            labels=labels, names=dict(names), hemisphere=hemi, path=path
        )

    if use_csv and entry.csv:
        _apply_csv_override(entry, hemis, settings)

    atlas = Atlas(
        name=entry.name,
        display_name=entry.display_name,
        left=hemis["left"],
        right=hemis["right"],
        unknown_labels=set(registry.unknown_labels),
    )
    log.info(
        "loaded atlas %s (%d regions across both hemispheres)",
        atlas.name,
        atlas.n_regions(),
    )
    return atlas


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=32)
def _read_label_gii(path: str) -> tuple[np.ndarray, tuple[tuple[int, str], ...]]:
    img = nib.load(path)
    if not img.darrays:
        raise ValueError(f"{path}: no data arrays")
    labels = np.asarray(img.darrays[0].data).astype(np.int64).ravel()

    names: dict[int, str] = {}
    table = getattr(img, "labeltable", None)
    if table is not None:
        try:
            names = {int(k): str(v) for k, v in table.get_labels_as_dict().items()}
        except Exception:  # pragma: no cover - malformed label tables
            log.warning("%s: could not read the GIFTI LabelTable", path)
    return labels, tuple(sorted(names.items()))


def _strip_prefixes(value: str, prefixes: Iterable[str]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _apply_csv_override(
    entry: AtlasEntry, hemis: dict[str, AtlasHemisphere], settings: Settings
) -> None:
    csv_dir = settings.resources.atlas_csv_dir
    if csv_dir is None:
        log.debug("atlas %s: no atlas_csv_dir configured, keeping LabelTable names",
                  entry.name)
        return
    csv_path = Path(csv_dir) / str(entry.csv)
    if not csv_path.exists():
        log.warning("atlas %s: CSV %s not found, keeping LabelTable names",
                    entry.name, csv_path)
        return

    table = _read_label_csv(csv_path)
    if not table:
        return

    if entry.csv_scope == "per_hemi":
        for hemi in ("left", "right"):
            _merge_names(hemis[hemi], table, offset=0)
    elif entry.csv_scope == "merged":
        right_offset = entry.csv_right_offset
        if right_offset is None:
            left_ids = hemis["left"].labels
            right_offset = int(left_ids.max()) if left_ids.size else 0
        _merge_names(hemis["left"], table, offset=entry.csv_left_offset)
        _merge_names(hemis["right"], table, offset=int(right_offset))
    else:
        log.warning("atlas %s: unknown csv_scope %r, ignoring CSV",
                    entry.name, entry.csv_scope)
        return
    log.debug("atlas %s: applied display names from %s", entry.name, csv_path.name)


def _merge_names(hemi: AtlasHemisphere, table: dict[int, str], offset: int) -> None:
    for label_id in list(hemi.names) + [int(v) for v in np.unique(hemi.labels)]:
        key = int(label_id) + offset
        if key in table:
            hemi.names[int(label_id)] = table[key]


@lru_cache(maxsize=32)
def _read_label_csv(path: Path) -> dict[int, str]:
    """Read a two-column ``id,name`` CSV. A non-numeric first row is a header."""
    out: dict[int, str] = {}
    with Path(path).open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in _csv.reader(fh):
            if len(row) < 2:
                continue
            try:
                key = int(float(row[0].strip()))
            except ValueError:
                continue  # header line
            out[key] = row[1].strip()
    return out
