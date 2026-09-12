"""The main window.

It owns the session state and wires the panels together.  All analysis goes
through the same functions the CLI uses, on a worker thread, so nothing here
duplicates behaviour and nothing blocks the interface.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import ConfigError, Settings, current_machine
from ..core.annotate import annotate_clusters
from ..core.cluster import find_clusters
from ..core.mask import suggest_mask_name
from ..core.peaks import cluster_peaks
from ..core.report import build_report, save_report
from ..core.threshold import compute_threshold
from ..io.atlas import list_atlases, load_atlas, load_registry
from ..io.cifti import load_surface_stat_map, save_like
from ..io.surface import load_hemisphere_surfaces
from ..logging_setup import get_logger
from ..pipeline import build_adjacency, save_cluster_mask
from ..results import AnalysisSpec
from .panels import (
    ClusterPanel,
    InputPanel,
    LogPanel,
    PreviewPanel,
    ReportPanel,
    TablePanel,
)
from .state import SessionState
from .theme import PALETTE
from .widgets import Pill
from .workers import Job, JobRunner

log = get_logger(__name__)

__all__ = ["MainWindow"]

SIDEBAR_WIDTH = 372


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.state = SessionState(settings=settings)
        self.runner = JobRunner(self)
        self._pending_report = False
        self._wb_available = False

        self.setWindowTitle("cifti_state — surface cluster analysis")
        self.resize(1500, 940)
        self.setMinimumSize(1180, 760)

        self._build_ui()
        self._connect()
        self._populate_from_settings()

        # One sweep over the finished window: every field that holds a number
        # gets the fixed-width family and the C locale, so digits cannot be
        # substituted by a proportional font's odd glyphs or reformatted by a
        # regional setting.
        from ..fonts import apply_numeric_fonts

        apply_numeric_fonts(
            self,
            settings=settings,
            pixel_size=int(settings.interface.font_size_px),
        )

    # ------------------------------------------------------------------ ui -- #

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        body = QWidget(central)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(12)

        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_workspace(), 1)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        self._build_status_bar()
        self._build_menus()

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("HeaderBar")
        header.setFixedHeight(66)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(14)

        titles = QWidget(header)
        titles_layout = QVBoxLayout(titles)
        titles_layout.setContentsMargins(0, 0, 0, 0)
        titles_layout.setSpacing(1)

        title = QLabel("cifti_state", titles)
        title.setObjectName("HeaderTitle")
        titles_layout.addWidget(title)

        subtitle = QLabel(
            "fsLR surface clustering, anatomical reporting and visualisation", titles
        )
        subtitle.setObjectName("HeaderSubtitle")
        titles_layout.addWidget(subtitle)

        layout.addWidget(titles)
        layout.addStretch(1)

        self.machine_chip = QLabel(header)
        self.machine_chip.setObjectName("HeaderChip")
        layout.addWidget(self.machine_chip)

        self.workbench_chip = QLabel(header)
        self.workbench_chip.setObjectName("HeaderChip")
        layout.addWidget(self.workbench_chip)

        return header

    def _build_sidebar(self) -> QWidget:
        self.input_panel = InputPanel()
        self.cluster_panel = ClusterPanel()
        self.report_panel = ReportPanel()

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(11)
        layout.addWidget(self.input_panel)
        layout.addWidget(self.cluster_panel)
        layout.addWidget(self.report_panel)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(SIDEBAR_WIDTH)
        return scroll

    def _build_workspace(self) -> QWidget:
        self.preview_panel = PreviewPanel()
        self.table_panel = TablePanel()
        self.log_panel = LogPanel()

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(self.preview_panel)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.table_panel, "Cluster table")
        self.tabs.addTab(self.log_panel, "Log")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(upper)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 340])
        splitter.setChildrenCollapsible(False)
        return splitter

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        bar.setObjectName("StatusBar")
        bar.setSizeGripEnabled(False)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusText")
        bar.addWidget(self.status_label, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        bar.addPermanentWidget(self.progress)

        self.state_pill = Pill("no data", "muted")
        bar.addPermanentWidget(self.state_pill)

        self.setStatusBar(bar)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open map…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._browse_for_map)
        file_menu.addAction(open_action)

        self.export_action = QAction("&Export report…", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.triggered.connect(self._export_report)
        self.export_action.setEnabled(False)
        file_menu.addAction(self.export_action)

        self.save_mask_action = QAction("Save selection as &mask…", self)
        self.save_mask_action.setShortcut("Ctrl+M")
        self.save_mask_action.triggered.connect(self._save_mask)
        self.save_mask_action.setEnabled(False)
        file_menu.addAction(self.save_mask_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        analysis_menu = self.menuBar().addMenu("&Analysis")
        self.cluster_action = QAction("Find &clusters", self)
        self.cluster_action.setShortcut("Ctrl+R")
        self.cluster_action.triggered.connect(self._run_clustering)
        self.cluster_action.setEnabled(False)
        analysis_menu.addAction(self.cluster_action)

        self.render_action = QAction("&Render surface", self)
        self.render_action.setShortcut("Ctrl+D")
        self.render_action.triggered.connect(self._render_preview)
        self.render_action.setEnabled(False)
        analysis_menu.addAction(self.render_action)

        self.wb_view_action = QAction("Open in &wb_view", self)
        self.wb_view_action.setShortcut("Ctrl+W")
        self.wb_view_action.triggered.connect(self._open_wb_view)
        self.wb_view_action.setEnabled(False)
        analysis_menu.addAction(self.wb_view_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About this configuration", self)
        about_action.triggered.connect(self._show_config)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------- wiring -- #

    def _connect(self) -> None:
        self.input_panel.load_requested.connect(self._load_map)
        self.input_panel.settings_changed.connect(self._on_input_settings_changed)

        self.cluster_panel.cluster_requested.connect(self._run_clustering)
        self.cluster_panel.cancel_requested.connect(self.runner.cancel)

        self.report_panel.report_requested.connect(self._build_report)
        self.report_panel.export_requested.connect(self._export_report)

        self.preview_panel.render_requested.connect(self._render_preview)
        self.preview_panel.save_figure_requested.connect(self._save_figure)
        self.preview_panel.screenshot_requested.connect(self._save_screenshot)

        self.table_panel.save_mask_requested.connect(self._save_mask)
        self.table_panel.open_wb_view_requested.connect(self._open_wb_view)
        self.table_panel.selection_changed.connect(self._on_selection_changed)

        self.runner.progressed.connect(self._on_progress)
        self.runner.busy_changed.connect(self._on_busy_changed)

        self.log_panel.attach(logging.INFO)

    def _populate_from_settings(self) -> None:
        settings = self.state.settings
        self.machine_chip.setText(
            f"{current_machine()} · {settings.machine.label()}"
        )

        from ..wb import probe

        info = probe(settings)
        if info["available"]:
            version = ""
            for line in info.get("version") or []:
                if "ersion" in line:
                    version = line.split(":")[-1].strip()
                    break
            self.workbench_chip.setText(f"Workbench {version or 'ready'}")
        else:
            self.workbench_chip.setText("Workbench not found")
        self.table_panel.set_wb_view_enabled(
            False,
            "Run a clustering first."
            if info["available"]
            else "wb_view was not found. Set workbench.wb_view in this machine's profile.",
        )
        self._wb_available = bool(info["available"])

        self.cluster_panel.apply_defaults(settings.defaults)

        try:
            atlases = list_atlases(settings, registry=load_registry())
        except ConfigError as exc:
            log.warning("could not list atlases: %s", exc)
            atlases = []
        self.report_panel.set_atlases(atlases, preferred=settings.defaults.atlas)

        problems = settings.validate()
        if problems:
            self._set_status(
                f"{len(problems)} configuration problem(s) — see Help ▸ About", "warn"
            )
            for problem in problems:
                log.warning("configuration: %s", problem)
        else:
            log.info(
                "machine profile %s (%s)",
                settings.machine.name or "—",
                settings.source_path or "built-in defaults",
            )

    # ----------------------------------------------------------- actions -- #

    def _browse_for_map(self) -> None:
        from .panels.input_panel import CIFTI_FILTER

        name, _ = QFileDialog.getOpenFileName(
            self, "Open a surface statistic map", "", CIFTI_FILTER
        )
        if name:
            self.input_panel.set_path(Path(name))
            self._load_map(Path(name))

    def _load_map(self, path: Path) -> None:
        settings = self.state.settings
        spec = AnalysisSpec(
            input_path=path,
            statistic=self.input_panel.statistic_kind(),
            df=self.input_panel.degrees_of_freedom(),
            column=self.input_panel.column_index(),
            mesh=settings.defaults.mesh,
            neighbor_source=self.cluster_panel.neighbor_source_value(),
        )

        def work(progress=None, cancel=None):
            from ..types import report_progress

            report_progress(progress, 0.1, "reading CIFTI")
            stat_map = load_surface_stat_map(
                path,
                statistic=spec.statistic,
                df=spec.df,
                column=spec.column,
            )
            report_progress(progress, 0.5, "loading vertex adjacency")
            adjacency = build_adjacency(stat_map, settings, spec)
            report_progress(progress, 0.8, "loading template surfaces")
            try:
                surfaces = load_hemisphere_surfaces(
                    settings, "midthickness",
                    mesh=_mesh_name(settings, self.state.stat_map),
                )
            except Exception as exc:
                log.warning("template surfaces unavailable: %s", exc)
                surfaces = None
            report_progress(progress, 1.0, "loaded")
            return stat_map, adjacency, surfaces

        self._run_job(
            work,
            description="loading map",
            busy_panels=(self.input_panel,),
            on_finished=self._on_map_loaded,
            on_failed=lambda message, detail: self._on_failure(
                "Could not read the map", message, detail, self.input_panel
            ),
        )

    def _on_map_loaded(self, result) -> None:
        stat_map, adjacency, surfaces = result
        self.state.reset_map()
        self.state.stat_map = stat_map
        self.state.adjacency = adjacency
        self.state.surfaces = surfaces
        self.state.spec.input_path = stat_map.source_path

        self.input_panel.show_facts(self.state.map_facts())
        self.cluster_panel.set_enabled(True)
        self.cluster_action.setEnabled(True)
        self.report_panel.set_enabled(False)
        self.table_panel.clear()
        self.preview_panel.clear()
        self.preview_panel.set_enabled(False)
        self.render_action.setEnabled(False)
        self.state_pill.set(stat_map.template.layout if stat_map.template else "loaded",
                            "neutral")
        self._set_status(
            f"Loaded {Path(stat_map.source_path).name} — "
            f"{stat_map.describe()['n_finite']} finite values"
        )

    def _on_input_settings_changed(self) -> None:
        if self.state.has_map:
            self.input_panel.status_pill.set("reload to apply", "warn")

    # -- clustering --------------------------------------------------------- #

    def _run_clustering(self) -> None:
        if not self.state.has_map:
            return
        stat_map = self.state.stat_map
        adjacency = self.state.adjacency
        surfaces = self.state.surfaces
        panel = self.cluster_panel

        method = panel.threshold_method()
        kwargs = dict(
            method=method,
            value=panel.threshold_value(),
            q=panel.q(),
            percentile=panel.percentile_value(),
            direction=panel.direction_value(),
            statistic=stat_map.statistic,
            df=stat_map.df,
        )
        extent = panel.extent_value()
        legacy = panel.legacy_mode()
        direction = panel.direction_value()

        def work(progress=None, cancel=None):
            from ..types import report_progress

            report_progress(progress, 0.05, "computing threshold")
            threshold = compute_threshold(stat_map.finite_values(), **kwargs)
            clusters = find_clusters(
                stat_map,
                adjacency,
                threshold,
                extent=extent,
                direction=direction,
                legacy_mode=legacy,
                progress=_scaled(progress, 0.1, 0.7),
                cancel=cancel,
            )
            report_progress(progress, 0.75, "measuring peaks")
            peaks = cluster_peaks(
                stat_map,
                clusters,
                surfaces=surfaces,
                progress=_scaled(progress, 0.75, 1.0),
                cancel=cancel,
            )
            return threshold, clusters, peaks

        self._run_job(
            work,
            description="clustering",
            busy_panels=(self.cluster_panel,),
            on_finished=self._on_clustered,
            on_failed=lambda message, detail: self._on_failure(
                "Clustering failed", message, detail, self.cluster_panel
            ),
        )

    def _on_clustered(self, result) -> None:
        threshold, clusters, peaks = result
        self.state.reset_clusters()
        self.state.threshold = threshold
        self.state.clusters = clusters
        self.state.peaks = peaks

        self.cluster_panel.show_facts(self.state.cluster_facts(), clusters.n_clusters)
        has = clusters.n_clusters > 0
        self.report_panel.set_enabled(has)
        self.preview_panel.set_enabled(has)
        self.render_action.setEnabled(has)
        self.table_panel.set_frame(peaks)
        self.table_panel.set_wb_view_enabled(
            has and self._wb_available,
            "Run a clustering first." if not has else
            ("wb_view was not found — set workbench.wb_view in this machine's profile."
             if not self._wb_available else
             "Launch Workbench with the cluster map and surfaces loaded."),
        )
        self.wb_view_action.setEnabled(has and self._wb_available)
        self.state_pill.set(f"{clusters.n_clusters} clusters",
                            "neutral" if has else "warn")

        if not has:
            self._set_status("No cluster survived the extent threshold", "warn")
            self.preview_panel.show_message(
                "No cluster survived.\nTry a lower threshold or a smaller extent."
            )
            return

        self._set_status(
            f"{clusters.n_clusters} clusters "
            f"(L {clusters.n_left} / R {clusters.n_right}) at {threshold.describe()}"
        )
        if self.report_panel.live_enabled():
            # The runner is still winding down this job, so queue the report
            # rather than starting it now; _on_busy_changed picks it up.
            self._pending_report = True

    # -- report ------------------------------------------------------------- #

    def _build_report(self) -> None:
        if not self.state.has_clusters:
            return
        if self.runner.busy:
            self._pending_report = True      # run it as soon as the runner frees up
            return
        settings = self.state.settings
        clusters = self.state.clusters
        peaks = self.state.peaks
        atlas_name = self.report_panel.atlas_name()
        top_n = self.report_panel.top_n_value()
        min_percent = self.report_panel.min_percent_value()
        style = self.report_panel.style_value()
        stat_map = self.state.stat_map

        def work(progress=None, cancel=None):
            from ..types import report_progress

            report_progress(progress, 0.1, f"loading atlas {atlas_name}")
            atlas = load_atlas(atlas_name, settings, registry=load_registry())
            for hemi in ("left", "right"):
                if atlas.hemi(hemi).n_vertices != stat_map.hemi(hemi).n_vertices:
                    raise ValueError(
                        f"atlas {atlas.name!r} {hemi} has "
                        f"{atlas.hemi(hemi).n_vertices} vertices but the map has "
                        f"{stat_map.hemi(hemi).n_vertices}; they must be on the "
                        f"same mesh"
                    )
            annotations = annotate_clusters(
                clusters,
                atlas,
                peaks=peaks,
                top_n=top_n,
                min_percent=min_percent,
                progress=_scaled(progress, 0.2, 0.85),
                cancel=cancel,
            )
            report_progress(progress, 0.9, "building table")
            report = build_report(peaks, annotations, style=style)
            return atlas, annotations, report

        self._run_job(
            work,
            description="building report",
            busy_panels=(self.report_panel,),
            on_finished=self._on_report_built,
            on_failed=lambda message, detail: self._on_failure(
                "Could not build the report", message, detail, self.report_panel
            ),
        )

    def _on_report_built(self, result) -> None:
        atlas, annotations, report = result
        self.state.atlas = atlas
        self.state.annotations = annotations
        self.state.report = report

        self.table_panel.set_frame(report)
        self.report_panel.show_done(len(report), atlas.name)
        self.report_panel.set_export_enabled(True)
        self.export_action.setEnabled(True)
        self.tabs.setCurrentWidget(self.table_panel)
        self._set_status(f"Report built against {atlas.display_name}")

    def _export_report(self) -> None:
        if not self.state.has_report:
            return
        from .panels.report_panel import REPORT_FILTER

        default = self._default_output_dir() / f"{self._stem()}_report.xlsx"
        name, _ = QFileDialog.getSaveFileName(
            self, "Export report", str(default), REPORT_FILTER
        )
        if not name:
            return
        path = Path(name)
        try:
            save_report(
                self.state.report,
                path,
                metadata=self.state.report.attrs.get("metadata"),
            )
        except Exception as exc:
            self._error("Export failed", str(exc))
            return
        self.state.last_output_dir = path.parent
        self._set_status(f"Report written to {path}")

    # -- preview ------------------------------------------------------------ #

    def _render_preview(self) -> None:
        if not self.state.has_clusters or self.runner.busy:
            return
        if self.preview_panel.active_view() == "3d":
            self._render_three_d()
        else:
            self._render_figure()

    def _render_three_d(self) -> None:
        from ..viz.interactive import build_scene

        settings = self.state.settings
        stat_map = self.state.stat_map
        clusters = self.state.clusters
        surface = self.preview_panel.surface_kind()
        mode = self.preview_panel.display_mode()
        split = self.preview_panel.split_mm()
        underlay = self.preview_panel.underlay_name()
        title = f"{self._stem()} — {clusters.n_clusters} clusters"

        def work(progress=None, cancel=None):
            from ..types import report_progress

            report_progress(progress, 0.2, "building meshes")
            scene = build_scene(
                stat_map, settings,
                clusters=clusters,
                mode=mode,
                surface=surface,
                split_mm=split,
                underlay=underlay,
                background=PALETTE.surface,
                title=title,
            )
            report_progress(progress, 1.0, "scene ready")
            return scene

        self._run_job(
            work,
            description="building the 3D scene",
            busy_panels=(self.preview_panel,),
            on_finished=self._on_scene_built,
            on_failed=self._on_render_failed,
        )

    def _render_figure(self) -> None:
        from ..viz.render import render_clusters, render_stat_map

        settings = self.state.settings
        stat_map = self.state.stat_map
        clusters = self.state.clusters
        surface = self.preview_panel.surface_kind()
        layout_name = self.preview_panel.layout_name()
        mode = self.preview_panel.display_mode()
        outlines = self.preview_panel.outlines_enabled()
        underlay = self.preview_panel.underlay_name()
        title = f"{self._stem()} — {clusters.n_clusters} clusters"

        def work(progress=None, cancel=None):
            if mode == "clusters":
                return render_clusters(
                    clusters, settings, surface=surface, layout=layout_name,
                    outline=outlines, title=title, underlay=underlay,
                    progress=progress, cancel=cancel,
                )
            return render_stat_map(
                stat_map, settings,
                clusters=clusters,
                mask_to_clusters=(mode == "masked"),
                outline_clusters=outlines,
                surface=surface,
                layout=layout_name,
                title=title,
                colorbar_label=stat_map.statistic,
                underlay=underlay,
                progress=progress,
                cancel=cancel,
            )

        self._run_job(
            work,
            description="rendering",
            busy_panels=(self.preview_panel,),
            on_finished=self._on_rendered,
            on_failed=self._on_render_failed,
        )

    def _on_scene_built(self, scene) -> None:
        # Handing the meshes to VTK has to happen on the GUI thread.
        if self.preview_panel.show_scene(scene):
            self._set_status("3D view ready — drag to rotate, wheel to zoom")
        else:
            self._set_status(
                "The 3D view is unavailable on this machine — use the Figure tab",
                "warn",
            )

    def _on_rendered(self, figure) -> None:
        self.preview_panel.show_figure(figure)
        self._set_status("Figure rendered")

    def _on_render_failed(self, message: str, detail: str) -> None:
        self.preview_panel.show_message(f"Rendering failed.\n\n{message}")
        self._set_status("Rendering failed", "danger")
        log.error("rendering failed: %s", message)

    def _save_figure(self) -> None:
        default = self._default_output_dir() / f"{self._stem()}_surface.png"
        name, _ = QFileDialog.getSaveFileName(
            self, "Save figure", str(default),
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff)",
        )
        if not name:
            return
        path = Path(name)
        try:
            self.preview_panel.figure.savefig(
                path, dpi=self.state.settings.render.dpi, bbox_inches="tight"
            )
        except Exception as exc:
            self._error("Could not save the figure", str(exc))
            return
        self.state.last_output_dir = path.parent
        self._set_status(f"Figure written to {path}")

    def _save_screenshot(self) -> None:
        default = self._default_output_dir() / f"{self._stem()}_3d.png"
        name, _ = QFileDialog.getSaveFileName(
            self, "Save 3D view", str(default), "PNG image (*.png)"
        )
        if not name:
            return
        path = Path(name)
        try:
            self.preview_panel.screenshot(path, scale=2)
        except Exception as exc:
            self._error("Could not save the screenshot", str(exc))
            return
        self.state.last_output_dir = path.parent
        self._set_status(f"3D view written to {path}")

    # -- masks and wb_view -------------------------------------------------- #

    def _on_selection_changed(self, cluster_ids) -> None:
        self.save_mask_action.setEnabled(bool(cluster_ids))

    def _save_mask(self) -> None:
        ids = self.table_panel.selected_cluster_ids()
        if not ids or not self.state.has_clusters:
            return
        stat_map = self.state.stat_map
        template = stat_map.template
        if template is None:
            from ..io.cifti import make_dense_surface_template

            template = make_dense_surface_template(*stat_map.n_vertices)

        stem = suggest_mask_name(self._stem(), ids)
        default = self._default_output_dir() / f"{stem}.dscalar.nii"
        name, _ = QFileDialog.getSaveFileName(
            self, "Save cluster mask", str(default),
            "CIFTI dense scalar (*.dscalar.nii);;All files (*)",
        )
        if not name:
            return
        path = Path(name)
        if not str(path).endswith(".dscalar.nii"):
            path = path.with_name(path.name.split(".")[0] + ".dscalar.nii")
        try:
            save_cluster_mask(
                self.state.clusters,
                ids,
                path,
                template,
                label_values=self.table_panel.label_values.isChecked(),
                map_name=stem,
            )
        except Exception as exc:
            self._error("Could not write the mask", str(exc))
            return
        self.state.last_output_dir = path.parent
        which = f"cluster {ids[0]}" if len(ids) == 1 else f"{len(ids)} clusters"
        self._set_status(f"Mask for {which} written to {path.name}")

    def _open_wb_view(self) -> None:
        if not self.state.has_clusters:
            return
        settings = self.state.settings
        try:
            path = self._ensure_cluster_map_on_disk()
        except Exception as exc:
            self._error("Could not write the cluster map", str(exc))
            return

        files: list[Path] = [path]
        if self.state.stat_map.source_path:
            files.append(Path(self.state.stat_map.source_path))
        for hemi in ("left", "right"):
            for kind in (self.preview_panel.surface_kind(), "midthickness"):
                try:
                    surface = settings.surface_for(
                        hemi, kind,
                        mesh=settings.mesh_of(
                            self.state.stat_map.left.n_vertices
                        ).name if self.state.stat_map else None,
                    )
                except Exception:
                    continue
                if surface.exists() and surface not in files:
                    files.append(surface)
                    break

        from ..wb import open_in_wb_view

        try:
            open_in_wb_view(files, settings)
        except Exception as exc:
            self._error(
                "Could not launch wb_view",
                f"{exc}\n\nSet workbench.wb_view in this machine's profile "
                f"({settings.source_path}).",
            )
            return
        self._set_status(f"wb_view launched with {len(files)} files")

    def _ensure_cluster_map_on_disk(self) -> Path:
        """Write the cluster label map so Workbench has something to open."""
        if self.state.cluster_map_path and self.state.cluster_map_path.exists():
            return self.state.cluster_map_path

        stat_map = self.state.stat_map
        template = stat_map.template
        if template is None:
            from ..io.cifti import make_dense_surface_template

            template = make_dense_surface_template(*stat_map.n_vertices)

        directory = self.state.settings.temp_dir()
        threshold = self.state.threshold
        value = threshold.positive if threshold.positive is not None else 0.0
        stem = (
            f"{self._stem()}_cluster_extent{self.state.clusters.params.extent}"
            f"_thr{value:.6g}"
        )
        path = save_like(
            directory / f"{stem}.dscalar.nii",
            self.state.clusters.labels_left.astype(float),
            self.state.clusters.labels_right.astype(float),
            template,
            map_name=stem,
        )
        self.state.cluster_map_path = path
        return path

    # ------------------------------------------------------------- plumbing -- #

    def _run_job(
        self,
        work,
        *,
        description: str,
        busy_panels: tuple = (),
        on_finished=None,
        on_failed=None,
    ) -> None:
        if self.runner.busy:
            self._set_status("Something is already running", "warn")
            return

        job = Job(work, description=description)
        self._busy_panels = busy_panels
        for panel in busy_panels:
            panel.set_busy(True)

        def finished(result):
            for panel in busy_panels:
                panel.set_busy(False)
            if on_finished:
                on_finished(result)

        def failed(message, detail):
            for panel in busy_panels:
                panel.set_busy(False)
            if on_failed:
                on_failed(message, detail)

        def cancelled():
            for panel in busy_panels:
                panel.set_busy(False)
            self._set_status(f"{description.capitalize()} cancelled", "warn")

        self._set_status(f"{description.capitalize()}…")
        self.runner.start(
            job, on_finished=finished, on_failed=failed, on_cancelled=cancelled
        )

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(fraction * 100))
        self.status_label.setText(message)

    def _on_busy_changed(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        if busy:
            return
        self.progress.setValue(0)
        if self._pending_report:
            self._pending_report = False
            QTimer.singleShot(0, self._build_report)

    def _on_failure(self, title: str, message: str, detail: str, panel) -> None:
        if hasattr(panel, "show_error"):
            panel.show_error(message)
        self._set_status(f"{title}: {message}", "danger")
        self._error(title, message, detail)

    def _error(self, title: str, message: str, detail: str = "") -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _set_status(self, message: str, tone: str = "") -> None:
        self.status_label.setText(message)
        colour = {
            "warn": PALETTE.warn, "danger": PALETTE.danger, "": PALETTE.muted,
        }.get(tone, PALETTE.muted)
        self.status_label.setStyleSheet(f"color: {colour}; font-size: 11px;")

    def _show_config(self) -> None:
        settings = self.state.settings
        lines = [
            f"machine        {current_machine()}",
            f"profile        {settings.machine.label()}",
            f"configuration  {settings.source_path or '(built-in defaults)'}",
        ]
        lines += [f"  extends      {p}" for p in settings.inherited_from]
        lines += [
            "",
            f"resources.root {settings.resources.root}",
            f"atlas_dir      {settings.resources.atlas_dir}",
            f"atlas_csv_dir  {settings.resources.atlas_csv_dir}",
            f"wb_command     {settings.workbench.resolve().wb_command or 'not found'}",
            f"wb_view        {settings.workbench.resolve().wb_view or 'not found'}",
            f"tmp_dir        {settings.runtime.tmp_dir or '(system temp)'}",
        ]
        problems = settings.validate()
        if problems:
            lines += ["", "problems:"] + [f"  - {p}" for p in problems]

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Configuration")
        box.setText("\n".join(lines[:3]))
        box.setDetailedText("\n".join(lines))
        box.exec()

    def _default_output_dir(self) -> Path:
        if self.state.last_output_dir:
            return self.state.last_output_dir
        if self.state.stat_map and self.state.stat_map.source_path:
            return Path(self.state.stat_map.source_path).parent
        return Path.home()

    def _stem(self) -> str:
        if self.state.stat_map and self.state.stat_map.source_path:
            return Path(self.state.stat_map.source_path).name.split(".")[0]
        return "analysis"

    # -- lifecycle ---------------------------------------------------------- #

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.runner.busy:
            answer = QMessageBox.question(
                self,
                "Still working",
                "An analysis is still running. Cancel it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.runner.wait()
        self.log_panel.detach()
        # VTK holds a native render window; let it go before Qt tears down.
        self.preview_panel.close_plotter()
        super().closeEvent(event)


def _mesh_name(settings, stat_map):
    """Which mesh the loaded map is on, or ``None`` to use the configured one."""
    if stat_map is None:
        return None
    try:
        return settings.mesh_of(stat_map.left.n_vertices).name
    except Exception:
        return None


def _scaled(progress, start: float, end: float):
    """Map a child's 0..1 progress onto a slice of the parent's range."""
    if progress is None:
        return None

    def inner(fraction: float, message: str) -> None:
        progress(start + (end - start) * fraction, message)

    return inner
