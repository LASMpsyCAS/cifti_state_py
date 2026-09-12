"""Which surface a map lives on, named once so everything else can agree.

A cortical surface map is only meaningful together with two facts: the *mesh*
it is sampled on (how many vertices, arranged how) and the *registration space*
those vertices were aligned in.  Conflating the two is the classic way to
produce a figure that looks fine and is wrong -- fs_LR 10k and fsaverage5 both
have 10242 vertices per hemisphere, and nothing about an array of 10242 numbers
says which one it is.  So a mesh here is always the pair:

    fsLR:32k        the HCP fs_LR mesh, 32492 vertices per hemisphere
    fsaverage5      FreeSurfer's fsaverage5, 10242 vertices per hemisphere

and :func:`identify_mesh` refuses to guess when a vertex count is ambiguous.

Registration space is what decides whether two meshes can be resampled between
directly.  Two fs_LR densities share the fs_LR sphere, so 32k -> 10k is one
step.  fs_LR and fsaverage do not, which is why HCP ships the
``fs_LR-deformed_to-fsaverage`` spheres: the fs_LR mesh with its vertices moved
onto the fsaverage sphere.  Crossing between the families means using those,
and :mod:`cifti_state.mesh.templates` knows which sphere belongs to which side
of a conversion.

**A naming collision worth knowing about.** "59k" means two different things in
this field.  A *59k CIFTI layout* (59412 greyordinates) is the fs_LR **32k**
mesh with the medial wall dropped -- that is a layout, not a mesh, and it is
handled by :mod:`cifti_state.io.cifti`.  The fs_LR **59k mesh** below is a
genuinely denser mesh, 59292 vertices per hemisphere.  The two are unrelated;
only the number is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

__all__ = [
    "MeshSpace",
    "KNOWN_MESHES",
    "parse_mesh",
    "identify_mesh",
    "list_meshes",
    "MeshError",
]


class MeshError(ValueError):
    """A mesh could not be named, found, or converted."""


@dataclass(frozen=True)
class MeshSpace:
    """One surface mesh in one registration space."""

    name: str              #: canonical id, e.g. ``"fsLR:32k"`` or ``"fsaverage5"``
    family: str            #: ``fsLR`` | ``fsaverage``
    density: str           #: ``3k`` | ``10k`` | ``32k`` | ``41k`` | ``59k`` | ``164k``
    n_vertices: int        #: per hemisphere
    registration: str      #: the sphere space this mesh's own sphere lives in
    aliases: tuple[str, ...] = ()
    note: str = ""

    @property
    def n_both(self) -> int:
        """Vertices across both hemispheres -- the dense CIFTI column count."""
        return 2 * self.n_vertices

    def describe(self) -> str:
        return (
            f"{self.name}: {self.n_vertices} vertices per hemisphere "
            f"({self.n_both} dense), {self.family} family"
        )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


#: Every mesh this package can name.  Adding a density is one row here plus its
#: filenames in :mod:`cifti_state.mesh.templates` -- nothing else in the
#: package hard-codes a vertex count.
KNOWN_MESHES: dict[str, MeshSpace] = {
    mesh.name: mesh
    for mesh in (
        MeshSpace(
            name="fsLR:10k", family="fsLR", density="10k", n_vertices=10242,
            registration="fsLR", aliases=("10k", "fslr10k", "fs_LR_10k"),
            note="same vertex count as fsaverage5, different space",
        ),
        MeshSpace(
            name="fsLR:32k", family="fsLR", density="32k", n_vertices=32492,
            registration="fsLR", aliases=("32k", "fslr32k", "fs_LR_32k", "fsLR32k"),
            note="the HCP standard; what this package's example data uses",
        ),
        MeshSpace(
            name="fsLR:59k", family="fsLR", density="59k", n_vertices=59292,
            registration="fsLR", aliases=("59k", "fslr59k"),
            note="a denser mesh -- NOT the 59412-greyordinate layout",
        ),
        MeshSpace(
            name="fsLR:164k", family="fsLR", density="164k", n_vertices=163842,
            registration="fsLR", aliases=("164k", "fslr164k"),
        ),
        MeshSpace(
            name="fsaverage4", family="fsaverage", density="3k", n_vertices=2562,
            registration="fsaverage", aliases=("fsavg4", "fsaverage-3k"),
        ),
        MeshSpace(
            name="fsaverage5", family="fsaverage", density="10k", n_vertices=10242,
            registration="fsaverage", aliases=("fsavg5", "fs5", "fsaverage-10k"),
            note="same vertex count as fsLR:10k, different space",
        ),
        MeshSpace(
            name="fsaverage6", family="fsaverage", density="41k", n_vertices=40962,
            registration="fsaverage", aliases=("fsavg6", "fs6", "fsaverage-41k"),
        ),
        MeshSpace(
            name="fsaverage", family="fsaverage", density="164k", n_vertices=163842,
            registration="fsaverage", aliases=("fsavg", "fsaverage7", "fsaverage-164k"),
        ),
    )
}

#: Alias -> canonical name, built once.  Lower-cased, punctuation-insensitive.
_ALIASES: dict[str, str] = {}
for _mesh in KNOWN_MESHES.values():
    for _key in (_mesh.name, *_mesh.aliases):
        _ALIASES[_key.lower().replace("-", "").replace("_", "")] = _mesh.name


def list_meshes(family: Optional[str] = None) -> list[MeshSpace]:
    """Every known mesh, optionally restricted to one family."""
    out = list(KNOWN_MESHES.values())
    if family is not None:
        out = [m for m in out if m.family.lower() == family.lower()]
    return sorted(out, key=lambda m: (m.family, m.n_vertices))


def parse_mesh(value: "str | MeshSpace") -> MeshSpace:
    """Look up a mesh by name or alias.

    Accepts the canonical ``"fsLR:32k"``, the bare density ``"32k"`` (which
    means fs_LR, because that is what a bare density has always meant in this
    package's configuration files), and the usual spellings of the fsaverage
    meshes.
    """
    if isinstance(value, MeshSpace):
        return value
    key = str(value).strip().lower().replace("-", "").replace("_", "")
    key = key.replace(":", "").replace(" ", "")
    if key in _ALIASES:
        return KNOWN_MESHES[_ALIASES[key]]
    raise MeshError(
        f"unknown mesh {value!r}; known meshes are "
        f"{', '.join(sorted(KNOWN_MESHES))} (a bare density such as '32k' "
        f"means the fs_LR mesh of that density)"
    )


def identify_mesh(
    n_vertices: int,
    *,
    prefer: "Optional[str | MeshSpace]" = None,
    candidates: Optional[Iterable[MeshSpace]] = None,
) -> MeshSpace:
    """Which mesh has *n_vertices* vertices per hemisphere?

    A vertex count does not always answer the question -- fs_LR 10k and
    fsaverage5 both have 10242, and fs_LR 164k and fsaverage both have 163842.
    When the count is ambiguous, *prefer* settles it: pass the mesh or family
    the caller already believes it is working in (the configured
    ``defaults.mesh`` is usually the right thing), and the matching candidate
    wins.  With nothing to go on, this raises rather than picking one, because
    a silently wrong space is worse than a stopped analysis.
    """
    pool = list(candidates) if candidates is not None else list(KNOWN_MESHES.values())
    matches = [m for m in pool if m.n_vertices == int(n_vertices)]
    if not matches:
        known = ", ".join(
            f"{m.name} ({m.n_vertices})" for m in sorted(pool, key=lambda x: x.n_vertices)
        )
        raise MeshError(
            f"no known mesh has {n_vertices} vertices per hemisphere; known: {known}"
        )
    if len(matches) == 1:
        return matches[0]

    if prefer is not None:
        preferred = prefer if isinstance(prefer, MeshSpace) else None
        family = None
        if preferred is None:
            try:
                preferred = parse_mesh(prefer)
            except MeshError:
                family = str(prefer).lower()
        if preferred is not None:
            if preferred in matches:
                return preferred
            family = preferred.family.lower()
        if family is not None:
            same_family = [m for m in matches if m.family.lower() == family]
            if len(same_family) == 1:
                return same_family[0]

    names = " or ".join(m.name for m in matches)
    raise MeshError(
        f"{n_vertices} vertices per hemisphere is ambiguous -- it could be "
        f"{names}. They have the same mesh size but different registration "
        f"spaces, so resampling one as the other would be wrong. Say which "
        f"with mesh=/--mesh, or set defaults.mesh in the configuration."
    )
