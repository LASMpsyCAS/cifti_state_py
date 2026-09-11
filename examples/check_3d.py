#!/usr/bin/env python
"""Check the interactive 3D view, one layer at a time.

    python examples/check_3d.py                 # interactive windows
    python examples/check_3d.py --offscreen     # no windows, saves PNGs
    python examples/check_3d.py --data D:/path/to/map.dscalar.nii

If the 3D tab in the interface is blank, run this: it tests the stack from the
bottom up and stops at the first thing that fails, so you learn *which* layer
is the problem rather than guessing.

    1  imports            pyvista / pyvistaqt / vtk are installed
    2  OpenGL context     VTK can get a render window at all
    3  plain window       a PyVista window opens and draws
    4  your surfaces      the fs_LR meshes load and colour correctly
    5  Qt-embedded        the same scene inside a Qt widget -- what the GUI uses

Close each window to move to the next step.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

BANNER = "=" * 68
STEPS_PASSED: list[str] = []


def head(number: int, title: str) -> None:
    print(f"\n{BANNER}\n {number}. {title}\n{BANNER}")


def ok(message: str) -> None:
    print(f"   PASS  {message}")


def bad(message: str, advice: str = "") -> None:
    print(f"   FAIL  {message}")
    if advice:
        for line in advice.strip().splitlines():
            print(f"         {line}")


# --------------------------------------------------------------------------- #


def step_imports() -> bool:
    head(1, "imports")
    from cifti_state.viz.interactive import check_pyvista

    info = check_pyvista()
    for name, key in (("pyvista", "pyvista"), ("pyvistaqt", "pyvistaqt"), ("vtk", "vtk")):
        version = info.get(key)
        print(f"   {name:<12}: {version or 'NOT INSTALLED'}")
    if not info.get("pyvista") or not info.get("pyvistaqt"):
        bad(
            "the interactive view needs both packages",
            "pip install pyvista pyvistaqt",
        )
        return False
    ok("all three are importable")
    return True


def step_context(offscreen: bool) -> bool:
    head(2, "OpenGL context")
    try:
        import vtk

        window = vtk.vtkRenderWindow()
        window.SetOffScreenRendering(1 if offscreen else 0)
        window.SetSize(64, 64)
        window.Render()
        ok(f"VTK {vtk.vtkVersion.GetVTKVersion()} got a render window")
        try:
            print(f"   renderer    : {window.ReportCapabilities().splitlines()[1][:70]}")
        except Exception:
            pass
        window.Finalize()
        return True
    except Exception as exc:
        bad(
            f"VTK could not get a render window: {exc}",
            "This is the usual cause of a blank 3D tab.\n"
            "  - Remote Desktop often cannot provide OpenGL; try a local session.\n"
            "  - Update the graphics driver.\n"
            "  - A virtual machine may need 3D acceleration enabled.\n"
            "The Figure tab in the interface does not need this.",
        )
        return False


def step_plain_window(offscreen: bool, out_dir: Path) -> bool:
    head(3, "a plain PyVista window")
    try:
        import pyvista as pv

        sphere = pv.Sphere(radius=1.0, theta_resolution=48, phi_resolution=48)
        sphere["height"] = sphere.points[:, 2]
        plotter = pv.Plotter(off_screen=offscreen, window_size=(700, 520))
        plotter.add_mesh(sphere, scalars="height", cmap="viridis", smooth_shading=True)
        plotter.set_background("#EDF5F4")
        plotter.add_text("close this window to continue", font_size=10)
        if offscreen:
            path = out_dir / "check_3d_sphere.png"
            plotter.screenshot(str(path))
            plotter.close()
            ok(f"drew offscreen -> {path}")
        else:
            print("   a window should have opened; rotate it, then close it")
            plotter.show()
            ok("the window opened and closed")
        return True
    except Exception as exc:
        bad(f"PyVista could not draw: {exc}")
        traceback.print_exc()
        return False


def step_surfaces(settings, data_path, offscreen: bool, out_dir: Path):
    head(4, "your surfaces and data")
    try:
        from cifti_state.core import compute_threshold, find_clusters
        from cifti_state.pipeline import build_adjacency, load_input
        from cifti_state.results import AnalysisSpec
        from cifti_state.viz.interactive import build_scene

        spec = AnalysisSpec(input_path=data_path)
        stat_map = load_input(spec)
        described = stat_map.describe()
        print(f"   map         : {Path(data_path).name}")
        print(f"   layout      : {described['layout']}")

        adjacency = build_adjacency(stat_map, settings, spec)
        import numpy as np

        finite = stat_map.finite_values()
        positive = np.sort(finite[finite > 0])
        # Aim for a threshold that leaves a decent number of clusters to look
        # at, whatever the map's value range happens to be.
        clusters = None
        for quantile in (0.50, 0.70, 0.85, 0.95):
            value = float(positive[int(quantile * (positive.size - 1))])
            threshold = compute_threshold(finite, method="fixed", value=value)
            candidate = find_clusters(stat_map, adjacency, threshold, extent=20,
                                      legacy_mode=True)
            clusters = candidate
            if candidate.n_clusters >= 8:
                break
        print(f"   threshold   : {value:.4g}  ->  {clusters.n_clusters} clusters")
        if clusters.n_clusters == 0:
            bad("no clusters at that threshold; pass --data with a map that has some")
            return None

        scene = build_scene(
            stat_map, settings, clusters=clusters, mode="masked",
            surface=settings.render.surface, split_mm=15,
            title=Path(data_path).stem,
        )
        layer = scene.layers[0]
        print(f"   meshes      : {len(scene.layers)} hemispheres, "
              f"{layer.n_vertices} vertices each")
        print(f"   colour range: {scene.clim[0]:.4g} … {scene.clim[1]:.4g}")

        import pyvista as pv

        plotter = pv.Plotter(off_screen=offscreen, window_size=(900, 640))
        scene.add_to(plotter)
        if offscreen:
            path = out_dir / "check_3d_surface.png"
            plotter.screenshot(str(path))
            plotter.close()
            ok(f"drew your surfaces offscreen -> {path}")
        else:
            print("   drag to rotate, wheel to zoom, then close the window")
            plotter.show()
            ok("your surfaces drew and the window closed")
        return scene
    except Exception as exc:
        bad(f"could not build or draw the scene: {exc}")
        traceback.print_exc()
        return None


def step_qt_embedded(scene, offscreen: bool, out_dir: Path) -> bool:
    head(5, "the same scene inside a Qt window (what the interface uses)")
    if scene is None:
        bad("skipped -- step 4 did not produce a scene")
        return False
    try:
        import os

        if offscreen:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
        from pyvistaqt import QtInteractor

        from cifti_state.fonts import apply_to_qt
        from cifti_state.gui.theme import PALETTE, stylesheet

        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyle("Fusion")
        choice = apply_to_qt(app, pixel_size=13)
        app.setStyleSheet(stylesheet(PALETTE, fonts=choice))
        print(f"   font        : {choice.ui}  (CJK fallback: {choice.cjk or 'not needed'})")

        window = QMainWindow()
        window.setWindowTitle("cifti_state — 3D check (close to finish)")
        window.resize(940, 680)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        plotter = QtInteractor(central)
        layout.addWidget(plotter.interactor)
        window.setCentralWidget(central)

        scene.add_to(plotter)
        window.show()

        if offscreen:
            app.processEvents()
            print("   NOTE  offscreen: the widget was created, but this does not")
            print("         prove it renders on screen. Run without --offscreen")
            print("         on the machine you will actually use.")
            plotter.close()
            return True

        print("   rotate it, resize the window, then close it")
        app.exec()
        plotter.close()
        ok("the embedded 3D view worked -- the interface will too")
        return True
    except Exception as exc:
        bad(
            f"the Qt-embedded view failed: {exc}",
            "Step 3 passing but this failing usually means a Qt/VTK version\n"
            "mismatch. Try: pip install -U pyvista pyvistaqt PySide6",
        )
        traceback.print_exc()
        return False


# --------------------------------------------------------------------------- #


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_config(explicit):
    """Use the bundled example configuration when no profile exists yet."""
    if explicit is not None:
        return explicit
    import os

    if os.environ.get("CIFTI_STATE_CONFIG"):
        return None
    from cifti_state.config import current_machine

    if (_repo_root() / "configs" / "machines" / f"{current_machine()}.yaml").exists():
        return None
    bundled = _repo_root() / "example_data" / "example.yaml"
    return bundled if bundled.exists() else None


def _find_example_map(settings):
    """A statistic map to draw: the bundled one, or one beside the templates."""
    folders = [_repo_root() / "example_data" / "maps"]
    if settings.resources.root is not None:
        folders.append(settings.resources.root.parent / "Example_test")
    for folder in folders:
        if not folder.exists():
            continue
        found = sorted(
            p for p in folder.glob("*.dscalar.nii") if "cluster" not in p.name
        )
        if found:
            return found[0]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="a map to draw (default: the example)")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--offscreen", action="store_true",
                        help="do not open windows; save PNGs instead")
    parser.add_argument("--out", type=Path, default=Path("."),
                        help="where to put the offscreen PNGs")
    args = parser.parse_args(argv)

    from cifti_state import configure_logging, load_settings
    from cifti_state.fonts import configure_stdio

    configure_stdio()
    configure_logging("WARNING")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not step_imports():
        return 2
    if not step_context(args.offscreen):
        print("\nStopping here: without an OpenGL context nothing 3D can draw.")
        return 1
    if not step_plain_window(args.offscreen, out_dir):
        return 1

    settings = load_settings(_resolve_config(args.config))
    problems = settings.validate()
    if problems:
        print("\nConfiguration problems, so steps 4-5 cannot run:")
        for problem in problems:
            print(f"  - {problem}")
        print("Fix them with: cifti-state check")
        return 1

    data = args.data or _find_example_map(settings)
    if data is None or not Path(data).exists():
        print("\nNo map to draw. Pass --data path/to/map.dscalar.nii")
        return 1

    scene = step_surfaces(settings, data, args.offscreen, out_dir)
    step_qt_embedded(scene, args.offscreen, out_dir)

    print(f"\n{BANNER}\n Done. If every step passed, run:  cifti-state-gui\n{BANNER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
