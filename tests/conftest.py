"""Test fixtures.

The data-backed tests -- including the MATLAB regression -- run against the
bundled ``example_data/`` by default, so ``pytest`` on a fresh clone exercises
everything without any setup.

To run them against a different copy of the data instead, point
``CIFTI_STATE_TEST_CONFIG`` at a configuration file and
``CIFTI_STATE_TEST_EXAMPLES`` at the folder holding the maps.  Whichever is
set wins; whatever is neither set nor bundled is skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from cifti_state.config import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_CONFIG = REPO_ROOT / "example_data" / "example.yaml"
BUNDLED_MAPS = REPO_ROOT / "example_data" / "maps"


@pytest.fixture(scope="session")
def settings() -> Settings:
    config = os.environ.get("CIFTI_STATE_TEST_CONFIG")
    if config and Path(config).exists():
        return load_settings(config)
    if BUNDLED_CONFIG.exists():
        return load_settings(BUNDLED_CONFIG)
    pytest.skip("set CIFTI_STATE_TEST_CONFIG to run data-backed tests")


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    path = os.environ.get("CIFTI_STATE_TEST_EXAMPLES")
    if path and Path(path).exists():
        return Path(path)
    if (BUNDLED_MAPS / "group_mean_thresh_fdr_E_C.dscalar.nii").exists():
        return BUNDLED_MAPS
    pytest.skip("set CIFTI_STATE_TEST_EXAMPLES to a folder with the example maps")


@pytest.fixture
def line_graph():
    """A 10-vertex path graph, handy for exact clustering assertions."""
    from scipy.sparse import csr_matrix

    n = 10
    rows = list(range(n - 1)) + list(range(1, n))
    cols = list(range(1, n)) + list(range(n - 1))
    data = np.ones(len(rows), dtype=np.int8)
    return csr_matrix((data, (rows, cols)), shape=(n, n))


@pytest.fixture
def toy_map(line_graph):
    """A tiny two-hemisphere SurfaceStatMap on the path graph above."""
    from cifti_state.io.cifti import HemiSurfaceData, SurfaceStatMap

    def hemi(values, name):
        values = np.asarray(values, dtype=float)
        n = values.size
        return HemiSurfaceData(
            values=values,
            present=np.ones(n, dtype=bool),
            vertex_index=np.arange(n),
            n_vertices=n,
            hemisphere=name,
        )

    left = [3.0, 3.0, 3.0, 0.0, 0.0, -3.0, -3.0, -3.0, -3.0, 0.0]
    right = [0.0, 4.0, 4.0, 4.0, 4.0, 0.0, 0.0, 2.0, 0.0, 0.0]
    return SurfaceStatMap(
        left=hemi(left, "left"),
        right=hemi(right, "right"),
        statistic="z",
        name="toy",
    )
