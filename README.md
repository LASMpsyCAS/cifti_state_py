<div align="center">

# cifti_state

**Cluster analysis, anatomical reporting and visualisation for fs_LR 32k cortical surfaces.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-112%20passing-brightgreen.svg)](#tests)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#install)

[English](README.md) · [中文](README.zh-CN.md)

</div>

![The cifti_state interface](docs/images/gui.png)

---

## What it does

You have a statistic map on the fs_LR 32k surface — a z or t map from any
package. This finds the connected clusters that survive a threshold, tells you
which anatomical regions they fall in, and draws them.

```
map.dscalar.nii  ──▶  threshold  ──▶  clusters  ──▶  peaks + atlas regions  ──▶  table
     91k/59k/64k       fixed/FDR/       connected      Glasser, AAL, Yeo,          csv/xlsx/md
                       percentile       components     Schaefer, Desikan …         + figures
```

| | |
|---|---|
| **Reads any fs_LR CIFTI layout** | 91k, 59k and 64k all describe the same left+right 32k cortex. The vertex mapping comes from each file's own brain models, so nothing is assumed about greyordinate counts. GIFTI metric pairs too. |
| **Reproduces the MATLAB result exactly** | `legacy_mode` gives vertex-for-vertex identical cluster maps, including the numbering. Verified in CI-style tests against reference outputs — see [MATLAB parity](#matlab-parity). |
| **…and fixes what was wrong with it** | Negative clusters can actually be found, `-inf` no longer manufactures clusters, AAL's right hemisphere gets right-hemisphere labels. [Full list](#what-changed-from-the-matlab-version). |
| **Region names from the atlas itself** | Each `label.gii`'s own LabelTable is the source of truth, so every atlas in the template pack works — not only the ones with a CSV. |
| **Three ways in, one core** | `cifti-state` on the command line, `import cifti_state` in a notebook, or `cifti-state-gui`. All three call the same functions. |
| **Publication figures and a live 3D view** | surfplot for the figure you export, PyVista for the one you rotate — both over the same arrays, both with the sulcal underlay. |
| **Runs the example out of the box** | `example_data/` ships the maps *and* the minimum templates. Clone, install, run. |

## Install

<details open>
<summary><b>Windows</b></summary>

```bat
conda create -n cifti-state python=3.11 pip -y
conda activate cifti-state

git clone https://github.com/<your-github>/cifti_state_py.git
cd cifti_state_py

pip install -r requirements.txt
pip install -e .
```

`scripts\install_windows.cmd` does the same thing in one step.
[`docs/INSTALL.md`](docs/INSTALL.md) is a ten-step walkthrough with the expected
output of every command, including a mainland-China pip mirror.

</details>

<details>
<summary><b>Linux</b></summary>

```bash
conda create -n cifti-state python=3.11 pip -y
conda activate cifti-state

git clone https://github.com/<your-github>/cifti_state_py.git
cd cifti_state_py

pip install -r requirements.txt
pip install -e .
```

Headless? VTK needs an offscreen context — run commands under `xvfb-run -a`, or
install a VTK built with OSMesa/EGL.

</details>

> **Why conda for the environment but pip for the packages?** Solving a full
> scientific stack in conda takes minutes; pip resolves the same set in seconds.
> conda's only job here is an isolated interpreter.

Two of the wheels are large (`vtk` and `PySide6`, ~100 MB each). If you only
need the analysis, `pip install -e .` alone pulls just numpy/scipy/pandas/
nibabel/matplotlib/PyYAML, and the extras are opt-in:

```bash
pip install -e ".[viz]"        # surfplot figures
pip install -e ".[gui]"        # the desktop interface and the 3D view
pip install -e ".[all]"        # everything, including the test tools
```

**Connectome Workbench** is optional. Clustering, reporting and both renderers
work without it; it is needed only for `wb_command` operations and for opening
results in `wb_view`.

## Quickstart

The repository ships everything the example needs, so this works immediately
after installing — no downloads, no paths to fix.

```bash
export CIFTI_STATE_CONFIG=$PWD/example_data/example.yaml       # bash
# $env:CIFTI_STATE_CONFIG = "$PWD\example_data\example.yaml"   # PowerShell

cifti-state check                                              # is everything in place?

cifti-state run example_data/maps/group_mean_thresh_fdr_E_C.dscalar.nii \
    --method fixed --threshold 1.039 --extent 20 --legacy \
    --atlas Glasser_2016 --render -o results/E_C
```

```
loaded group_mean_thresh_fdr_E_C.dscalar.nii [91k greyordinates
      (32k cortex without medial wall + subcortex)]: L 29696/32492, R 29716/32492
found 32 clusters (left 15, right 17) at fixed(+1.039), extent >= 20
loaded atlas Glasser_2016 (360 regions across both hemispheres)

group_mean_thresh_fdr_E_C.dscalar: 32 clusters (L 15 / R 17)
  cluster_map      ..._cluster_extent20_thr1.039.dscalar.nii
  report_csv       ..._cluster_extent20_thr1.039_report.csv
  peaks_csv        ..._cluster_extent20_thr1.039_peaks.csv
  annotations_csv  ..._cluster_extent20_thr1.039_regions.csv
  figure           ..._cluster_extent20_thr1.039.png
  record           ..._cluster_extent20_thr1.039_analysis.json
```

Those 32 clusters are the answer the MATLAB pipeline gave, and
`example_data/maps/` contains its output so you can check:

```python
import numpy as np, nibabel as nib
name = "group_mean_thresh_fdr_E_C_cluster_extent20_thr1.039.dscalar.nii"
mine      = nib.load(f"results/E_C/{name}").get_fdata().ravel()
reference = nib.load(f"example_data/maps/{name}").get_fdata().ravel()
np.array_equal(mine, reference)      # True — 0 differing vertices
```

The report:

| cluster_id | hemi | peak_region | primary_region | % | regions | size | mm² | peak | x | y | z |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | L | V1_ROI | V1_ROI | 70.3 | V1_ROI (70.3%); ProS_ROI (29.7%) | 37 | 92.0 | 1.089 | −19 | −56 | 0 |
| 2 | L | a32pr_ROI | p32pr_ROI | 41.7 | p32pr_ROI (41.7%); a32pr_ROI (41.7%) | 36 | 64.0 | 1.092 | −6 | 23 | 39 |
| 3 | L | 23c_ROI | PCV_ROI | 64.3 | PCV_ROI (64.3%); 23c_ROI (35.7%) | 28 | 43.0 | 1.108 | −7 | −45 | 51 |

Then try the other two entry points:

```bash
python examples/worked_example.py        # ten steps, every claim in this README demonstrated
cifti-state-gui                          # the interface
```

## Use cases

<details open>
<summary><b>1 · Reproduce a MATLAB analysis exactly</b></summary>

`--legacy` pins every behavioural difference back to the original: positive
direction only, the original infinity handling, the original FDR tail.

```bash
cifti-state run map.dscalar.nii --method fixed --threshold 1.09 --extent 20 --legacy
```

The output file is named the way the MATLAB script named it
(`<input>_cluster_extent<N>_thr<T>.dscalar.nii`), so it drops straight into an
existing folder structure.

</details>

<details>
<summary><b>2 · FDR threshold, both tails, a different atlas, Excel out</b></summary>

```bash
cifti-state run map.dscalar.nii \
    --method fdr --q 0.05 --direction two_sided \
    --extent 20 --atlas Desikan \
    --format xlsx --format csv --report-style long \
    --render --render-layout grid_8 --render-format pdf \
    -o results/fdr_two_sided
```

`--direction two_sided` finds negative clusters as well, numbered continuously
with the positive ones. `--report-style long` gives one row per
cluster × region instead of packing the regions into a single cell.

</details>

<details>
<summary><b>3 · A t map instead of a z map</b></summary>

The statistic type must be declared, because the p-value conversion depends on
it. A t map read as z gives overstated p-values (this was a real bug in the
MATLAB version).

```bash
cifti-state run tmap.dscalar.nii --statistic t --df 29 --method fdr --q 0.05
```

</details>

<details>
<summary><b>4 · Many maps, one parameter set</b></summary>

```bash
cifti-state batch "derivatives/*/stats/*_zstat.dscalar.nii" \
    --method fdr --q 0.05 --extent 20 --atlas Glasser_2016 \
    --format csv -o results/batch
```

Each map gets its own output folder; one map failing does not stop the rest.

</details>

<details>
<summary><b>5 · Record a run so it can be repeated</b></summary>

Every analysis writes `*_analysis.json` carrying the complete parameter set,
and an analysis can be driven from a spec file:

```yaml
# analysis.yaml
input_path: example_data/maps/group_mean_thresh_fdr_E_D.dscalar.nii
statistic: z
threshold_method: fixed
threshold_value: 1.09
direction: positive
extent: 20
legacy_mode: true
atlas: Glasser_2016
top_n_regions: 2
output_dir: results/E_D
report_formats: [csv, xlsx]
render: true
```

```bash
cifti-state run --spec analysis.yaml
```

See `examples/spec_example.yaml` for every field.

</details>

<details>
<summary><b>6 · The desktop interface</b></summary>

```bash
cifti-state-gui                                  # or: cifti-state-gui map.dscalar.nii
```

Three steps down the left: **Data** (pick the file, declare z or t) →
**Cluster** (threshold, direction, extent, legacy) → **Anatomical report**
(atlas, regions per cluster, minimum share, table style). The workspace on the
right has the preview above and the cluster table + log below.

- Report parameters re-run **only** the annotation step. Tick *Update as I
  change these* and the table follows the controls live — no re-clustering.
- Select rows in the table → **Save selection as mask…** writes the chosen
  clusters as a binary `dscalar.nii` **in the same greyordinate layout as the
  input**, ready to feed back into anything. Tick *keep cluster ids* to write
  each cluster's own number instead of 1.
- **Open in wb_view** writes the cluster map to a temp folder and launches
  Workbench with the statistic map and the template surfaces.
- The preview has two tabs: a rotatable **3D view** and the **Figure** that
  gets exported — the figure is built by the very same function the CLI uses.
- Everything slow runs on a worker thread with a progress bar and Cancel.

Shortcuts: `Ctrl+O` open · `Ctrl+R` cluster · `Ctrl+D` render · `Ctrl+E` export
report · `Ctrl+M` save mask · `Ctrl+W` wb_view.

</details>

<details>
<summary><b>7 · Python API — the whole pipeline in eight lines</b></summary>

```python
from cifti_state import load_settings, run_analysis, AnalysisSpec

settings = load_settings("example_data/example.yaml")
spec = AnalysisSpec(
    input_path="example_data/maps/group_mean_thresh_fdr_E_C.dscalar.nii",
    threshold_method="fixed", threshold_value=1.039,
    extent=20, legacy_mode=True, atlas="Glasser_2016",
    output_dir="results/E_C", report_formats=("csv", "xlsx"), render=True,
)
result = run_analysis(spec, settings)

print(result.summary())          # "32 clusters (L 15 / R 17) at fixed(+1.039) …"
result.report                    # a pandas DataFrame
result.outputs                   # {"cluster_map": Path(...), "report_csv": Path(...), …}
result.to_json("run.json")       # enough to reproduce this exact run
```

</details>

<details>
<summary><b>8 · Python API — step by step, for when you need the pieces</b></summary>

```python
from cifti_state import load_settings
from cifti_state.io import load_surface_stat_map, save_like, load_hemisphere_surfaces
from cifti_state.core import (compute_threshold, find_clusters, cluster_peaks,
                              annotate_clusters, build_report, clusters_mask)
from cifti_state.io.atlas import load_atlas
from cifti_state.pipeline import build_adjacency
from cifti_state.results import AnalysisSpec

settings = load_settings("example_data/example.yaml")

# 1 · read — the layout is detected, not assumed
stat_map = load_surface_stat_map(
    "example_data/maps/group_mean_thresh_fdr_E_D.dscalar.nii", statistic="z"
)
stat_map.describe()
# {'layout': '91k greyordinates (32k cortex without medial wall + subcortex)',
#  'n_vertices_left': 32492, 'n_present_left': 29696, 'medial_wall_excluded': True, …}
stat_map.left.values        # (32492,) on the FULL mesh, NaN where absent
stat_map.left.present       # (32492,) bool — which vertices the file carried

# 2 · threshold
values = stat_map.finite_values()
thr = compute_threshold(values, method="fixed", value=1.09, direction="positive")
# or  method="fdr", q=0.05, statistic="z", df=None
# or  method="percentile", percentile=95
thr.describe(), thr.positive, thr.n_suprathreshold
# ('fixed(+1.09)', 1.09, 11634)

# 3 · cluster
adjacency = build_adjacency(stat_map, settings, AnalysisSpec(mesh="32k"))
clusters = find_clusters(stat_map, adjacency, thr, extent=20, legacy_mode=True)
clusters.n_clusters, clusters.n_left, clusters.n_right     # (76, 38, 38)
clusters.labels_left        # (32492,) int, 0 = no cluster
clusters.sizes()            # {1: 114, 2: 408, 3: 707, …}
clusters.info(3)            # ClusterInfo(cluster_id=3, hemisphere='left', …)

# 4 · peaks and anatomy
surfaces = load_hemisphere_surfaces(settings, "midthickness")
peaks   = cluster_peaks(stat_map, clusters, surfaces=surfaces)         # (76, 17)
atlas   = load_atlas("Glasser_2016", settings)
regions = annotate_clusters(clusters, atlas, peaks=peaks,
                            top_n=2, min_percent=5.0)                  # (147, 10)
report  = build_report(peaks, regions, style="wide")                   # (76, 21)

# 5 · write selected clusters as a mask, in the input's own layout
left, right = clusters_mask(clusters, [3, 7, 12])      # label_values=True keeps the ids
save_like("clusters_3_7_12.dscalar.nii", left, right, stat_map.template,
          map_name="clusters 3, 7, 12")
```

Every step takes optional `progress=` and `cancel=` callbacks, which is how the
interface drives them from a worker thread.

</details>

<details>
<summary><b>9 · Python API — figures and the 3D view</b></summary>

```python
from cifti_state.viz.render import render_stat_map, render_clusters, save_figure

fig = render_stat_map(
    stat_map, settings,
    clusters=clusters,
    mask_to_clusters=True,        # statistic only inside surviving clusters
    outline_clusters=True,        # draw cluster borders on top
    surface="inflated",
    layout="grid_4",              # grid_4 row_4 grid_8 dorsal_ventral
                                  # left_only right_only flat
    underlay="sulc",              # the greyscale folding pattern
    colorbar_label="z",
)
save_figure(fig, "clusters.pdf", settings=settings, dpi=300)
```

`render_stat_map` returns a plain matplotlib `Figure` — save it, or embed the
same object in a Qt window with `FigureCanvasQTAgg`. What the interface shows
and what you export come from one code path.

![A grid_4 figure with the sulcal underlay](docs/images/figure_grid4.png)

For something you can rotate:

```python
import pyvista as pv
from cifti_state.viz.interactive import build_scene

scene = build_scene(
    stat_map, settings, clusters=clusters,
    mode="masked",                # masked | full | clusters
    surface="inflated", split_mm=12, view="dorsal", underlay="sulc",
)
plotter = pv.Plotter()
scene.add_to(plotter)
plotter.show()
```

`build_scene` returns toolkit-agnostic `pyvista.PolyData` meshes plus a
description of how to colour them, so the same scene goes to a
`pyvista.Plotter` in a script or a `pyvistaqt.QtInteractor` in the interface.

![The interactive 3D view](docs/images/view_3d.png)

</details>

<details>
<summary><b>10 · Calling Connectome Workbench</b></summary>

```python
from cifti_state.wb import probe, smooth_cifti, cifti_separate, open_in_wb_view

probe(settings)
# {'platform': 'windows', 'wb_command': 'D:/…/wb_command.exe',
#  'version': '1.5.0', 'available': True}

smooth_cifti("map.dscalar.nii", "smooth.dscalar.nii", settings,
             surface_kernel=4.0, volume_kernel=4.0)

cifti_separate("map.dscalar.nii", settings,
               left_metric="L.func.gii", right_metric="R.func.gii")

open_in_wb_view(["map.dscalar.nii", "clusters.dscalar.nii"], settings)
```

Arguments are passed as a list — never through a shell — and every call is
logged in full, so a failure shows you the exact command that failed.

</details>

## Configure — one file per machine

Paths differ per machine and nothing else does, so that is the only thing a
machine file contains.

```
configs/
  settings.windows.yaml          platform template: file-naming conventions + analysis defaults
  settings.linux.yaml            (no machine paths in either)
  machines/
    <hostname>.yaml              ← yours, created by the command below
    example-linux-server.yaml    ← a server template to copy
example_data/example.yaml        ← a complete self-contained config for the bundled data
```

```bash
cifti-state config --init-machine     # writes configs/machines/<hostname>.yaml
cifti-state config --show-chain       # which file wins, and why
cifti-state check --workbench --render
```

```yaml
# configs/machines/my-laptop.yaml
extends: ../settings.windows.yaml

workbench:
  wb_command: D:/workbench/bin_windows64/wb_command.exe
  wb_view:    D:/workbench/bin_windows64/wb_view.exe

resources:
  root:      D:/data/fs_LR_32-master      # the template pack
  atlas_dir: D:/data/fs_LR_32-master
  neighbors:
    "32k":
      left:  D:/data/lh.neighbors_IndexStart0.txt
      right: D:/data/rh.neighbors_IndexStart0.txt
```

**Resolution order**, first hit wins: `--config` → `$CIFTI_STATE_CONFIG` →
`configs/machines/<hostname>.yaml` → user config → `configs/settings.<platform>.yaml`.

Worth knowing:

- A **blank value inherits** rather than clears, so the scaffolded file is safe
  to leave half-empty and you can delete lines you do not need to change.
- Relative paths resolve against **the file that wrote them**, so a parent and
  child template never fight over a working directory.
- Windows drive paths (`D:/…`) survive being read on Linux — checking a config
  cross-platform does not silently rewrite them.
- On a cluster where hostnames differ but paths do not, set
  `CIFTI_STATE_MACHINE=shared` and name the file `shared.yaml`.

## Input formats

| greyordinates | what it is | medial wall |
|---|---|---|
| **91282** | cortex 29696 + 29716, plus 19 subcortical structures | excluded |
| **59412** | cortex only, the same vertices | excluded |
| **64984** | dense surface, 32492 + 32492 | included |

All three describe the same left + right 32k cortex and are handled
transparently: the vertex mapping comes from each file's own brain models.
Everything downstream sees a full-mesh array plus a `present` mask, so vertex
indices line up with the surfaces and the atlases without bookkeeping — and
output is written back in the input's own layout.

Other mesh densities (10k, 59k, 164k) work the same way given matching atlases
and neighbour tables. A pair of `*.func.gii` metric files can be read with
`load_surface_stat_map_from_gifti`.

## Atlases

```bash
cifti-state atlases          # what is available in your template pack
cifti-state atlases --all    # including ones not in the registry
```

Region names come from each `label.gii`'s own LabelTable, so **any** atlas
following the `<name>.32k.{L,R}.label.gii` convention works — Glasser 2016,
AAL, Yeo 7/17, Schaefer, Gordon, Power, Desikan, Destrieux, Fan, Shen,
Baldassano, Wang, the Icosahedrons, and the rest of the pack. A CSV, where one
is configured, is applied on top as a display-name override.

## The sulcal underlay

Both renderers draw the folding pattern in greyscale under the statistic, so a
blob sits on a gyral crown or in a sulcal fundus instead of floating on a
featureless shell.

```yaml
render:
  underlay: sulc            # sulc | curvature | none
  underlay_style: binary    # binary (the Workbench look) | continuous
  underlay_orient: auto     # auto | negative | positive
  underlay_dark: 0.38
  underlay_light: 0.72
```

Which sign of an underlay is a *sulcus* is not a constant — FreeSurfer's
`?h.sulc` is positive in sulci, the fs_LR file is negative there — and getting
it backwards inverts the whole picture without looking obviously wrong. So it
is measured rather than assumed: concavity is computed from the surface mesh
and the greyscale is oriented to match. `underlay_orient` pins it if the
automatic answer is ever wrong.

## Fonts and encoding

Fonts are resolved in Python, not left to the desktop: an explicit ordered list
is searched, and Qt and matplotlib are given the same answer so the window and
the figures cannot disagree. `axes.unicode_minus` is off (matplotlib's U+2212
is missing from many CJK fonts and renders as a box — the usual reason negative
numbers look wrong), and stdout is forced to UTF-8 so a Chinese path cannot
raise `UnicodeEncodeError` on the GBK Windows console.

Sizes are in **pixels**, in one place, and the stylesheet uses the same number,
so the desktop's DPI setting cannot scale text without scaling its box. Number
fields also get the fixed-width family and the **C locale**, so a regional
setting cannot substitute native digits or a decimal comma into a threshold.

**If numbers render as the wrong characters** — `0.1160` as `O.ıı ϗ OO` — the
string is correct and the font is not: some fonts (variable fonts under Qt's
DirectWrite backend, and broken subsets) return glyphs that do not belong to
the characters asked for. Such families are now skipped automatically, and:

```bash
cifti-state check --fonts                      # what was resolved
cifti-state check --fonts --sample spec.png    # the same numbers in every candidate family
```

To settle it for good, name a family in the machine profile:

```yaml
interface:
  ui_font: Microsoft YaHei UI    # covers Latin and Chinese
  mono_font: Consolas
  font_size_px: 13
  numeric_font: mono             # mono | ui
```

Dropping a `.ttf`/`.otf` into `resources/fonts/` makes the result fully
machine-independent — bundled fonts are registered with both toolkits and take
priority over anything installed.

## If the 3D view is blank

```bash
python examples/check_3d.py              # or --offscreen to save PNGs instead
```

It tests the stack bottom-up — imports, OpenGL context, a plain PyVista window,
your own surfaces, then the Qt-embedded widget the interface uses — and stops
at the first failure, so you learn *which* layer is at fault. The usual answer
is that VTK cannot get an OpenGL context: Remote Desktop generally cannot
provide one, and an old graphics driver may not either. The Figure tab does not
need one.

## MATLAB parity

`tests/test_regression.py` asserts vertex-for-vertex equality with the
reference cluster maps in `example_data/maps/`:

| map | threshold | extent | MATLAB | cifti_state | differing vertices |
|---|---|---|---|---|---|
| `group_mean_thresh_fdr_E_C` | 1.039 | 20 | 32 (L 15 / R 17) | 32 (L 15 / R 17) | **0** |
| `group_mean_thresh_fdr_E_D` | 1.09 | 20 | 76 (L 38 / R 38) | 76 (L 38 / R 38) | **0** |

Including the cluster numbering, which is deterministic by
(hemisphere, sign, lowest vertex index).

### What changed from the MATLAB version

Corrections, not refactors. The first four change results:

1. **Negative clusters can now be found.** `get_clusters_fsLR32k.m` masked with
   `map .* (map > threshold)` and then searched with `threshold = 0`, so
   sub-threshold vertices became exactly zero and no negative cluster could
   ever survive. `direction` selects `positive` / `negative` / `two_sided`.
2. **Symmetric infinity handling.** `map(isinf(map)) = max(...)` turned `-inf`
   into a large *positive* value, manufacturing clusters. `inf_policy="clip"`
   sends `+inf` to the largest finite value and `-inf` to the smallest.
3. **AAL right-hemisphere labels were wrong.** `w_find_brain_region.m` loaded
   `data_aal_L.label.gii` for both hemispheres.
4. **State leaked between clusters.** `label_index_name` was never cleared, so
   a cluster matching only one region inherited the previous cluster's second.
5. **Region names come from each `label.gii`'s own LabelTable**, so all atlases
   in the template pack work rather than only the ones with a CSV — and the
   hard-wired `+1` / `+36` / `+max(L)` hemisphere offsets are gone.
6. **`zstat` was the cluster mean, not the peak.** Both are reported under
   honest names, plus SD, min/max, area in mm², and peak/centroid coordinates.
7. **The statistic type must be declared.** `1 - normcdf` treats its input as a
   z-score; `surfstate.m` fed it `slm.t`. A t map now requires `df`.
8. **Linear-time clustering.** The original queued neighbours without checking
   whether they were already queued; components now come from
   `scipy.sparse.csgraph`.
9. **Single-region clusters are no longer padded** by duplicating their row.

For a positive-only analysis of finite data, old and new agree exactly. Where
the data contain negative values or infinities, or where AAL was used, they
differ — that is the bug being fixed, not a regression.

`lh/rh.neighbors_IndexStart0.txt` remain the default adjacency source. They were
checked against the mesh topology and agree vertex for vertex (32 480 vertices
with 6 neighbours, 12 with 5), so `neighbor_source: surface` is an exact
drop-in that works at any mesh density — and is what the bundled example uses,
so no 2.6 MB table has to be shipped.

## Project layout

```
cifti_state/
  config.py     Settings dataclasses, platform detection, path resolution
  wb.py         wb_command / wb_view wrapper, every call logged in full
  io/           cifti · surface · neighbors · atlas
  core/         threshold · cluster · peaks · annotate · report · mask
  fonts.py      font resolution and UTF-8, shared by Qt and matplotlib
  viz/          render (surfplot) · interactive (PyVista) · underlay (sulci)
                · colormaps · layouts
  gui/          theme · widgets · state · workers · panels/ · main_window
  pipeline.py   the orchestration the CLI and the GUI share
  results.py    AnalysisSpec / AnalysisResult, both serialisable
  cli.py
```

`core/` is written to six rules so a Qt front end can call it directly: pure
functions over arrays, no printing (only `logging`), a `progress` callback on
anything slow, a `cancel` token on anything long, configuration passed in
rather than read from globals, and every result carrying the parameter snapshot
that produced it — `AnalysisResult.to_json()` is enough to reproduce a run.

## Tests

```bash
pytest          # 112 tests; on a bare clone 3 skip (they need the neighbour
                # tables and the Desikan atlas, which are not bundled)
```

No setup: the data-backed tests, **including the MATLAB regression**, run
against `example_data/` by default. To point them at your own copy instead:

```bash
export CIFTI_STATE_TEST_CONFIG=configs/machines/<hostname>.yaml
export CIFTI_STATE_TEST_EXAMPLES=/path/to/Example_test
pytest
```

What is covered: clustering on synthetic graphs where the answer is known by
hand; BH-FDR against the textbook definition; the three CIFTI layouts and their
round trips; the `extends`/machine-profile resolution including blank-inherits
and Windows drive paths read on Linux; the `wb_command` wrapper against a stub
executable; annotation and report assembly; cluster masks; the underlay's
greyscale and its sign detection; font resolution and the numeric-field
defences; the interface run headless — including the assertion that worker
callbacks land on the GUI thread, which every widget update depends on; and the
MATLAB regression above.

## Not yet built

- **RFT cluster correction.** The formula is not the obstacle — it was
  validated by simulation and hits an exact 0.05 FWE rate when the field's
  smoothness is known. The obstacle is that SurfStat takes smoothness from the
  GLM residuals; estimating it from the statistic map instead is biased low
  (6–17% in simulation), pushing the FWE rate to 0.06–0.18. It would need
  either the residuals or a user-supplied FWHM.
- Generating `.spec` / `.scene` files for wb_view; it is currently launched
  with a plain file list.
- Sub-peaks (`find_local_peaks`) are implemented but not surfaced in the
  interface or the pipeline.
- Batch mode has no interface; use `cifti-state batch`.

## Example data

`example_data/` holds the two statistic maps, the MATLAB reference outputs, and
the minimum fs_LR 32k template files needed to run everything above — about
8 MB in total. See [`example_data/README.md`](example_data/README.md) for what
each file is and where the third-party templates come from.

## Acknowledgements

The fs_LR 32k templates bundled in `example_data/fs_LR_32k/` come from
[DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32). The
surfaces derive from the HCP group average (Van Essen et al., *Cerebral Cortex*
2012) and the parcellation is Glasser et al., *Nature* 2016 — please cite those
works when you use them. Figures are drawn with
[surfplot](https://github.com/danjgale/surfplot) and
[BrainSpace](https://github.com/MICA-MNI/BrainSpace); the interactive view uses
[PyVista](https://pyvista.org/). CIFTI and GIFTI reading is
[NiBabel](https://nipy.org/nibabel/).

## License

MIT — see [LICENSE](LICENSE). The files in `example_data/fs_LR_32k/` are third
party and carry their own terms; see
[`example_data/README.md`](example_data/README.md).
