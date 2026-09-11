"""Per-machine configuration: resolution order, ``extends``, path anchoring."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from cifti_state.config import (
    ConfigError,
    Settings,
    current_machine,
    load_settings,
    machine_config_path,
    resolution_chain,
)


def write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# machine identity
# --------------------------------------------------------------------------- #


def test_machine_name_is_slugified(monkeypatch):
    monkeypatch.setenv("CIFTI_STATE_MACHINE", "LASM-Node01.lab.example.edu")
    assert current_machine() == "lasm-node01"


def test_machine_env_overrides_hostname(monkeypatch):
    monkeypatch.setenv("CIFTI_STATE_MACHINE", "lasm-cluster")
    assert current_machine() == "lasm-cluster"
    assert machine_config_path().name == "lasm-cluster.yaml"


def test_resolution_chain_is_ordered(monkeypatch, tmp_path):
    monkeypatch.setenv("CIFTI_STATE_CONFIG", str(tmp_path / "env.yaml"))
    monkeypatch.setenv("CIFTI_STATE_MACHINE", "somebox")
    chain = resolution_chain(tmp_path / "explicit.yaml")
    sources = [entry["source"] for entry in chain]
    assert sources[0] == "--config"
    assert sources[1] == "$CIFTI_STATE_CONFIG"
    assert sources[2].startswith("machine (somebox)")
    assert sources[3] == "user"
    assert sources[4].startswith("platform")


# --------------------------------------------------------------------------- #
# extends
# --------------------------------------------------------------------------- #


def test_child_overrides_parent(tmp_path):
    parent = write(
        tmp_path / "parent.yaml",
        {
            "resources": {"root": "/templates", "atlas_dir": "/templates"},
            "defaults": {"extent": 20, "atlas": "Glasser_2016"},
            "runtime": {"n_jobs": 4},
        },
    )
    child = write(
        tmp_path / "child.yaml",
        {
            "extends": parent.name,
            "machine": {"name": "childbox", "description": "test"},
            "defaults": {"extent": 50},
            "runtime": {"n_jobs": 16},
        },
    )
    settings = load_settings(child)
    assert settings.defaults.extent == 50          # overridden
    assert settings.defaults.atlas == "Glasser_2016"   # inherited
    assert settings.runtime.n_jobs == 16
    assert str(settings.resources.root) == str(Path("/templates"))
    assert settings.machine.name == "childbox"
    assert settings.inherited_from == [parent.resolve()]


def test_blank_child_value_inherits_rather_than_clearing(tmp_path):
    """A scaffolded machine file is full of empty placeholders; they must not wipe."""
    parent = write(
        tmp_path / "parent.yaml",
        {
            "workbench": {"wb_command": "/opt/workbench/bin_linux64/wb_command"},
            "resources": {"root": "/templates"},
        },
    )
    child = write(
        tmp_path / "child.yaml",
        {
            "extends": parent.name,
            "workbench": {"wb_command": None, "wb_view": ""},
            "resources": {"root": None},
        },
    )
    settings = load_settings(child)
    assert str(settings.workbench.wb_command).endswith("wb_command")
    assert str(settings.resources.root) == str(Path("/templates"))


def test_relative_paths_anchor_to_their_own_file(tmp_path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "machines").mkdir()
    parent = write(
        tmp_path / "parent.yaml", {"resources": {"root": "templates"}}
    )
    child = write(
        tmp_path / "machines" / "box.yaml",
        {"extends": "../parent.yaml", "runtime": {"tmp_dir": "scratch"}},
    )
    settings = load_settings(child)
    # parent's 'templates' resolved next to parent.yaml ...
    assert settings.resources.root == (tmp_path / "templates").resolve()
    # ... and the child's 'scratch' next to the child.
    assert settings.runtime.tmp_dir == (tmp_path / "machines" / "scratch").resolve()


def test_extends_can_chain(tmp_path):
    base = write(tmp_path / "base.yaml", {"defaults": {"extent": 10, "atlas": "AAL"}})
    middle = write(
        tmp_path / "middle.yaml", {"extends": "base.yaml", "defaults": {"extent": 20}}
    )
    leaf = write(
        tmp_path / "leaf.yaml", {"extends": "middle.yaml", "defaults": {"extent": 30}}
    )
    settings = load_settings(leaf)
    assert settings.defaults.extent == 30
    assert settings.defaults.atlas == "AAL"
    assert len(settings.inherited_from) == 2


def test_circular_extends_is_reported(tmp_path):
    write(tmp_path / "a.yaml", {"extends": "b.yaml"})
    write(tmp_path / "b.yaml", {"extends": "a.yaml"})
    with pytest.raises(ConfigError, match="circular"):
        load_settings(tmp_path / "a.yaml")


def test_missing_parent_is_reported(tmp_path):
    child = write(tmp_path / "child.yaml", {"extends": "nope.yaml"})
    with pytest.raises(ConfigError, match="does not exist"):
        load_settings(child)


def test_machine_name_defaults_to_filename(tmp_path):
    path = write(tmp_path / "lasm-node07.yaml", {"defaults": {"extent": 5}})
    assert load_settings(path).machine.name == "lasm-node07"


# --------------------------------------------------------------------------- #
# the shipped machine profiles
# --------------------------------------------------------------------------- #


def test_shipped_machine_profiles_parse():
    from cifti_state.config import machine_config_dir

    directory = machine_config_dir()
    if not directory.exists():
        pytest.skip("no machines directory")
    files = sorted(directory.glob("*.yaml"))
    assert files, "expected at least one machine profile"
    for path in files:
        settings = load_settings(path)
        assert isinstance(settings, Settings)
        assert settings.machine.name, f"{path.name} has no machine.name"
        # every shipped profile must inherit its platform template
        assert settings.inherited_from, f"{path.name} does not extend a template"
        # and must have inherited the surface file names from it
        assert settings.resources.surfaces.get("left"), f"{path.name} has no surfaces"


def test_windows_profile_points_at_workbench_15():
    from cifti_state.config import machine_config_dir

    path = machine_config_dir() / "laptop-ghlod870.yaml"
    if not path.exists():
        pytest.skip("laptop profile not present")
    settings = load_settings(path)
    assert str(settings.workbench.wb_command).endswith("wb_command.exe")
    assert str(settings.workbench.wb_view).endswith("wb_view.exe")
    assert "workbench-windows64-v1.5.0" in str(settings.workbench.wb_command)
    # inherited from the Windows template rather than restated
    assert settings.resources.surfaces["left"]["inflated"].endswith(
        "fs_LR.32k.L.inflated.surf.gii"
    )


# --------------------------------------------------------------------------- #
# cross-platform path handling
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    ["D:/data/templates", "D:\\data\\templates", "\\\\server\\share\\templates",
     "/shared/templates"],
)
def test_absolute_paths_are_not_reanchored(tmp_path, value):
    """A Windows profile read on Linux (or in CI) must keep its drive paths."""
    parent = write(tmp_path / "parent.yaml", {"defaults": {"extent": 20}})
    child = write(
        tmp_path / "child.yaml",
        {"extends": parent.name, "resources": {"root": value}},
    )
    settings = load_settings(child)
    assert str(settings.resources.root).replace("\\", "/") == value.replace("\\", "/")
    assert str(tmp_path) not in str(settings.resources.root)


def test_windows_profile_paths_survive_on_any_platform():
    from cifti_state.config import machine_config_dir

    path = machine_config_dir() / "laptop-ghlod870.yaml"
    if not path.exists():
        pytest.skip("laptop profile not present")
    settings = load_settings(path)
    for value in (
        settings.workbench.wb_command,
        settings.workbench.wb_view,
        settings.resources.root,
        settings.resources.atlas_csv_dir,
        settings.runtime.tmp_dir,
        settings.resources.neighbor_path("32k", "left"),
        settings.resources.surface_path("left", "inflated"),
    ):
        assert str(value).startswith("D:/"), f"{value} was re-anchored"
