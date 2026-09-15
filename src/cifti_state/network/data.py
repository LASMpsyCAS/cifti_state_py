"""Reading a node-and-edge dataset from plain text.

A dataset is four files, in the layout NeuroMArVL uses:

===================== ==================================================
``coordinates.txt``   ``n`` rows of ``x y z`` in MNI millimetres
``labels.txt``        ``n`` node names, one per line
``matrix.txt``        an ``n x n`` matrix; ``matrix[i, j]`` is ``i -> j``
``attributes.txt``    ``n`` rows of ``m`` numeric columns, with a header
===================== ==================================================

Only the coordinates and the matrix are required.  Whitespace, tabs and commas
all separate columns, and a leading header row is detected rather than
declared, because the files in the wild have it both ways.

The matrix is kept exactly as written -- **it is not symmetrised**.  Effective
connectivity is directed, and ``matrix[i, j] != matrix[j, i]`` is the signal,
not noise to be averaged away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "NetworkError",
    "NetworkData",
    "read_coordinates",
    "read_labels",
    "read_matrix",
    "read_attributes",
    "load_network",
    "load_network_dir",
]


class NetworkError(ValueError):
    """A dataset could not be read, or its files disagree with each other."""


_SPLIT = re.compile(r"[,\t ;]+")


def _rows(path: Path) -> list[list[str]]:
    """Split a text file into fields, dropping blanks and ``#`` comments."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:  # pragma: no cover - unusual encodings
        text = path.read_text(encoding="latin-1")
    out: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append([f for f in _SPLIT.split(line) if f])
    return out


def _is_number(field_: str) -> bool:
    try:
        float(field_)
    except ValueError:
        return False
    return True


def _looks_like_header(row: Sequence[str]) -> bool:
    """True when no field in the row parses as a number.

    A header is recognised, not declared, so the same reader takes
    ``coordinates.txt`` with its ``x y z`` line and one without.
    """
    return bool(row) and not any(_is_number(f) for f in row)


def _numeric(rows: list[list[str]], path: Path) -> np.ndarray:
    try:
        return np.array([[float(f) for f in row] for row in rows], dtype=float)
    except ValueError as exc:
        raise NetworkError(f"{path}: could not read a number ({exc})") from exc


# --------------------------------------------------------------------------- #
# the individual files
# --------------------------------------------------------------------------- #


def read_coordinates(path: Path | str) -> np.ndarray:
    """Read node positions as an ``(n, 3)`` array of MNI millimetres."""
    path = Path(path)
    rows = _rows(path)
    if not rows:
        raise NetworkError(f"{path}: no coordinates in the file")
    if _looks_like_header(rows[0]):
        rows = rows[1:]
    widths = {len(r) for r in rows}
    if widths != {3}:
        raise NetworkError(
            f"{path}: expected 3 columns (x y z) on every row, found "
            f"{sorted(widths)}"
        )
    coords = _numeric(rows, path)
    if not np.isfinite(coords).all():
        raise NetworkError(f"{path}: coordinates contain NaN or infinity")
    return coords


def read_labels(path: Path | str) -> tuple[str, ...]:
    """Read node names, one per line.

    Names may contain spaces: the whole line is the name.  A first line of
    ``label`` or ``name`` alone is treated as a header.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:  # pragma: no cover
        text = path.read_text(encoding="latin-1")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if lines and lines[0].lower() in {"label", "labels", "name", "node"}:
        lines = lines[1:]
    if not lines:
        raise NetworkError(f"{path}: no labels in the file")
    return tuple(lines)


def read_matrix(path: Path | str) -> np.ndarray:
    """Read a square connectivity matrix; ``matrix[i, j]`` is ``i -> j``."""
    path = Path(path)
    rows = _rows(path)
    if not rows:
        raise NetworkError(f"{path}: no matrix in the file")
    if _looks_like_header(rows[0]):
        rows = rows[1:]
        # A header row means there may also be a leading label column.
        if rows and not _is_number(rows[0][0]):
            rows = [r[1:] for r in rows]
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise NetworkError(f"{path}: rows have different lengths {sorted(widths)}")
    matrix = _numeric(rows, path)
    if matrix.shape[0] != matrix.shape[1]:
        raise NetworkError(
            f"{path}: the matrix is {matrix.shape[0]} x {matrix.shape[1]}, "
            "not square"
        )
    return matrix


def read_attributes(path: Path | str) -> dict[str, np.ndarray]:
    """Read the node attribute table as ``{column name: values}``.

    Column order is preserved.  Columns with no header get ``col1``,
    ``col2``, ... so that every column can still be named on the command line.
    """
    path = Path(path)
    rows = _rows(path)
    if not rows:
        raise NetworkError(f"{path}: no attributes in the file")
    if _looks_like_header(rows[0]):
        names = list(rows[0])
        rows = rows[1:]
    else:
        names = [f"col{i + 1}" for i in range(len(rows[0]))]
    widths = {len(r) for r in rows}
    if widths != {len(names)}:
        raise NetworkError(
            f"{path}: {len(names)} column names but rows of width "
            f"{sorted(widths)}"
        )
    values = _numeric(rows, path)
    out: dict[str, np.ndarray] = {}
    for index, name in enumerate(names):
        key = name
        suffix = 2
        while key in out:  # duplicated headers happen
            key = f"{name}_{suffix}"
            suffix += 1
        out[key] = values[:, index]
    return out


# --------------------------------------------------------------------------- #
# the dataset
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NetworkData:
    """One network: where the nodes are, how they connect, what they are."""

    coordinates: np.ndarray                       #: (n, 3) MNI mm
    matrix: np.ndarray                            #: (n, n), row -> column
    labels: tuple[str, ...] = ()
    attributes: Mapping[str, np.ndarray] = field(default_factory=dict)
    name: str = ""
    sources: Mapping[str, Path] = field(default_factory=dict)

    # -- shape -------------------------------------------------------------- #

    @property
    def n_nodes(self) -> int:
        return int(self.coordinates.shape[0])

    @property
    def attribute_names(self) -> tuple[str, ...]:
        return tuple(self.attributes)

    def attribute(self, name: str) -> np.ndarray:
        """One attribute column, by name, with a helpful error if absent."""
        try:
            return np.asarray(self.attributes[name], dtype=float)
        except KeyError:
            known = ", ".join(self.attributes) or "(none)"
            raise NetworkError(
                f"no attribute named {name!r}; the table has: {known}"
            ) from None

    def label(self, index: int) -> str:
        if 0 <= index < len(self.labels):
            return self.labels[index]
        return f"node {index + 1}"

    # -- what the matrix is ------------------------------------------------- #

    @property
    def is_directed(self) -> bool:
        """True when the matrix is not symmetric to within rounding."""
        return not np.allclose(self.matrix, self.matrix.T, atol=1e-9, rtol=0.0)

    @property
    def asymmetry(self) -> float:
        """How directed the matrix is: ``|M - M'| / |M|``, 0 when symmetric."""
        total = float(np.abs(self.matrix).sum())
        if total == 0.0:
            return 0.0
        return float(np.abs(self.matrix - self.matrix.T).sum() / total)

    @property
    def has_negative(self) -> bool:
        return bool((self._offdiag() < 0).any())

    def _offdiag(self) -> np.ndarray:
        mask = ~np.eye(self.n_nodes, dtype=bool)
        return self.matrix[mask]

    # -- degrees ------------------------------------------------------------ #

    def in_degree(self, *, absolute: bool = True) -> np.ndarray:
        """Column sums, excluding the diagonal."""
        return self._degree(axis=0, absolute=absolute)

    def out_degree(self, *, absolute: bool = True) -> np.ndarray:
        """Row sums, excluding the diagonal."""
        return self._degree(axis=1, absolute=absolute)

    def _degree(self, *, axis: int, absolute: bool) -> np.ndarray:
        matrix = np.array(self.matrix, dtype=float, copy=True)
        np.fill_diagonal(matrix, 0.0)
        if absolute:
            matrix = np.abs(matrix)
        return matrix.sum(axis=axis)

    # -- description -------------------------------------------------------- #

    def describe(self) -> str:
        bits = [f"{self.n_nodes} nodes"]
        bits.append("directed" if self.is_directed else "symmetric")
        if self.has_negative:
            n_neg = int((self._offdiag() < 0).sum())
            bits.append(f"{n_neg}/{self._offdiag().size} negative")
        if self.attributes:
            bits.append(f"attributes: {', '.join(self.attributes)}")
        head = self.name or "network"
        return f"{head} ({'; '.join(bits)})"


def _validate(data: NetworkData) -> NetworkData:
    n = data.n_nodes
    if n == 0:
        raise NetworkError("the dataset has no nodes")
    if data.matrix.shape != (n, n):
        raise NetworkError(
            f"{n} coordinates but the matrix is {data.matrix.shape[0]} x "
            f"{data.matrix.shape[1]}; the two files describe different networks"
        )
    if data.labels and len(data.labels) != n:
        raise NetworkError(
            f"{n} coordinates but {len(data.labels)} labels"
        )
    for key, values in data.attributes.items():
        if len(values) != n:
            raise NetworkError(
                f"{n} coordinates but attribute {key!r} has {len(values)} rows"
            )
    if not np.isfinite(data.matrix).all():
        raise NetworkError("the matrix contains NaN or infinity")
    return data


def load_network(
    coordinates: Path | str,
    matrix: Path | str,
    *,
    labels: Optional[Path | str] = None,
    attributes: Optional[Path | str] = None,
    name: str = "",
) -> NetworkData:
    """Read a dataset from four files.  ``labels`` and ``attributes`` optional."""
    sources: dict[str, Path] = {
        "coordinates": Path(coordinates),
        "matrix": Path(matrix),
    }
    coords = read_coordinates(coordinates)
    mat = read_matrix(matrix)
    names: tuple[str, ...] = ()
    if labels is not None:
        names = read_labels(labels)
        sources["labels"] = Path(labels)
    attrs: dict[str, np.ndarray] = {}
    if attributes is not None:
        attrs = read_attributes(attributes)
        sources["attributes"] = Path(attributes)
    data = NetworkData(
        coordinates=coords,
        matrix=mat,
        labels=names,
        attributes=attrs,
        name=name or Path(matrix).stem,
        sources=sources,
    )
    return _validate(data)


def _one(candidates: Iterable[Path], what: str, where: Path) -> Optional[Path]:
    found = sorted(candidates)
    if not found:
        return None
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise NetworkError(
            f"{where}: more than one {what} file ({names}); name the file "
            "you want explicitly"
        )
    return found[0]


def load_network_dir(
    directory: Path | str,
    *,
    dataset: str = "",
    absolute: bool = False,
) -> NetworkData:
    """Read the dataset laid out in ``directory``.

    Looks for ``coordinates.txt`` and ``labels.txt``, then for a matrix and an
    attribute table.  With several of those present, ``dataset`` picks one by
    the part of the filename after ``matrix_``:

        load_network_dir("Neuromarvl_input", dataset="listen")

    ``absolute=True`` prefers the ``*_abs.txt`` pair, which holds ``|weight|``
    so that negative connections are drawn too.  Without it, ``*_abs`` files
    are ignored.
    """
    where = Path(directory)
    if not where.is_dir():
        raise NetworkError(f"{where}: not a directory")

    coords = _one(where.glob("coordinates*.txt"), "coordinates", where)
    if coords is None:
        raise NetworkError(f"{where}: no coordinates*.txt")
    labels = _one(where.glob("labels*.txt"), "labels", where)

    def pick(prefix: str) -> Optional[Path]:
        hits = [p for p in where.glob(f"{prefix}*.txt")]
        if dataset:
            stems = {f"{prefix}_{dataset}", f"{prefix}_{dataset}_abs"}
            hits = [p for p in hits if p.stem in stems]
        if absolute:
            abs_hits = [p for p in hits if p.stem.endswith("_abs")]
            if abs_hits:
                hits = abs_hits
        else:
            hits = [p for p in hits if not p.stem.endswith("_abs")]
        return _one(hits, prefix, where)

    matrix = pick("matrix")
    if matrix is None:
        which = f" for dataset {dataset!r}" if dataset else ""
        raise NetworkError(f"{where}: no matrix*.txt{which}")
    attributes = pick("attributes")

    stem = matrix.stem
    for lead in ("matrix_", "matrix"):
        if stem.startswith(lead):
            stem = stem[len(lead) :]
            break
    return load_network(
        coords, matrix, labels=labels, attributes=attributes,
        name=stem or where.name,
    )
