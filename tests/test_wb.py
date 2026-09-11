"""The wb_command wrapper, exercised against a stub executable.

These tests do not need Connectome Workbench installed: they check what the
wrapper *does* — how it builds the argument list, that it never goes through a
shell, that it reports failures usefully, and that paths with spaces survive.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cifti_state.config import ConfigError, Settings, WorkbenchSettings
from cifti_state.wb import WbResult, WorkbenchError, probe, run_wb, smooth_cifti

STUB = """\
#!{python}
import sys, json
args = sys.argv[1:]
if args and args[0] == "-version":
    print("Connectome Workbench")
    print("Version: 1.5.0")
    sys.exit(0)
if args and args[0] == "-fail":
    sys.stderr.write("stub failure: bad argument\\n")
    sys.exit(3)
print(json.dumps(args))
sys.exit(0)
"""


@pytest.fixture
def stub_workbench(tmp_path):
    """A directory whose name contains a space, holding a fake wb_command."""
    folder = tmp_path / "Program Files" / "workbench" / "bin"
    folder.mkdir(parents=True)
    exe = folder / ("wb_command.exe" if os.name == "nt" else "wb_command")
    exe.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def stub_settings(stub_workbench, tmp_path):
    settings = Settings()
    settings.workbench = WorkbenchSettings(wb_command=stub_workbench, wb_view=None)
    settings.resources.root = tmp_path / "templates"
    settings.resources.root.mkdir(exist_ok=True)
    for hemi, letter in (("left", "L"), ("right", "R")):
        name = f"fs_LR.32k.{letter}.midthickness.surf.gii"
        (settings.resources.root / name).write_text("stub", encoding="utf-8")
        settings.resources.surfaces[hemi] = {"midthickness": name}
    return settings


@pytest.mark.skipif(os.name == "nt", reason="stub script needs a shebang")
def test_probe_reports_version(stub_settings):
    info = probe(stub_settings)
    assert info["available"] is True
    assert any("1.5.0" in line for line in info["version"])


def test_probe_reports_missing_workbench():
    settings = Settings()
    settings.workbench = WorkbenchSettings(
        wb_command=Path("/definitely/not/here/wb_command"), wb_view=None
    )
    info = probe(settings)
    assert info["available"] is False


def test_missing_executable_raises_config_error():
    settings = Settings()
    settings.workbench = WorkbenchSettings(
        wb_command=Path("/definitely/not/here/wb_command"), wb_view=None
    )
    with pytest.raises(ConfigError, match="does not exist"):
        run_wb(["-version"], settings)


@pytest.mark.skipif(os.name == "nt", reason="stub script needs a shebang")
def test_arguments_are_passed_through_verbatim(stub_settings):
    result = run_wb(["-some-command", "a b.nii", "42"], stub_settings)
    assert result.ok
    assert '"a b.nii"' in result.command_line       # quoted for display only
    import json

    assert json.loads(result.stdout) == ["-some-command", "a b.nii", "42"]


@pytest.mark.skipif(os.name == "nt", reason="stub script needs a shebang")
def test_failure_raises_with_the_command_line_attached(stub_settings):
    with pytest.raises(WorkbenchError) as excinfo:
        run_wb(["-fail"], stub_settings)
    message = str(excinfo.value)
    assert "exit 3" in message
    assert "stub failure" in message
    assert "-fail" in message


@pytest.mark.skipif(os.name == "nt", reason="stub script needs a shebang")
def test_check_false_returns_the_failed_result(stub_settings):
    result = run_wb(["-fail"], stub_settings, check=False)
    assert isinstance(result, WbResult)
    assert result.returncode == 3
    assert not result.ok


@pytest.mark.skipif(os.name == "nt", reason="stub script needs a shebang")
def test_smooth_cifti_builds_the_expected_command(stub_settings, tmp_path):
    import json

    result = smooth_cifti(
        tmp_path / "in.dscalar.nii",
        tmp_path / "out.dscalar.nii",
        stub_settings,
        surface_kernel=2.0,
        volume_kernel=2.0,
    )
    args = json.loads(result.stdout)
    # wb_command -cifti-smoothing <in> <surf-kernel> <vol-kernel> <direction> <out>
    assert args[0] == "-cifti-smoothing"
    assert args[1].endswith("in.dscalar.nii")
    assert (args[2], args[3]) == ("2.0", "2.0")
    assert args[4] == "COLUMN"
    assert args[5].endswith("out.dscalar.nii")
    assert "-left-surface" in args and "-right-surface" in args
    left = args[args.index("-left-surface") + 1]
    assert left.endswith("fs_LR.32k.L.midthickness.surf.gii")
    assert result.outputs == [tmp_path / "out.dscalar.nii"]


def test_command_line_quotes_only_what_needs_it(stub_settings):
    result = WbResult(
        command=["/opt/wb/wb_command", "-x", "/a b/c.nii"],
        returncode=0, stdout="", stderr="", duration=0.0,
    )
    assert result.command_line == '/opt/wb/wb_command -x "/a b/c.nii"'
