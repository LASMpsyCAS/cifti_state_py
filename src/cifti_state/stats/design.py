"""Who is in the analysis, and what model is being fitted to them.

A study is described by a **participants table** -- a CSV or TSV with one row
per scan, one column holding the path to that scan's surface map, and whatever
other columns describe it (group, condition, subject, age, sex, motion).  That
table is the only thing a test needs to know about the study::

    subject,group,age,file
    sub-01,patient,34,maps/sub-01_zstat.dscalar.nii
    sub-02,control,29,maps/sub-02_zstat.dscalar.nii
    ...

From it, :func:`one_sample_design`, :func:`two_sample_design` and
:func:`paired_design` build a :class:`Design`: the model matrix, the contrast,
and the ordered list of files that supplies each row of the data matrix.  The
design is a plain description -- nothing here reads an image or fits anything,
so a design can be inspected, printed and checked before any heavy lifting.

Two decisions worth knowing about:

* **Continuous covariates are mean-centred.**  Without it the intercept of a
  one-sample model means "the value at age zero" rather than "the mean at the
  average age", and the test is then answering a question nobody asked.
  Centring changes neither the two-sample group contrast nor the model fit, so
  it costs nothing and removes a real trap.
* **A paired test is expressed as differences.**  ``paired_design`` pairs the
  rows up and hands back the two file lists; the data matrix is the difference,
  and the model is then a one-sample test on it.  That is algebraically the
  same as a model with one dummy per subject, but it does not put N nuisance
  columns into the design, and it makes the residuals -- which the RFT
  correction depends on -- the residuals of the thing actually being tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from ..logging_setup import get_logger

log = get_logger(__name__)

__all__ = [
    "Participants",
    "Design",
    "DesignError",
    "load_participants",
    "one_sample_design",
    "two_sample_design",
    "paired_design",
]

#: Column names searched, in order, when the file column is not named.
FILE_COLUMN_CANDIDATES: tuple[str, ...] = (
    "file", "path", "filename", "filepath", "map", "image", "cifti", "dscalar",
)


class DesignError(ValueError):
    """The participants table cannot support the requested model."""


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #


@dataclass
class Participants:
    """A participants table with its file paths resolved and checked."""

    frame: pd.DataFrame
    file_column: str
    source: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def paths(self) -> list[Path]:
        return [Path(p) for p in self.frame[self.file_column]]

    def missing(self) -> list[Path]:
        return [p for p in self.paths if not p.exists()]

    def require_columns(self, *names: str) -> None:
        absent = [n for n in names if n and n not in self.frame.columns]
        if absent:
            raise DesignError(
                f"the participants table has no column(s) {absent}; "
                f"it has {list(self.frame.columns)}"
            )

    def describe(self) -> str:
        parts = [f"{len(self)} rows"]
        if self.source is not None:
            parts.append(self.source.name)
        return ", ".join(parts)


def load_participants(
    path: Path | str,
    *,
    file_column: Optional[str] = None,
    root: Optional[Path | str] = None,
    sep: Optional[str] = None,
    require_files: bool = True,
) -> Participants:
    """Read a participants CSV/TSV and resolve its file paths.

    *root* anchors relative paths; it defaults to the directory the table lives
    in, which is what makes a study folder portable -- the table and the maps
    can be moved together and nothing has to be rewritten.
    """
    path = Path(path)
    if not path.exists():
        raise DesignError(f"participants table not found: {path}")
    if sep is None:
        sep = "\t" if path.suffix.lower() in (".tsv", ".tab") else ","

    frame = pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)
    frame = frame.rename(columns=lambda c: str(c).strip())
    if frame.empty:
        raise DesignError(f"{path.name} has no rows")

    column = file_column or _guess_file_column(frame)
    if column not in frame.columns:
        raise DesignError(
            f"{path.name}: no column named {column!r}; "
            f"columns are {list(frame.columns)}"
        )

    anchor = Path(root) if root is not None else path.parent
    resolved = []
    for raw in frame[column]:
        candidate = Path(str(raw).strip())
        resolved.append(candidate if candidate.is_absolute() else anchor / candidate)
    frame[column] = resolved

    # Anything that is not the file column and parses as a number becomes one,
    # so covariates arrive as numbers and group labels stay as strings.
    for name in frame.columns:
        if name == column:
            continue
        converted = pd.to_numeric(frame[name], errors="coerce")
        if converted.notna().all():
            frame[name] = converted

    participants = Participants(frame=frame, file_column=column, source=path)

    absent = participants.missing()
    if absent and require_files:
        preview = "\n  ".join(str(p) for p in absent[:5])
        raise DesignError(
            f"{len(absent)} of {len(frame)} files in {path.name} do not exist "
            f"(paths are resolved against {anchor}):\n  {preview}"
            + ("\n  ..." if len(absent) > 5 else "")
        )
    log.info(
        "participants: %d rows from %s, file column %r",
        len(frame), path.name, column,
    )
    return participants


def _guess_file_column(frame: pd.DataFrame) -> str:
    lowered = {str(c).strip().lower(): str(c).strip() for c in frame.columns}
    for candidate in FILE_COLUMN_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    # Fall back to any column whose values look like surface files.
    for name in frame.columns:
        sample = str(frame[name].iloc[0])
        if sample.endswith((".nii", ".gii")):
            return str(name)
    raise DesignError(
        "could not tell which column holds the file paths; name it one of "
        f"{FILE_COLUMN_CANDIDATES} or pass file_column="
    )


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #


@dataclass
class Design:
    """A model matrix, a contrast, and the files that fill in the rows."""

    matrix: np.ndarray                   #: (n_observations, n_predictors)
    names: list[str]                     #: one per predictor
    contrast: np.ndarray                 #: (n_predictors,)
    contrast_name: str
    kind: str                            #: one_sample | two_sample | paired
    paths: list[Path]                    #: one per observation
    labels: list[str]                    #: observation labels, for messages
    subtract_paths: Optional[list[Path]] = None   #: paired: Y = paths - these
    groups: Optional[tuple[str, str]] = None
    rows: Optional[pd.DataFrame] = None
    notes: list[str] = field(default_factory=list)

    @property
    def n_observations(self) -> int:
        return int(self.matrix.shape[0])

    @property
    def n_predictors(self) -> int:
        return int(self.matrix.shape[1])

    @property
    def rank(self) -> int:
        return int(np.linalg.matrix_rank(self.matrix))

    @property
    def dof(self) -> int:
        """Residual degrees of freedom of the fitted model."""
        return self.n_observations - self.rank

    def validate(self) -> None:
        if self.n_observations < 3:
            raise DesignError(
                f"{self.n_observations} observations is not enough to fit "
                f"anything; a t-test needs at least 3"
            )
        if self.dof < 1:
            raise DesignError(
                f"the model has {self.n_predictors} predictors for "
                f"{self.n_observations} observations, leaving {self.dof} "
                f"residual degrees of freedom"
            )
        if self.rank < self.n_predictors:
            raise DesignError(
                "the model matrix is rank deficient -- two predictors carry the "
                "same information. Check for a covariate that is constant "
                "within group, or a duplicated column."
            )

    def describe(self) -> str:
        terms = " + ".join(self.names)
        line = (
            f"{self.kind}: {self.n_observations} observations, "
            f"{terms}  [contrast: {self.contrast_name}, df={self.dof}]"
        )
        return line

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "n_observations": self.n_observations,
            "predictors": list(self.names),
            "contrast": [float(c) for c in self.contrast],
            "contrast_name": self.contrast_name,
            "dof": self.dof,
            "groups": list(self.groups) if self.groups else None,
            "notes": list(self.notes),
        }


def one_sample_design(
    participants: Participants,
    *,
    covariates: Sequence[str] = (),
    label_column: Optional[str] = None,
) -> Design:
    """Is the mean map different from zero?

    The model is an intercept (plus any covariates) and the contrast tests the
    intercept.  With covariates mean-centred, that intercept is the group mean
    adjusted to the average covariate value.
    """
    participants.require_columns(*covariates)
    frame = participants.frame
    columns, names, notes = _covariate_columns(frame, covariates)

    matrix = np.column_stack([np.ones(len(frame))] + columns) if columns \
        else np.ones((len(frame), 1))
    predictor_names = ["intercept"] + names
    contrast = np.zeros(matrix.shape[1])
    contrast[0] = 1.0

    design = Design(
        matrix=matrix,
        names=predictor_names,
        contrast=contrast,
        contrast_name="mean > 0",
        kind="one_sample",
        paths=participants.paths,
        labels=_labels(frame, label_column, participants.file_column),
        rows=frame,
        notes=notes,
    )
    design.validate()
    log.info("design %s", design.describe())
    return design


def two_sample_design(
    participants: Participants,
    group_column: str,
    *,
    groups: Optional[Sequence[str]] = None,
    covariates: Sequence[str] = (),
    label_column: Optional[str] = None,
) -> Design:
    """Do two independent groups differ?

    The model is an intercept, one dummy for the second group, and any
    covariates; the contrast tests the dummy, so a positive statistic means
    *group 2 is larger than group 1*.  Which group is which is stated
    explicitly in the result rather than left to alphabetical order.
    """
    participants.require_columns(group_column, *covariates)
    frame = participants.frame

    values = frame[group_column].astype(str)
    present = list(pd.unique(values))
    if groups is None:
        if len(present) != 2:
            raise DesignError(
                f"column {group_column!r} has {len(present)} distinct values "
                f"{present[:6]}; a two-sample test needs exactly two. Pass "
                f"groups=('a', 'b') to choose which two."
            )
        chosen = tuple(sorted(present))
    else:
        chosen = tuple(str(g) for g in groups)
        if len(chosen) != 2:
            raise DesignError(f"groups must name exactly two values, got {chosen}")
        unknown = [g for g in chosen if g not in present]
        if unknown:
            raise DesignError(
                f"{unknown} not found in column {group_column!r}; "
                f"it holds {present[:8]}"
            )

    keep = values.isin(chosen).to_numpy()
    if not keep.all():
        log.info("dropping %d rows outside %s", int((~keep).sum()), list(chosen))
    frame = frame.loc[keep].reset_index(drop=True)
    values = values.loc[keep].reset_index(drop=True)

    counts = {g: int((values == g).sum()) for g in chosen}
    for group, n in counts.items():
        if n < 2:
            raise DesignError(f"group {group!r} has {n} observation(s); need at least 2")

    columns, names, notes = _covariate_columns(frame, covariates)
    dummy = (values == chosen[1]).to_numpy(dtype=float)

    matrix = np.column_stack([np.ones(len(frame)), dummy] + columns)
    predictor_names = ["intercept", f"{group_column}[{chosen[1]}]"] + names
    contrast = np.zeros(matrix.shape[1])
    contrast[1] = 1.0

    notes.append(f"group sizes: {chosen[0]} n={counts[chosen[0]]}, "
                 f"{chosen[1]} n={counts[chosen[1]]}")

    design = Design(
        matrix=matrix,
        names=predictor_names,
        contrast=contrast,
        contrast_name=f"{chosen[1]} - {chosen[0]}",
        kind="two_sample",
        paths=[Path(p) for p in frame[participants.file_column]],
        labels=_labels(frame, label_column, participants.file_column),
        groups=(str(chosen[0]), str(chosen[1])),
        rows=frame,
        notes=notes,
    )
    design.validate()
    log.info("design %s", design.describe())
    return design


def paired_design(
    participants: Participants,
    condition_column: str,
    subject_column: str,
    *,
    conditions: Optional[Sequence[str]] = None,
    covariates: Sequence[str] = (),
) -> Design:
    """Do two measurements of the same subjects differ?

    Rows are paired on *subject_column*; the data matrix becomes
    ``condition2 - condition1`` per subject and the model is then a one-sample
    test of that difference.  A subject missing either condition is dropped,
    loudly.
    """
    participants.require_columns(condition_column, subject_column, *covariates)
    frame = participants.frame

    values = frame[condition_column].astype(str)
    present = list(pd.unique(values))
    if conditions is None:
        if len(present) != 2:
            raise DesignError(
                f"column {condition_column!r} has {len(present)} distinct values "
                f"{present[:6]}; a paired test needs exactly two. Pass "
                f"conditions=('a', 'b') to choose which two."
            )
        chosen = tuple(sorted(present))
    else:
        chosen = tuple(str(c) for c in conditions)
        unknown = [c for c in chosen if c not in present]
        if unknown:
            raise DesignError(
                f"{unknown} not found in column {condition_column!r}; "
                f"it holds {present[:8]}"
            )

    first, second = chosen
    by_subject: dict[str, dict[str, int]] = {}
    for index, row in frame.iterrows():
        condition = str(row[condition_column])
        if condition not in chosen:
            continue
        subject = str(row[subject_column])
        slot = by_subject.setdefault(subject, {})
        if condition in slot:
            raise DesignError(
                f"subject {subject!r} has more than one row for condition "
                f"{condition!r}; a paired test needs exactly one of each"
            )
        slot[condition] = int(index)

    complete = [s for s, slot in by_subject.items() if len(slot) == 2]
    incomplete = sorted(set(by_subject) - set(complete))
    if incomplete:
        log.warning(
            "dropping %d subject(s) without both conditions: %s",
            len(incomplete), ", ".join(incomplete[:8]) + ("..." if len(incomplete) > 8 else ""),
        )
    if len(complete) < 3:
        raise DesignError(
            f"only {len(complete)} subject(s) have both {first!r} and {second!r}; "
            f"a paired test needs at least 3"
        )
    complete = sorted(complete)

    second_rows = [by_subject[s][second] for s in complete]
    first_rows = [by_subject[s][first] for s in complete]
    paired_frame = frame.loc[second_rows].reset_index(drop=True)

    columns, names, notes = _covariate_columns(paired_frame, covariates)
    matrix = np.column_stack([np.ones(len(complete))] + columns) if columns \
        else np.ones((len(complete), 1))
    predictor_names = ["intercept"] + names
    contrast = np.zeros(matrix.shape[1])
    contrast[0] = 1.0

    notes.append(f"{len(complete)} paired subjects")
    if incomplete:
        notes.append(f"{len(incomplete)} dropped for a missing condition")

    file_column = participants.file_column
    design = Design(
        matrix=matrix,
        names=predictor_names,
        contrast=contrast,
        contrast_name=f"{second} - {first}",
        kind="paired",
        paths=[Path(p) for p in frame.loc[second_rows, file_column]],
        subtract_paths=[Path(p) for p in frame.loc[first_rows, file_column]],
        labels=list(complete),
        groups=(str(first), str(second)),
        rows=paired_frame,
        notes=notes,
    )
    design.validate()
    log.info("design %s", design.describe())
    return design


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _covariate_columns(
    frame: pd.DataFrame, covariates: Iterable[str]
) -> tuple[list[np.ndarray], list[str], list[str]]:
    """Turn covariate column names into model columns.

    Numeric columns are mean-centred; anything else is dummy coded against its
    first level.  Both choices are reported back as notes so the model that was
    actually fitted is visible in the result.
    """
    columns: list[np.ndarray] = []
    names: list[str] = []
    notes: list[str] = []

    for name in covariates:
        series = frame[name]
        if pd.api.types.is_numeric_dtype(series):
            values = series.to_numpy(dtype=float)
            if not np.isfinite(values).all():
                raise DesignError(
                    f"covariate {name!r} has missing or non-finite values"
                )
            mean = float(values.mean())
            columns.append(values - mean)
            names.append(name)
            notes.append(f"covariate {name} mean-centred at {mean:.4g}")
            continue

        levels = list(pd.unique(series.astype(str)))
        if len(levels) < 2:
            raise DesignError(f"covariate {name!r} is constant; drop it")
        if len(levels) > 12:
            raise DesignError(
                f"covariate {name!r} has {len(levels)} levels; that is a label, "
                f"not a covariate"
            )
        reference = levels[0]
        for level in levels[1:]:
            columns.append((series.astype(str) == level).to_numpy(dtype=float))
            names.append(f"{name}[{level}]")
        notes.append(
            f"covariate {name} dummy coded against {reference!r} "
            f"({len(levels)} levels)"
        )
    return columns, names, notes


def _labels(
    frame: pd.DataFrame, label_column: Optional[str], file_column: str
) -> list[str]:
    if label_column and label_column in frame.columns:
        return [str(v) for v in frame[label_column]]
    for candidate in ("subject", "participant_id", "subject_id", "id", "sub"):
        if candidate in frame.columns:
            return [str(v) for v in frame[candidate]]
    return [Path(str(p)).name for p in frame[file_column]]
