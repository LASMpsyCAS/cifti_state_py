<div align="center">

# cifti_state

**Cluster analysis, anatomical reporting and visualisation for fs_LR 32k cortical surfaces.**

Take a statistic map on the cortical surface, find the clusters that survive a threshold, name the regions they fall in, and draw them — from the command line, from Python, or from a desktop interface, all over one shared core.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-207%20passing-brightgreen.svg)](#tests)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#install)

[English](README.md) · [中文](README.zh-CN.md)

</div>

![The cifti_state interface](docs/images/gui.png)

---

## What it does

You have a statistic map on the fs_LR 32k surface — a z or t map from any
package. This turns it into the table and the figure you actually need.

```
map.dscalar.nii  ──▶  threshold  ──▶  clusters  ──▶  peaks + atlas regions  ──▶  table
     91k/59k/64k       fixed/FDR/       connected      Glasser, AAL, Yeo,          csv/xlsx/md
                       percentile       components     Schaefer, Desikan …         + figures
```

| | |
|---|---|
| **Reads any fs_LR CIFTI layout** | 91k, 59k and 64k all describe the same left+right 32k cortex. The vertex mapping comes from each file's own brain models, so nothing is assumed about greyordinate counts — and results are written back in the layout you gave. GIFTI metric pairs too. |
| **More than one resolution** | fs_LR 10k and fsaverage5 run the whole pipeline at their own density — adjacency, areas, atlas, figure — and `cifti-state resample` moves data between meshes through `wb_command` with the right spheres, the area metrics, and the medial wall held out of the interpolation. |
| **Clustering that says what it did** | Positive, negative or two-sided; a minimum extent in vertices; fixed, FDR or percentile thresholds; deterministic cluster numbering. Every result carries the parameter snapshot that produced it. |
| **Region names from the atlas itself** | Each `label.gii`'s own LabelTable is the source of truth, keyed by (hemisphere, label id) — so every atlas in the template pack works, not only the ones with a lookup table shipped alongside. |
| **Honest peak tables** | Peak *and* mean value under their own names, plus SD, min/max, surface area in mm², peak and centroid coordinates, and the share of each cluster falling in each region. |
| **Group statistics, corrected** | One-sample, two-sample and paired t-tests across subjects, with covariates — then family-wise error correction over the whole surface by random field theory, by permutation, and by TFCE, so the three can be compared. The TFCE agrees with PALM to machine precision. |
| **Three ways in, one core** | `cifti-state` on the command line, `import cifti_state` in a notebook, or `cifti-state-gui`. All three call the same functions, so anything the interface can do is scriptable. |
| **Publication figures and a live 3D view** | surfplot for the figure you export, PyVista for the one you rotate — both over the same arrays, both with the sulcal underlay. |
| **Runs the example out of the box** | `example_data/` ships the maps *and* the minimum templates. Clone, install, run. |

## Install

<details open>
<summary><b>Windows</b></summary>

```bat
conda create -n cifti-state python=3.11 pip -y
conda activate cifti-state

git clone https://github.com/LASMpsyCAS/cifti_state_py.git
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

git clone https://github.com/LASMpsyCAS/cifti_state_py.git
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
    --method fixed --threshold 1.039 --extent 20 \
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

The expected cluster map for that command ships alongside the input, so you can
confirm your installation reproduces it exactly:

```python
import numpy as np, nibabel as nib
name = "group_mean_thresh_fdr_E_C_cluster_extent20_thr1.039.dscalar.nii"
mine     = nib.load(f"results/E_C/{name}").get_fdata().ravel()
expected = nib.load(f"example_data/maps/{name}").get_fdata().ravel()
np.array_equal(mine, expected)      # True — 0 differing vertices
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
<summary><b>1 · Threshold, cluster, report and draw in one command</b></summary>

```bash
cifti-state run map.dscalar.nii --method fixed --threshold 1.09 --extent 20
```

You get the cluster label map as a `dscalar.nii` in the input's own layout, a
per-cluster report, a peak table, a region-by-region annotation table, and a
JSON record of the parameters. Add `--render` for the figure.

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
it — a t map read as z gives overstated p-values, silently.

```bash
cifti-state run tmap.dscalar.nii --statistic t --df 29 --method fdr --q 0.05
```

</details>

<details>
<summary><b>4 · A map that is not on fs_LR 32k</b></summary>

Nothing to set: the density is read from the file, and the whole pipeline runs
at it — adjacency, cluster areas, the atlas, the figure.

```bash
cifti-state run zmap_10k.dscalar.nii --method fixed --threshold 1.039 --extent 5
```

To move a map between meshes instead:

```bash
cifti-state meshes                                  # what is available here
cifti-state resample zmap.dscalar.nii --to fsaverage5 --nan mask
```

See [Working at other resolutions](#working-at-other-resolutions), or
[`docs/MESHES.md`](docs/MESHES.md) for the full reference.

</details>

<details>
<summary><b>5 · Many maps, one parameter set</b></summary>

```bash
cifti-state batch "derivatives/*/stats/*_zstat.dscalar.nii" \
    --method fdr --q 0.05 --extent 20 --atlas Glasser_2016 \
    --format csv -o results/batch
```

Each map gets its own output folder; one map failing does not stop the rest.

</details>

<details>
<summary><b>6 · Record a run so it can be repeated</b></summary>

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
<summary><b>7 · Match an analysis you have already published</b></summary>

Three switches exist because the conventional choices are not the only ones,
and an existing result may have been produced under different ones:

| option | what it pins |
|---|---|
| `--direction positive` | only the positive tail is searched, so no negative cluster can survive |
| `--inf-policy legacy` | `±inf` are both mapped to the largest finite value (rather than `clip`, which sends `-inf` to the smallest) |
| `fdr_legacy_tail: true` | only values on the tested side enter the FDR calculation |

`--legacy` sets the first two together:

```bash
cifti-state run map.dscalar.nii --method fixed --threshold 1.09 --extent 20 --legacy
```

The output is named `<input>_cluster_extent<N>_thr<T>.dscalar.nii`, so it drops
straight into an existing folder structure.

</details>

<details>
<summary><b>8 · Compare two groups across subjects, corrected for the whole surface</b></summary>

Describe the study in a CSV — one row per scan, one column of file paths, and
whatever else describes it:

```csv
subject,group,age,sex,file
sub-01,patient,34,M,maps/sub-01_thickness.dscalar.nii
sub-02,control,29,F,maps/sub-02_thickness.dscalar.nii
...
```

```bash
cifti-state stats participants.csv --test two-sample --group group \
    --covariate age --covariate sex \
    --cluster-forming 3.1 --extent 20 \
    --permutations 5000 -o results/patients_vs_controls
```

```
two_sample: 28 observations, intercept + group[b] + age  [contrast: b - a, df=25]
  59412 vertices tested
  |t| max 5.848
  59412 vertices -> 608.0 resels (EC 2, FWHM 13.67 mm)
  peak FWE 0.05 at 5.750
  permutation peak FWE 0.05 at 5.699

8 clusters at t > 3.1, extent >= 20
 cluster_id hemi  size_vertices  size_resels   peak_t  p_rft_cluster  p_rft_peak  p_perm_cluster  p_perm_peak
          2    L            211         2.21     5.85         0.0021      0.0403          0.0040       0.0319
          5    R             53         0.88     5.05         0.2388      0.2330          0.6607       0.2176
          6    R             43         0.48     4.04         0.7712      1.0000          0.8044       0.8683
```

The planted blob is cluster 2; everything else is noise, and both corrections
agree about which is which. `--test one-sample` and `--test paired` (with
`--condition` and `--subject`) take the same options. See
[Group statistics](#group-statistics) for what the columns mean and which of
them to trust.

</details>

<details>
<summary><b>9 · The desktop interface</b></summary>

```bash
cifti-state-gui                                  # or: cifti-state-gui map.dscalar.nii
```

Three steps down the left: **Data** (pick the file, declare z or t) →
**Cluster** (threshold, direction, extent) → **Anatomical report** (atlas,
regions per cluster, minimum share, table style). The workspace on the right
has the preview above and the cluster table + log below.

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
<summary><b>10 · Python API — the whole pipeline in eight lines</b></summary>

```python
from cifti_state import load_settings, run_analysis, AnalysisSpec

settings = load_settings("example_data/example.yaml")
spec = AnalysisSpec(
    input_path="example_data/maps/group_mean_thresh_fdr_E_C.dscalar.nii",
    threshold_method="fixed", threshold_value=1.039,
    extent=20, atlas="Glasser_2016",
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
<summary><b>11 · Python API — step by step, for when you need the pieces</b></summary>

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
clusters = find_clusters(stat_map, adjacency, thr, extent=20)
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
<summary><b>12 · Python API — figures and the 3D view</b></summary>

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
<summary><b>13 · Calling Connectome Workbench</b></summary>

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

A pair of `*.func.gii` metric files can be read with
`load_surface_stat_map_from_gifti`. Other mesh densities already work the same
way given matching templates — see [Roadmap](#roadmap).

**Adjacency** comes either from a neighbour table (`neighbor_source: txt`, one
row per vertex) or from the surface mesh itself (`neighbor_source: surface`).
The two give the identical graph on fs_LR 32k — checked vertex for vertex,
32 480 vertices with 6 neighbours and 12 with 5 — and the mesh route needs no
extra files, so it is what the bundled example uses.

## Atlases

```bash
cifti-state atlases          # what is available in your template pack
cifti-state atlases --all    # including ones not in the registry
```

Region names come from each `label.gii`'s own LabelTable, so **any** atlas
following the `<name>.32k.{L,R}.label.gii` convention works — Glasser 2016,
AAL, Yeo 7/17, Schaefer, Gordon, Power, Desikan, Destrieux, Fan, Shen,
Baldassano, Wang, the Icosahedrons, and the rest of the pack. Labels are keyed
by `(hemisphere, label_id)`, so an atlas that reuses the same id range in both
hemispheres is handled correctly without offsets. A CSV, where one is
configured, is applied on top as a display-name override.

## Group statistics

Everything above starts from a statistic map someone else computed. This part
computes it — a vertex-wise model across subjects — and then says which of the
result survives correction over the whole surface.

### Describing the study

One CSV or TSV, one row per scan, one column holding the path to that scan's
map, and whatever other columns describe it. Relative paths resolve against the
table's own folder, so a study folder can be moved without editing anything.

```csv
subject,group,condition,age,sex,file
sub-01,patient,pre,34,M,maps/sub-01_pre.dscalar.nii
sub-01,patient,post,34,M,maps/sub-01_post.dscalar.nii
sub-02,control,pre,29,F,maps/sub-02_pre.dscalar.nii
...
```

### The three tests

| test | what it asks | how the rows are used |
|---|---|---|
| `--test one-sample` | is the mean map different from zero? | every row is one observation |
| `--test two-sample --group G` | do two independent groups differ? | `G` splits the rows; the contrast is *second minus first*, and which is which is printed, never left to alphabetical luck |
| `--test paired --condition C --subject S` | do two measurements of the same subjects differ? | rows are paired on `S`; the data becomes the difference and the model is a one-sample test of it |

`--covariate age --covariate sex` adds nuisance regressors. Continuous
covariates are mean-centred (so a one-sample intercept means "the mean at the
average age", not "the value at age zero"); categorical ones are dummy coded.
The model that was actually fitted is reported back:

```
two_sample: 28 observations, intercept + group[b] + age  [contrast: b - a, df=25]
  - covariate age mean-centred at 32.5
  - group sizes: a n=14, b n=14
```

`--variance welch` drops the equal-variance assumption for a two-sample test.
The degrees of freedom then differ from vertex to vertex, so the map is
converted to z before correction — that is stated in the result rather than
done quietly.

### How much of it is real: two corrections

Both control the family-wise error rate over the whole surface. They rest on
different assumptions, so running both and comparing them is the cheapest
confidence you will ever buy.

**Random field theory** measures how smooth the field is — from the model's
*residuals*, which is the only thing in the analysis that is pure noise — and
asks what a null map of that smoothness would have thrown up. The currency is
the resel, a patch one FWHM across:

```
59412 vertices -> 608.0 resels (EC 2, FWHM 13.67 mm)
peak FWE 0.05 at 5.437
```

**Permutation** rearranges the data under the null instead: sign flips for a
one-sample or paired test, group shuffling for a two-sample one, with
Freedman–Lane residualisation so covariates survive the rearrangement. It
assumes nothing about the distribution or the smoothness. With few enough
subjects every rearrangement is enumerated and the test becomes exact.

```bash
--permutations 5000        # ~1 minute per thousand on a whole cortex
```

Each cluster then gets up to five P values, and they answer different questions:

| column | level | meaning |
|---|---|---|
| `p_rft_peak` | peak | this cluster's highest vertex, corrected — evidence about *that vertex* |
| `p_perm_peak` | peak | the same, from the permutation max-statistic null |
| `p_rft_cluster` | cluster | this cluster's extent in resels, corrected |
| `p_perm_cluster` | cluster | the same, from the permutation max-extent null |
| `p_tfce` | vertex | the best TFCE vertex in this cluster, corrected — see below |

Cluster-level evidence licenses "something is going on in this blob"; it never
licenses "this vertex is significant". Peak-level evidence is the other way
round.

### TFCE: not having to pick a threshold

Every number above depends on the cluster-forming threshold, and the answer
moves when you change it: a low threshold favours large diffuse effects, a high
one favours focal peaks, and nothing tells you which is right for an effect you
have not seen yet. Threshold-free cluster enhancement removes the choice by
integrating over all of them. Each vertex is scored by

```
TFCE(v) = ∫ e(h)^E · h^H dh
```

where `e(h)` is the extent of the supra-threshold cluster containing *v* at
height *h*. A vertex is rewarded for being high (`h^H`) and for belonging to
something big (`e(h)^E`), so a broad low bump and a narrow tall spike can both
win. On the surface the extent is **area in mm²**, not a vertex count — an
uneven tessellation must not hand an advantage to whichever region happens to
be sampled more densely.

```bash
--tfce            # PALM's defaults, H=2 E=0.5
--tfce-2d         # PALM's -tfce2D, H=2 E=1, which its docs suggest for surfaces
--tfce-H 2 --tfce-E 1 --tfce-dh 0.1     # or set them yourself
```

TFCE has no null distribution of its own, so it only becomes a test alongside
`--permutations`; ask for it without them and you get the map, and a warning
saying it is a map to look at rather than a test. The output is two more
files — the score, and a whole map of FWE-corrected P values, one per vertex,
with no cluster-forming threshold anywhere in it:

```
  TFCE (H=2, E=1, dh=auto (max/100)): 4 vertices at corrected P<=0.05

 cluster_id hemi  size_vertices  peak_t  p_rft_cluster  p_perm_cluster   peak_tfce  p_tfce
          2    L             81   4.043          0.029           0.064     7300.57   0.050
          3    L             50   4.693          0.250           0.486     3761.96   0.706
          1    L             49   4.954          0.382           0.512     2366.29   0.982
          4    L             22   3.661          0.697           0.984     1944.61   0.998
```

Note cluster 1: the tallest peak of the four, and the third-ranked by TFCE.
Height alone did not carry it, and that is the statistic working as intended.

**Agreement with PALM.** This is a reimplementation of `palm_tfce.m`, and it is
tested against the real thing rather than against a description of it: PALM's
function was run in GNU Octave over four meshes — different sizes, masked and
unmasked, three settings of H and E — and the two agree to **1e-15 relative**,
which is machine precision. Those reference vectors are committed
(`tests/data/palm_tfce_reference.npz`) and the comparison runs in the ordinary
test suite. The details that have to match are documented in
`cifti_state/stats/tfce.py`, along with the one deliberate departure: PALM also
accepts a fixed `-tfce_dh`, but its surface branch never assigns the `dh` it
multiplies by at the end, so that path raises an error inside PALM itself;
`--tfce-dh` here is implemented the way PALM's *volume* branch does it.

It costs about 0.2 s per map on the fs_LR 32k cortex — roughly 3.5 minutes per
thousand rearrangements, on top of the permutation loop itself.

### How well calibrated are they?

Measured, not assumed. Null data smoothed to ~11 mm FWHM on the fs_LR 32k mesh,
one-sample tests, cluster-forming threshold 3.1, nominal family-wise error rate
0.05:

| correction | realised FWE | |
|---|---|---|
| RFT, peak level | **0.040** ± 0.017 | as promised |
| permutation, peak level | **0.042** ± 0.036 | as promised |
| permutation, cluster extent | **0.058** ± 0.042 | as promised |
| RFT, cluster extent | **0.158** ± 0.032 | liberal |
| RFT, cluster extent, Gaussian closed form | 0.206 ± 0.035 | worse still — which is why it is not the default |

(500 replications at n=28 for the RFT rows, 120 replications × 200
rearrangements at n=24 for the permutation rows; ± is a 95% interval on the
simulation itself. Reproduce them with `examples/make_example_study.py
--effect 0` and your own loop.)

The peak-level and permutation results are what they should be. The RFT
cluster-extent result is not, and the intermediate quantities say why: the
expected *number* of clusters comes out right to within a few percent (9.71
observed against 9.39 predicted), but real clusters are 25–35% larger than the
theory expects, because the vertices that cross the threshold sit
preferentially where the field happens to be locally rough. This is a known
limitation of parametric cluster-extent inference (Eklund, Nichols & Knutsson
2016) rather than a defect here — the formulas reproduce SurfStat's to about
one part in a thousand, checked directly against
[BrainStat](https://github.com/MICA-MNI/BrainStat).

TFCE was measured separately, on a smaller search region so that 200 null
realisations × 500 rearrangements were affordable (a 40 × 40 lattice, FWHM 4,
n=16, nominal 0.05, ± is a 95% interval on the simulation):

| correction | realised FWE | |
|---|---|---|
| TFCE, H=2 E=0.5 (PALM default) | **0.030** ± 0.030 | as promised |
| TFCE, H=2 E=1 (`--tfce-2d`) | **0.035** ± 0.030 | as promised |
| permutation, peak level | 0.050 ± 0.030 | the same run, for reference |
| permutation, cluster extent | 0.045 ± 0.030 | the same run, for reference |

**So: report peak-level RFT freely, and base cluster-level claims on the
permutation or TFCE P values.** `cifti-state stats` says so too, in a warning,
if you run it without permutations.

### From Python

```python
from cifti_state import load_settings
from cifti_state.stats import load_participants, two_sample_t

settings = load_settings()
people = load_participants("study/participants.csv")

analysis = two_sample_t(
    people, settings, "group",
    covariates=["age", "sex"],
    permutations=5000, cluster_forming=3.1, extent=20,
    tfce=True,             # or tfce_settings=PALM_2D for H=2, E=1
)
print(analysis.summary())

analysis.stat_map          # an ordinary SurfaceStatMap - cluster it, report it, draw it
analysis.smoothness        # resels, FWHM in mm, resels per vertex
analysis.random_field      # peak and cluster P values under RFT
analysis.permutation       # the max-statistic, max-extent and max-TFCE nulls
analysis.tfce              # the TFCE score, one value per vertex
analysis.tfce_p            # FWE-corrected P per vertex, no threshold anywhere
analysis.tfce_stat_map()   # both of those as SurfaceStatMaps, ready to draw
analysis.to_json("run.json")
```

The statistic map is an ordinary
[`SurfaceStatMap`](#input-formats), so the clustering, the anatomical report,
the figures and the interface all take it without knowing where it came from:

```python
from cifti_state.core import compute_threshold, find_clusters
from cifti_state.pipeline import build_adjacency
from cifti_state.results import AnalysisSpec

threshold = compute_threshold(analysis.stat_map.finite_values(),
                              method="fixed", value=3.1, direction="two_sided",
                              statistic="t", df=analysis.dof)
adjacency = build_adjacency(analysis.stat_map, settings, AnalysisSpec(mesh="32k"))
clusters = find_clusters(analysis.stat_map, adjacency, threshold, extent=20,
                         direction="two_sided")

analysis.correct_clusters(clusters)      # a DataFrame with the four P columns
```

### Trying it without any data

The repository cannot ship a cohort, so it ships a generator:

```bash
python examples/make_example_study.py --out study --effect 0.9
python examples/make_example_study.py --out study_paired --paired --effect 0.7
python examples/group_stats_example.py --study study --paired-study study_paired
```

That plants a blob of known size in one group and walks through all three
tests, the smoothness estimate, and both corrections, printing what it finds at
every step. `--effect 0` gives a null study, if you want to watch the
correction do nothing.

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

## Working at other resolutions

fs_LR 32k is the default, not a requirement.

> **Full reference:** [`docs/MESHES.md`](docs/MESHES.md) — every mesh, every
> configuration key, every command line option, every Python entry point, what
> each error message means, and a worked case on real data. The section below
> is the tour.
 A map on another mesh runs the
whole pipeline at its own density — adjacency, cluster areas, peak coordinates,
the atlas, the figure — and a map on one mesh can be moved to another.

### Naming a mesh

A mesh is a **pair**: how many vertices, and which registration space they were
aligned in. That is not pedantry — fs_LR 10k and fsaverage5 both have 10 242
vertices per hemisphere and are completely different surfaces. An array of
10 242 numbers does not say which one it is, so this package never guesses:

```
fsLR:32k     32 492 vertices/hemisphere    (a bare "32k" also means this)
fsLR:10k     10 242
fsLR:59k     59 292        ← a denser mesh, NOT the 59 412-greyordinate layout
fsLR:164k   163 842
fsaverage4    2 562        fsaverage5   10 242
fsaverage6   40 962        fsaverage   163 842
```

```bash
cifti-state meshes            # which of them have their template files here
cifti-state meshes --routes   # what can be converted to what
```

When a vertex count is ambiguous the analysis stops and asks, rather than
picking a space and producing a map that looks perfectly normal and is wrong.
`defaults.mesh` (or `--mesh`) settles it.

### Converting

```bash
cifti-state resample sub-01_bold.dtseries.nii --to fsLR:10k
cifti-state resample zmap.dscalar.nii --to fsaverage5 --nan mask -o zmap_fs5.dscalar.nii
```

```
fsLR:32k -> fsaverage5 [ADAP_BARY_AREA], 1 map(s), medial wall excluded, ROI-masked
  output  zmap_fs5.dscalar.nii
```

Under it is `wb_command -metric-resample ... ADAP_BARY_AREA`, which is what HCP
uses and what the field expects. The package's job is everything around that
call, which is where surface resampling normally goes wrong:

| | |
|---|---|
| **The right spheres** | fs_LR → fs_LR uses each mesh's own sphere. fs_LR → fsaverage uses the `fs_LR-deformed_to-fsaverage` sphere on one side and the fsaverage standard sphere on the other. Get the pair wrong and the output is smooth, plausible, and rotated with respect to the anatomy. |
| **The area metrics** | `ADAP_BARY_AREA` without `-area-metrics` quietly degrades to something close to plain barycentric. They are never optional here: a mesh without its area metric cannot be an end of a conversion, and says so by name. |
| **The medial wall** | A 91k or 59k file has no data there. Without `-current-roi` the absent wall bleeds into the vertices beside it — wrong in exactly the ring where cortex is thinnest. |
| **NaN** | A thresholded map stores NaN below threshold, and interpolation spreads each one over its whole neighbourhood. On the bundled example map, 32k → 10k keeps **8.0% → 1.3%** of cortex by default and **8.0% → 6.7%** with `--nan mask`. |
| **Label maps** | A network mask or a parcellation holds label *numbers*, and averaging those invents a network 4 between 3 and 5. An integer-valued map with few levels is recognised and each target vertex takes the label covering most of it — `wb_command`'s `-largest`, extended to parcellations stored as a plain dscalar. `--continuous` forces averaging back on. |
| **Routing** | fs_LR 10k has no fsaverage-deformed sphere, so `fsLR:10k → fsaverage5` cannot be done in one step. It is done in two, through fs_LR 32k, and the extra interpolation that costs is reported rather than hidden. |

Every `wb_command` line is kept and can be printed with `--print-commands`, so
a surprising result can be reproduced by hand.

A round trip is the check that catches all of the above at once: fs_LR 32k →
10k → 32k on a smooth field comes back at **r = 0.9995**, and the test suite
asserts it. For a binary network mask the same round trip returns a **Dice of
0.998** — three vertices in 20 484 changed.

The ambiguity is not hypothetical. An individual language-network mask at
10 242 vertices per hemisphere, read as fs_LR 10k, lands on TPOJ1, STSdp,
STSda, A5, STGa, 55b and PSL. The same file read as fsaverage5 lands in V1,
fusiform and postcentral cortex — a speckled map with no anatomy in it. Both
answers look like brain maps; only one is the language network. That is why
`identify_mesh` raises instead of picking.

### Templates, without a wall of configuration

The template files have stable, self-describing names —
`fs_LR.32k.L.sphere.surf.gii`, `fsaverage5_std_sphere.L.10k_fsavg_L.surf.gii`,
`S900.L.midthickness_MSMAll.10k_fs_LR_va.shape.gii` — so the configuration
lists **directories**, not files:

```yaml
resources:
  root: fs_LR_32k
  mesh_dirs: [fs_LR_32k, 10k, resample_fsaverage]
```

Anything missing is reported by name, with what it is for and where the search
looked. A file that does not follow the conventions can be pinned in
`resources.mesh_files`. The search never recurses and skips `old_`/`fix_`
prefixes, because HCP's `resample_fsaverage/misc/` holds superseded spheres and
using one silently would be a wrong answer that looks entirely normal.

### The pipeline follows the data

```bash
cifti-state run zmap_10k.dscalar.nii --method fixed --threshold 1.039 --extent 5
```

```
loaded zmap_10k.dscalar.nii [18722 greyordinates]: L 9362/10242, R 9360/10242
input is on fsLR:10k (10242 vertices per hemisphere)
resampling atlas Glasser_2016 from fsLR:32k to fsLR:10k
found 40 clusters (left 22, right 18) at fixed(+1.039), extent >= 5
```

The density is read from the file. The adjacency comes from that mesh's
surface, cluster areas and peak coordinates from its midthickness, the figure
from its inflated surface, and the sulcal underlay is resampled to it. The
Glasser atlas ships on 32k, so it is resampled once with
`wb_command -label-resample ... -largest` — each target vertex takes the label
covering most of it, never an interpolated average — and cached. All 360
regions survive.

### From Python

The whole API is in [`docs/MESHES.md`](docs/MESHES.md#8-python-api); the shape
of it:

```python
from cifti_state import load_settings
from cifti_state.mesh import MeshLibrary, plan_route, resample_cifti

settings = load_settings()
print(settings.mesh_library().report())        # what is available

result = resample_cifti("zmap.dscalar.nii", "zmap_10k.dscalar.nii",
                        "fsLR:10k", settings, nan="mask")
print(result.describe())
result.commands            # every wb_command line, ready to paste into a shell
result.warnings            # two-hop routes, NaN handling, missing atlasroi
```

### What about neuromaps?

[neuromaps](https://github.com/netneurolab/neuromaps) solves the same problem
well, and its surface transforms shell out to these same `wb_command` calls —
what it adds is fetching the template files from OSF. Those files are already
here, so the default backend calls Workbench directly: one dependency fewer, no
download (which matters on a machine that cannot reach OSF), and a call trace
you can read. Pass `backend="neuromaps"` if you would rather use its atlas
management.

## Roadmap

### More of the statistical model

The t-tests, all three corrections and PALM-equivalent TFCE are built (see
[Group statistics](#group-statistics)). What the
[SurfStat](https://www.math.mcgill.ca/keith/surfstat/) model offers beyond
them, and what is planned next:

| planned | SurfStat equivalent | what it would add |
|---|---|---|
| F contrasts | `SurfStatF` | test several predictors at once, not one contrast |
| mixed effects | `SurfStatLinMod` with random terms | repeated measures and longitudinal designs beyond a simple pair |
| accept a user-supplied FWHM or resel count | `SurfStatResels` | drive the RFT correction from a smoothness estimated elsewhere |
| accept residual maps directly | — | correct a model that was fitted in another package |

A non-parametric FDR over clusters would also close the gap the calibration
table above points at, and is the most likely next addition.

### Other surface resolutions

fs_LR 32k is no longer the only density (see
[Working at other resolutions](#working-at-other-resolutions)). What is still
to come:

- **fs_LR 59k and 164k, fsaverage4/6/fsaverage** are in the mesh registry and
  work as soon as their template packs are on the search path; only the
  fs_LR 10k and fsaverage5 routes are shipped ready to run.
- **FreeSurfer-native input** — reading `.mgh`/`.mgz` surface data and `.annot`
  parcellations directly, rather than going through GIFTI and CIFTI.
- **A per-density atlas registry**, so `cifti-state atlases` says what exists at
  the density you are working in rather than resampling on demand.
- **Subject-native surfaces**, resampled through each subject's own
  `sphere.reg`, for analyses that should not go through a group template at all.

Clustering is linear in the number of vertices, so 164k costs about five times
32k and stays comfortable; building the adjacency and rendering are the parts
that grow, and both are cached.

### Smaller items

- **Volume structures.** The 19 subcortical structures in a 91k file are
  currently carried through untouched. Clustering them in 3D, with a matching
  report, is a natural extension of the same machinery.
- **`.spec` / `.scene` generation for wb_view**; it is currently launched with a
  plain file list.
- **Sub-peaks.** `find_local_peaks` is implemented but not yet surfaced in the
  interface or the pipeline.
- **Batch mode in the interface**; for now use `cifti-state batch`.

## Project layout

```
cifti_state/
  config.py     Settings dataclasses, platform detection, path resolution
  wb.py         wb_command / wb_view wrapper, every call logged in full
  io/           cifti · surface · neighbors · atlas
  core/         threshold · cluster · peaks · annotate · report · mask
  stats/        design · data · glm · resels · rft · permutation · tfce · ttest
  mesh/         spaces (naming) · templates (finding) · resample (converting)
  fonts.py      font resolution and UTF-8, shared by Qt and matplotlib
  viz/          render (surfplot) · interactive (PyVista) · underlay (sulci)
                · colormaps · layouts
  gui/          theme · widgets · state · workers · panels/ · main_window
  pipeline.py   the orchestration the CLI and the GUI share
  results.py    AnalysisSpec / AnalysisResult, both serialisable
  cli.py
```

Longer-form documentation lives in `docs/`:

| | |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | A ten-step Windows install, with the expected output of every command. |
| [`docs/MESHES.md`](docs/MESHES.md) · [中文](docs/MESHES.zh-CN.md) | Surface meshes and resampling: every mesh, configuration key, CLI option, Python entry point and error message, plus a worked case. |

`core/` is written to six rules so a Qt front end can call it directly: pure
functions over arrays, no printing (only `logging`), a `progress` callback on
anything slow, a `cancel` token on anything long, configuration passed in
rather than read from globals, and every result carrying the parameter snapshot
that produced it — `AnalysisResult.to_json()` is enough to reproduce a run.

## Tests

```bash
pytest          # 207 tests; on a bare clone 3 skip (they need the neighbour
                # tables and the Desikan atlas, which are not bundled)
```

No setup: the data-backed tests run against `example_data/` by default,
including the regression tests that assert vertex-for-vertex equality with the
reference cluster maps shipped there. To point them at your own copy instead:

```bash
export CIFTI_STATE_TEST_CONFIG=configs/machines/<hostname>.yaml
export CIFTI_STATE_TEST_EXAMPLES=/path/to/maps
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
regression fixtures above.

The group statistics get their own arbiters: every t-test is checked against
scipy's `ttest_1samp` / `ttest_ind` / `ttest_rel` (including Welch's degrees of
freedom) to machine precision; the resel counts are checked against
[BrainStat](https://github.com/MICA-MNI/BrainStat)'s implementation of
SurfStat, and agree to 1e-15; the EC densities and peak P values agree with it
to 1e-8; the estimated FWHM is checked against a field smoothed by a known
kernel; a mesh conversion is checked by round trip -- fs_LR 32k to 10k and back
correlates at 0.9995 with the original, which no wrong sphere or missing area
metric would survive; the permutation machinery is checked on the one identity
that must hold exactly — the unpermuted rearrangement reproduces the observed statistic,
covariates and all; and the TFCE is checked against PALM's own `palm_tfce.m`,
run in Octave, to 1e-15.

## Example data

`example_data/` holds two statistic maps, the expected cluster map for each, and
the minimum fs_LR 32k template files needed to run everything above — about
8 MB in total. See [`example_data/README.md`](example_data/README.md) for what
each file is and where the third-party templates come from.

## References and acknowledgements

**Templates.** The fs_LR 32k files in `example_data/fs_LR_32k/` come from
[DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32). The
surfaces derive from the HCP group average (Van Essen DC, Glasser MF, Dierker
DL, Harwell J, Coalson T. *Parcellations and hemispheric asymmetries of human
cerebral cortex analyzed on surface-based atlases.* Cerebral Cortex 2012;
22:2241–2262). The default parcellation is Glasser MF et al. *A multi-modal
parcellation of human cerebral cortex.* Nature 2016; 536:171–178. Please cite
those works when you use them.

**Statistics.** Worsley KJ, Taylor JE,
Carbonell F, Chung MK, Duerden E, Bernhardt B, Lyttelton O, Boucher M, Evans
AC. *SurfStat: A Matlab toolbox for the statistical analysis of univariate and
multivariate surface and volumetric data using linear mixed effects models and
random field theory.* NeuroImage 2009; 47(Suppl 1):S102. The random field
theory behind the correction is Worsley KJ, Marrett S, Neelin P, Vandal AC,
Friston KJ, Evans AC. *A unified statistical approach for determining
significant signals in images of cerebral activation.* Human Brain Mapping
1996; 4:58–73. The Python successor is
[BrainStat](https://github.com/MICA-MNI/BrainStat), which this package's resel
and EC-density code is validated against. Permutation inference follows Winkler
AM, Ridgway GR, Webster MA, Smith SM, Nichols TE. *Permutation inference for the
general linear model.* NeuroImage 2014; 92:381–397. TFCE is Smith SM, Nichols
TE. *Threshold-free cluster enhancement: addressing problems of smoothing,
threshold dependence and localisation in cluster inference.* NeuroImage 2009;
44:83–98; the implementation here is checked against `palm_tfce.m` from
[PALM](https://github.com/andersonwinkler/PALM), by the same authors as the
permutation reference above. The calibration of
cluster-extent inference is Eklund A, Nichols TE, Knutsson H. *Cluster failure:
why fMRI inferences for spatial extent have inflated false-positive rates.*
PNAS 2016; 113:7900–7905.

**Software.** Figures are drawn with
[surfplot](https://github.com/danjgale/surfplot) and
[BrainSpace](https://github.com/MICA-MNI/BrainSpace); the interactive view uses
[PyVista](https://pyvista.org/); CIFTI and GIFTI reading is
[NiBabel](https://nipy.org/nibabel/); surface operations and `wb_view` are
[Connectome Workbench](https://www.humanconnectome.org/software/connectome-workbench).

## License

MIT — see [LICENSE](LICENSE). The files in `example_data/fs_LR_32k/` are third
party and carry their own terms; see
[`example_data/README.md`](example_data/README.md).
