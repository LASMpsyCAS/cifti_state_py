# Surface meshes and resampling — the complete reference

[English](MESHES.md) · [中文](MESHES.zh-CN.md)

The short version is in the main README under
[Working at other resolutions](../README.md#working-at-other-resolutions).
This is the full reference: every mesh, every configuration key, every command
line option, every Python entry point, and what each error message means.

---

## Contents

- [1. What a mesh is here](#1-what-a-mesh-is-here)
- [2. The meshes](#2-the-meshes)
- [3. Template files](#3-template-files)
- [4. Configuration](#4-configuration)
- [5. `cifti-state meshes`](#5-cifti-state-meshes)
- [6. `cifti-state resample`](#6-cifti-state-resample)
- [7. Running the pipeline at another density](#7-running-the-pipeline-at-another-density)
- [8. Python API](#8-python-api)
- [9. Error messages](#9-error-messages)
- [10. A worked case: an individual language network](#10-a-worked-case-an-individual-language-network)
- [11. What this does not do](#11-what-this-does-not-do)

---

## 1. What a mesh is here

A mesh is a **pair**: how many vertices, and which registration space they were
aligned in. Both halves are load-bearing.

| | |
|---|---|
| **Density** | 10 242, 32 492, 163 842 … vertices per hemisphere. Decides array shapes. |
| **Registration space** | `fsLR` or `fsaverage`. Decides *where on the cortex* vertex 5000 is. |

They are independent, and the pairs collide:

```
fs_LR 10k    10 242 vertices          fsaverage5   10 242 vertices
fs_LR 164k  163 842 vertices          fsaverage   163 842 vertices
```

An array of 10 242 numbers does not say which one it is. Treating fsaverage5
data as fs_LR 10k produces a map that is the right size, renders without
complaint, and is anatomically scrambled — see
[section 10](#10-a-worked-case-an-individual-language-network) for what that
looks like on real data.

So this package **never guesses**. Meshes are named as `family:density`
(`fsLR:32k`) or by the fsaverage name (`fsaverage5`), and an ambiguous vertex
count raises rather than picking.

> **A naming collision worth knowing.** "59k" means two different things.
> A **59k CIFTI layout** (59 412 greyordinates) is the fs_LR **32k** mesh with
> the medial wall dropped — a layout, not a mesh. The **fs_LR 59k mesh** is a
> genuinely denser mesh, 59 292 vertices per hemisphere. Only the number is
> shared.

---

## 2. The meshes

| Name | Aliases | Vertices/hemi | Dense CIFTI | Registration |
|---|---|---|---|---|
| `fsLR:10k` | `10k`, `fslr10k`, `fs_LR_10k` | 10 242 | 20 484 | fs_LR |
| `fsLR:32k` | `32k`, `fslr32k`, `fs_LR_32k` | 32 492 | 64 984 | fs_LR |
| `fsLR:59k` | `59k`, `fslr59k` | 59 292 | 118 584 | fs_LR |
| `fsLR:164k` | `164k`, `fslr164k` | 163 842 | 327 684 | fs_LR |
| `fsaverage4` | `fsavg4`, `fsaverage-3k` | 2 562 | 5 124 | fsaverage |
| `fsaverage5` | `fsavg5`, `fs5`, `fsaverage-10k` | 10 242 | 20 484 | fsaverage |
| `fsaverage6` | `fsavg6`, `fs6`, `fsaverage-41k` | 40 962 | 81 924 | fsaverage |
| `fsaverage` | `fsavg`, `fsaverage7`, `fsaverage-164k` | 163 842 | 327 684 | fsaverage |

Names are case-insensitive and ignore `-`, `_` and `:`, so `FS_LR_32K`,
`fslr32k` and `fsLR:32k` are the same thing. **A bare density means fs_LR** —
that is what every configuration file in this package has always meant by
`mesh: "32k"`.

Adding a density is one row in `cifti_state/mesh/spaces.py` plus its filename
patterns in `templates.py`. Nothing else in the package hard-codes a vertex
count.

### Which pairs can convert

Two meshes can be resampled in one step when their spheres can be expressed in
a common space:

- **Same family** → each mesh's own sphere. `fsLR:32k ↔ fsLR:10k`, on the fs_LR
  sphere.
- **fs_LR ↔ fsaverage** → the fs_LR mesh's `fs_LR-deformed_to-fsaverage`
  sphere on one side, the fsaverage standard sphere on the other. The meeting
  space is always fsaverage, because HCP ships the deformed spheres in that
  direction only.

When no direct pair exists the route goes through an intermediate mesh. fs_LR
10k has no deformed sphere, so `fsLR:10k → fsaverage5` is done in two hops via
fs_LR 32k, and the extra interpolation is reported in `result.warnings`.

---

## 3. Template files

Each mesh needs up to seven files per hemisphere. Only the first two are
required to take part in a conversion.

| Role | Required for | What it is |
|---|---|---|
| `sphere` | resampling | The registration sphere. Without it the mesh cannot be an end of a conversion. |
| `area` | resampling | Average midthickness vertex areas. `ADAP_BARY_AREA` without these quietly degrades to something close to plain barycentric, so they are **never optional** here. |
| `sphere@fsaverage` | crossing families | The fs_LR mesh with its vertices on the fsaverage sphere. fs_LR meshes only. |
| `roi` | recommended | The medial-wall mask. Keeps the wall out of the interpolation and out of the output layout. |
| `midthickness` | analysis | Vertex areas, adjacency, cluster surface area, peak coordinates. |
| `inflated` | figures | The default rendering surface. |
| `very_inflated`, `flat` | figures | Optional alternatives. |

### The filenames it looks for

Files are found by their standard HCP and FreeSurfer names, so a configuration
lists **directories**, not files. `{H}` is `L` or `R`. The first pattern that
matches wins; each role also has permissive fallbacks not listed here.

**fs_LR meshes** (`<den>` is `10k`, `32k`, `59k` or `164k`):

```
sphere              fs_LR.<den>.{H}.sphere.surf.gii
                    {H}.sphere.<den>_fs_LR.surf.gii
sphere@fsaverage    fs_LR-deformed_to-fsaverage.{H}.sphere.<den>_fs_LR.surf.gii
area                fs_LR.{H}.midthickness_va_avg.<den>_fs_LR.shape.gii
                    *.{H}.midthickness*<den>_fs_LR_va.shape.gii
roi                 {H}.atlasroi.<den>_fs_LR.shape.gii
midthickness        fs_LR.<den>.{H}.midthickness.surf.gii
                    *.{H}.midthickness_MSMAll.<den>_fs_LR.surf.gii
inflated            fs_LR.<den>.{H}.inflated.surf.gii
                    *.{H}.inflated_MSMAll.<den>_fs_LR.surf.gii
```

**fsaverage meshes** (`<name>` is `fsaverage4/5/6/fsaverage`, `<den>` is
`3k`/`10k`/`41k`/`164k`):

```
sphere              <name>_std_sphere.{H}.<den>_fsavg_{H}.surf.gii
area                <name>.{H}.midthickness_va_avg.<den>_fsavg_{H}.shape.gii
roi                 <name>.{H}.atlasroi.<den>_fsavg_{H}.shape.gii
midthickness        <name>.{H}.midthickness.<den>_fsavg_{H}.surf.gii
inflated            <name>.{H}.inflated.<den>_fsavg_{H}.surf.gii
```

### Two rules that keep the search honest

1. **It never recurses.** The HCP `resample_fsaverage` pack keeps superseded
   spheres in a `misc/` subdirectory.
2. **It skips any basename starting `old_` or `fix_`.** Same reason.

Resampling through a superseded sphere gives a wrong answer that looks entirely
normal, so both rules are deliberate rather than an optimisation. There is a
test asserting no matched file comes from `misc/` or carries those prefixes.

### Where to get them

| Pack | Contains |
|---|---|
| HCP `standard_mesh_atlases` | fs_LR spheres, atlasroi masks, group-average surfaces at 32k/59k/164k |
| HCP `resample_fsaverage` | the `fs_LR-deformed_to-fsaverage` spheres, the fsaverage standard spheres, and every `midthickness_va_avg` metric |
| [DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32) | a compact fs_LR 32k set |
| FreeSurfer `$FREESURFER_HOME/subjects/fsaverage*` | fsaverage surfaces, after conversion to GIFTI |

`example_data/` bundles enough for fs_LR 32k, fs_LR 10k and fsaverage5.

---

## 4. Configuration

```yaml
resources:
  root: fs_LR_32k                              # unchanged: the working mesh
  atlas_dir: fs_LR_32k

  # NEW: where to look for mesh templates. Relative paths resolve against
  # this file. Leaving it out searches `root` and its sibling directories.
  mesh_dirs: [fs_LR_32k, 10k, resample_fsaverage]

  # NEW, and rarely needed: pin a file whose name does not follow the
  # conventions. Roles are the ones in section 3.
  mesh_files:
    fsaverage6:
      sphere:
        left:  /data/atlas/my_fsavg6_lh_sphere.surf.gii
        right: /data/atlas/my_fsavg6_rh_sphere.surf.gii
      area:
        left:  /data/atlas/my_fsavg6_lh_va.shape.gii
        right: /data/atlas/my_fsavg6_rh_va.shape.gii

  surfaces:                                    # unchanged
    left:
      midthickness: fs_LR.32k.L.midthickness.surf.gii
      inflated:     fs_LR.32k.L.inflated.surf.gii
    right:
      ...

defaults:
  mesh: "32k"      # the working mesh, and the tie-breaker for ambiguous counts
```

### How the two surface sources interact

`resources.surfaces` still wins for the **working mesh** (the one
`defaults.mesh` names), so an existing configuration behaves exactly as it did.
Any other mesh is resolved through the template search. The order for
`settings.surface_for(hemi, kind, mesh=...)` is:

1. `resources.surfaces` — if the requested mesh is the working mesh and that
   kind is listed there
2. the template search in `mesh_dirs`
3. `resources.surfaces` again, as a last resort, before raising

### Neighbour tables

`resources.neighbors` is keyed by mesh, and both spellings work:

```yaml
resources:
  neighbors:
    "32k":       # or "fsLR:32k" — both resolve
      left:  lh.neighbors_IndexStart0.txt
      right: rh.neighbors_IndexStart0.txt
```

With `defaults.neighbor_source: surface` no tables are needed at all; the
adjacency comes from the mesh of whichever density the data is on.

---

## 5. `cifti-state meshes`

```bash
cifti-state meshes            # what is known, and what was found here
cifti-state meshes --routes   # also every conversion the templates allow
```

```
=== known meshes ============================================
mesh         vertices/hemi  family      note
fsLR:10k             10242  fsLR        same vertex count as fsaverage5, different space
fsLR:32k             32492  fsLR        the HCP standard; what this package's example data uses
fsLR:59k             59292  fsLR        a denser mesh -- NOT the 59412-greyordinate layout
fsLR:164k           163842  fsLR
fsaverage4            2562  fsaverage
fsaverage5           10242  fsaverage   same vertex count as fsLR:10k, different space
fsaverage6           40962  fsaverage
fsaverage           163842  fsaverage

mesh templates found in 3 directories:
  .../example_data/fs_LR_32k
  .../example_data/10k
  .../example_data/resample_fsaverage
  ok  fsLR:10k     area, inflated, midthickness, roi, sphere
  ok  fsLR:32k     area, inflated, midthickness, sphere, sphere@fsaverage
      fsLR:59k     nothing found
      ...
  ok  fsaverage5   area, sphere

=== conversions =============================================
  fsLR:10k -> fsLR:32k
  fsLR:10k -> fsLR:32k -> fsaverage5   (two hops)
  fsLR:32k -> fsLR:10k
  fsLR:32k -> fsaverage5
  fsaverage5 -> fsLR:32k -> fsLR:10k   (two hops)
  fsaverage5 -> fsLR:32k
```

`ok` means the mesh has both a sphere and an area metric, and can therefore be
an end of a conversion. `cifti-state check` prints the same list in one line.

---

## 6. `cifti-state resample`

```bash
cifti-state resample INPUT --to MESH [options]
```

| Option | Default | What it does |
|---|---|---|
| `--to MESH` | required | Target mesh: `fsLR:10k`, `fsaverage5`, `32k`, … |
| `--from MESH` | read from the file | Source mesh. **Required when the vertex count is ambiguous** (10 242 and 163 842). |
| `-o, --output PATH` | beside the input | Output file. The default appends the target name. |
| `--via MESH` | automatic | Force an intermediate mesh. Repeatable. |
| `--method` | `ADAP_BARY_AREA` | Or `BARYCENTRIC`. Use the default unless you have a reason. |
| `--nan` | `propagate` | `mask` treats NaN as absent data instead of letting it spread. |
| `--coverage` | `0.5` | How much of a target vertex's catchment must be real data for it to keep a value. |
| `--continuous` | off | Force averaging even for integer-valued data. |
| `--discrete` | off | Force the parcellation path even when the values do not look like labels. |
| `--medial-wall` | `auto` | `auto` mirrors the input; `exclude` / `include` force it. |
| `--no-roi` | off | Do not pass the wall as `-current-roi`. For bit-matching a pipeline that skipped this. |
| `--backend` | `wb` | Or `neuromaps` — see [section 11](#11-what-this-does-not-do). |
| `--print-commands` | off | Print every `wb_command` line that was run. |

### What happens underneath

For each hemisphere:

```
wb_command -cifti-separate  IN COLUMN -metric CORTEX_LEFT lh.func.gii [-roi lh.roi.shape.gii]
wb_command -metric-resample lh.func.gii  <src sphere> <dst sphere> ADAP_BARY_AREA lh_out.func.gii \
                            -area-metrics <src area> <dst area> \
                            [-current-roi lh.roi.shape.gii] [-valid-roi-out ...]
wb_command -cifti-create-dense-scalar OUT -left-metric ... [-roi-left ...] -right-metric ... [-name-file ...]
```

`-cifti-create-dense-timeseries` for a dtseries (timestep, timestart and unit
preserved) and `-cifti-create-label` for a dlabel. Map names on a dscalar are
carried across via `-name-file`.

### `--nan`: thresholded maps

A thresholded statistic map stores NaN below threshold. Barycentric
interpolation spreads each NaN over every target vertex whose triangle touches
it, so the surviving blob **erodes**. On the bundled example map, 32k → 10k:

| | finite cortex |
|---|---|
| input | 8.0 % |
| `--nan propagate` (what `wb_command` does alone) | 1.3 % |
| `--nan mask` | 6.7 % |

`mask` folds the NaN into the source ROI so it takes no part in the weighting,
then resamples the ROI itself with the same weights to get each target vertex's
**coverage fraction**, and writes NaN back wherever coverage falls below
`--coverage`. Neither dilating nor eroding.

> Two simpler things were tried and rejected: masking alone gives 21.2 %
> (values filled in from far away), and `-valid-roi-out` alone dilates too — it
> keeps any vertex that caught a single valid source.

### `--discrete`: parcellations and network masks

A network mask or a parcellation holds label **numbers**. Averaging them
invents labels: interpolating between networks 3 and 5 produces a 4 along every
border.

`wb_command` has `-label-resample … -largest` for this, but only for a real
`.dlabel.nii`. A parcellation handed over as a plain dscalar — which is how
most individual-parcellation pipelines write them — cannot use it.

So the default is `discrete: "auto"`: an integer-valued map with 64 or fewer
distinct levels is recognised and each target vertex takes **the label covering
most of it**. This is computed by resampling each label's 0/1 indicator with
the ordinary weights — the resampled indicator *is* the covered fraction — and
taking the largest, with background winning when no label covers half. It is
the same answer `-largest` would give.

A warning says when this happened. `--continuous` forces averaging back on;
`--discrete` forces the label path on.

### Exit behaviour

Non-zero exit with the reason on stderr when the mesh is unknown, ambiguous, or
unreachable with the templates present. The output file is only written on
success.

---

## 7. Running the pipeline at another density

Nothing to configure. The density is read from the input file.

```bash
cifti-state run zmap_10k.dscalar.nii --method fixed --threshold 1.039 --extent 5
```

```
loaded zmap_10k.dscalar.nii [18722 greyordinates]: L 9362/10242, R 9360/10242
input is on fsLR:10k (10242 vertices per hemisphere)
resampling atlas Glasser_2016 from fsLR:32k to fsLR:10k
found 40 clusters (left 22, right 18) at fixed(+1.039), extent >= 5
```

What follows the data:

| Step | Source |
|---|---|
| Adjacency | that mesh's midthickness (or its neighbour table, if configured) |
| Clustering, threshold, extent | that mesh |
| Figures | that mesh's inflated surface |
| Sulcal underlay | resampled to that mesh, then cached |
| Group statistics | smoothness, resels and permutation all run at that density |
| **The report** | **fs_LR 32k** — see below |

Caches live under `runtime.tmp_dir` (default: the system temp directory,
`cifti_state/atlases/` and `cifti_state/underlays/`). They are keyed by mesh and
invalidated when the source file is newer.

`--mesh` overrides the detection, and is how you resolve an ambiguous vertex
count for a `run` rather than a `resample`.

### The report is written on fs_LR 32k

Atlases are distributed on fs_LR 32k. Carrying one *down* to meet coarser data
works, but it throws away the thing being asked for: a 180-region parcellation
on a 10k mesh has whole areas represented by a handful of vertices, and the
region percentages get correspondingly coarse.

So the report goes the other way. Once the clusters are found — at whatever
density the data is on — they are projected **up** onto fs_LR 32k with
`wb_command -label-resample … -largest` (one call per hemisphere, all clusters
at once) and measured and named there, against the atlas as distributed. A
table then means the same thing whatever density produced it, and two studies
at different densities can be read side by side.

What is measured where is not arbitrary — it follows what each number *is*:

| Column | Measured on | Why |
|---|---|---|
| `peak_value`, `mean_value`, `sd_value`, `min_value`, `max_value` | the analysis density | These are the data. An interpolated peak height is a number that appears in no file you have. |
| `size_mm2`, `peak_x/y/z`, `centroid_x/y/z` | fs_LR 32k | The finer mesh measures area more faithfully, and the coordinates then match the atlas. |
| `peak_region`, `primary_region`, `primary_region_percent`, `regions` | fs_LR 32k | Where the atlas is defined, at its own resolution. |
| `peak_vertex` | fs_LR 32k | The vertex sitting where the real peak is — mapped through the shared registration sphere, not recomputed as the argmax of the resampled map, which can land a vertex over and carry a different value. |
| `size_vertices` | fs_LR 32k | The report mesh. |
| `size_vertices_native` | the analysis density | What the `--extent` threshold was actually applied to. Present only when a projection happened. |

The cluster map is written twice: in the input's own layout, and again on the
report mesh as `..._fsLR-32k.dscalar.nii`, so the `peak_vertex` column can be
checked against something. **Figures are still drawn at the analysis density**,
on that mesh's own surfaces.

Nothing changes for an analysis that is already on fs_LR 32k: the projection is
skipped, and `size_vertices_native` does not appear.

```yaml
defaults:
  report_mesh: "fsLR:32k"     # the default
  # report_mesh: native       # measure each report at its own density instead
```

`--report-mesh native` does the same for one run. If the report mesh cannot be
reached — its templates are not on the search path — the run does not fail: the
table is measured at the analysis density with the atlas carried down to it,
which is what this package did before, and a warning says so.

---

## 8. Python API

```python
from cifti_state import load_settings
from cifti_state.mesh import (
    MeshSpace, MeshLibrary, MeshFiles, MeshError,
    parse_mesh, identify_mesh, list_meshes,
    plan_route, resample_cifti, resample_metric, resample_label_gifti,
)
```

### Naming

```python
parse_mesh("32k")                  # MeshSpace(name='fsLR:32k', ...)
parse_mesh("fs5").n_vertices       # 10242
list_meshes("fsaverage")           # every fsaverage mesh, by size

identify_mesh(32492)               # fsLR:32k
identify_mesh(10242)               # raises MeshError: ambiguous
identify_mesh(10242, prefer="fsLR:32k")   # fsLR:10k  (same family wins)
```

`MeshSpace` fields: `name`, `family`, `density`, `n_vertices`, `n_both`,
`registration`, `aliases`, `note`; plus `.describe()`.

### Finding templates

```python
settings = load_settings()
library = settings.mesh_library()          # built once, cached on the settings
print(library.report())

files = library.files_for("fsLR:32k")      # a MeshFiles
files.can_resample                         # sphere + area both present
files.sphere("left")                       # native sphere
files.sphere("left", "fsaverage")          # the fsaverage-deformed one
files.area("left")
files.roi("left")                          # None when absent
files.surface("left", "inflated")
files.surface_kinds()                      # ['midthickness', 'inflated']
files.describe()                           # dict, JSON-safe
library.available()                        # meshes that can be resampled
```

Every lookup that fails raises `MeshError` with the message in
[section 9](#9-error-messages), not a `KeyError`.

### Planning and converting

```python
plan_route("fsLR:10k", "fsaverage5", library)
# [ResampleStep(fsLR:10k -> fsLR:32k, 'fsLR'),
#  ResampleStep(fsLR:32k -> fsaverage5, 'fsaverage')]

result = resample_cifti(
    "zmap.dscalar.nii", "zmap_10k.dscalar.nii", "fsLR:10k", settings,
    source=None,           # read from the file unless ambiguous
    library=None,          # defaults to settings.mesh_library()
    method="ADAP_BARY_AREA",
    use_roi=True,
    medial_wall="auto",    # auto | exclude | include
    nan="propagate",       # propagate | mask
    coverage=0.5,
    discrete="auto",       # "auto" | True | False
    via=None,
    backend="wb",
    workdir=None,          # keeps the intermediates when given
    progress=None, cancel=None,
)
```

`ResampleResult` fields:

| Field | |
|---|---|
| `output` | the file that was written |
| `source`, `target` | `MeshSpace` |
| `steps` | the hops taken |
| `method`, `kind`, `n_maps` | |
| `used_roi` | whether `-current-roi` was passed |
| `medial_wall` | `excluded` / `included` / `unchanged` |
| `commands` | every `wb_command` line, ready to paste into a shell |
| `warnings` | two-hop routes, NaN handling, label detection, missing atlasroi |
| `.describe()`, `.to_dict()` | one line, and a JSON-safe dict |

### One hemisphere at a time

```python
step = plan_route("fsLR:32k", "fsLR:10k", library)[0]
resample_metric("lh.func.gii", "lh_10k.func.gii", step, "left",
                library, settings, method="ADAP_BARY_AREA")
resample_label_gifti("lh.label.gii", "lh_10k.label.gii", step, "left",
                     library, settings)
```

### Settings helpers

```python
settings.mesh_library()                             # MeshLibrary, cached
settings.mesh_of(10242)                             # uses defaults.mesh to disambiguate
settings.surface_for("left", "inflated", mesh="fsLR:10k")
```

### Everything else takes a `mesh=`

```python
load_hemisphere_surfaces(settings, "midthickness", mesh="fsLR:10k")
load_neighbors(settings, "left", mesh="fsLR:10k", source="surface")
load_atlas("Glasser_2016", settings, mesh="fsLR:10k")     # resamples + caches
```

### Projecting a finished analysis onto the report mesh

```python
from cifti_state.mesh import (
    project_for_report, project_stat_map, project_clusters,
    nearest_target_vertices,
)

projection = project_for_report(stat_map, clusters, "fsLR:32k", settings)
projection.stat_map        # SurfaceStatMap on fs_LR 32k
projection.clusters        # ClusterResult on fs_LR 32k, same ids and signs
projection.surfaces        # the 32k midthickness, per hemisphere
projection.native_sizes    # cluster id -> vertex count at the analysis density
projection.peak_vertices   # cluster id -> the report-mesh vertex of the real peak
projection.warnings        # e.g. a cluster too small to survive a downward projection
```

`project_for_report` returns `None` when the analysis is already on the target
mesh. The three primitives are usable on their own:
`project_stat_map` (continuous, ROI-aware), `project_clusters` (labels, one
`-largest` call per hemisphere, returns `(ClusterResult, warnings)`) and
`nearest_target_vertices` (vertex addresses through the shared sphere).

---

## 9. Error messages

**Ambiguous vertex count**

```
10242 vertices per hemisphere is ambiguous -- it could be fsLR:10k or
fsaverage5. They have the same mesh size but different registration spaces,
so resampling one as the other would be wrong. Say which with mesh=/--mesh,
or set defaults.mesh in the configuration.
```

Pass `--from fsLR:10k` (or `--mesh` for `run`). If you do not know which it is,
[section 10](#10-a-worked-case-an-individual-language-network) shows how to
find out.

**Unknown mesh**

```
unknown mesh 'fsLR:7k'; known meshes are fsLR:10k, fsLR:164k, fsLR:32k,
fsLR:59k, fsaverage, fsaverage4, fsaverage5, fsaverage6 (a bare density such
as '32k' means the fs_LR mesh of that density)
```

**A template file is missing**

```
fsaverage6 has no sphere file for the left hemisphere.
  It is the registration sphere -- resampling cannot happen without it.
  Expected a file named like: fsaverage6_std_sphere.L.41k_fsavg_L.surf.gii,
                              fsaverage6.L.sphere.41k_fsavg_L.surf.gii
  Looked in:
    .../example_data/fs_LR_32k
    .../example_data/10k
    .../example_data/resample_fsaverage
  Add the directory holding it to resources.mesh_dirs, or name the file
  directly in resources.mesh_files.
```

**No route**

```
cannot get from fsLR:32k to fsaverage6 with the templates available.
<followed by the full mesh report>
```

One of the two ends is missing a sphere or an area metric, or the pair needs an
intermediate mesh that is not available either. The report underneath says
which.

**The map's mesh disagrees with the configuration** — not an error. The file
wins, and the run records a note:

```
the map is on fsLR:10k (10242 vertices per hemisphere) but the configuration
says fsLR:32k; using fsLR:10k
```

---

## 10. A worked case: an individual language network

An individual language-network mask from an MS-HBM-style parcellation,
10 242 vertices per hemisphere, binary. Which space is it in? The filename does
not say and the vertex count cannot.

**Resolve it by anatomy.** Convert to fs_LR 32k under both hypotheses and see
where the mask lands in a parcellation you trust:

```python
from cifti_state.io.atlas import load_atlas
from cifti_state.mesh import resample_cifti
import numpy as np, nibabel as nib

atlas = load_atlas("Glasser_2016", settings)
for hypothesis in ("fsLR:10k", "fsaverage5"):
    resample_cifti(src, f"as_{hypothesis}.dscalar.nii", "fsLR:32k",
                   settings, source=hypothesis)
    ...
```

```
as fsLR:10k    L: TPOJ1, STSdp, A5, TPOJ2, STSda, STSva, STV, PGi, STGa
               R: TPOJ1, STSdp, STSda, STV, STSva, STGa, 55b, PSL
as fsaverage5  L: V1, FFC, TF, PF, 3b, PH, TE2a, AIP, V3
               R: TF, V2, FFC, 24dd, V3, TE1p, 5L
```

The first is a language network. The second is scattered across V1, fusiform
and postcentral cortex. **The file is fs_LR 10k.** Both outputs render as
plausible brain maps; only the anatomy distinguishes them, which is exactly why
the ambiguity is an error rather than a default.

**Then convert.** It is a binary mask, so the label path applies automatically:

```bash
cifti-state resample sub1_LAN.dscalar.nii --from fsLR:10k --to fsLR:32k
cifti-state resample sub1_LAN.dscalar.nii --from fsLR:10k --to fsaverage5
```

| | vertices | area | share of cortex |
|---|---|---|---|
| fs_LR 10k (input) | 776 | 3 716 mm² | 3.31 % |
| → fs_LR 32k | 2 435 | 4 452 mm² | 3.58 % |
| → fsaverage5 | 771 | — | — |
| ← back to fs_LR 10k | 773 | 3 704 mm² | 3.30 % |

Round trip **Dice 0.998** — three vertices in 20 484 changed. Compare areas as a
*share of cortex*, not in mm²: a coarser mesh has flatter triangles and measures
about 10 % less total surface, which is a property of the mesh, not of the
conversion.

**Checking the fsaverage route.** There is no fsaverage5 anatomical surface in
the bundled pack, so verify it by going out and back:

```
10k → 32k          direct : 2435 vertices
10k → fsaverage5 → 32k    : 2428 vertices     Dice 0.965
```

with the same Glasser regions in the same rank order. Dice 0.965 rather than
0.998 is the honest cost of two extra interpolations **plus crossing
registration spaces** — fs_LR and fsaverage are genuinely different alignments,
and that difference is real, not error.

---

## 11. What this does not do

**It does not reimplement resampling.** `wb_command -metric-resample` does the
arithmetic. This package chooses the spheres, insists on the area metrics,
handles the medial wall and NaN, plans routes, and keeps the command trace.
Connectome Workbench must be installed and on `PATH` (or named in
`workbench.wb_command`).

**neuromaps.** [neuromaps](https://github.com/netneurolab/neuromaps) solves the
same problem well, and its surface transforms shell out to the same
`wb_command` calls — what it adds is fetching template files from OSF. Those
files are already here, so `backend="wb"` is the default: one dependency fewer,
no download (which matters where OSF is unreachable), and a readable call
trace. `backend="neuromaps"` is accepted but currently raises with an
explanation rather than silently doing the same thing through a translation
layer.

**Volumes.** Cortical surface structures only. The subcortical structures of a
91k file are not carried through a resampling; convert the surface part and
recombine if you need both.

**Subject-native surfaces.** Everything here goes through group templates.
Resampling through each subject's own `sphere.reg` is not implemented.

**FreeSurfer formats.** Input and output are CIFTI and GIFTI. `.mgh`/`.mgz` and
`.annot` are not read or written; the `mri_convert` step some pipelines end with
has no equivalent here yet.

**Not planned: guessing.** If a vertex count is ambiguous, it will keep raising.
