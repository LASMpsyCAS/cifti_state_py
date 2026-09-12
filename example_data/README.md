# Example data / 示例数据

Enough to run everything in the main README from a fresh clone — no downloads,
no paths to fix.

从零开始跑通主 README 里所有例子所需的全部数据，不用下载、不用改路径。

```
example_data/
  example.yaml          a complete, self-contained configuration pointing here
  maps/                 the statistic maps and the expected cluster maps
  fs_LR_32k/            the minimum fs_LR 32k template files (third party)
  10k/                  the fs_LR 10k mesh: spheres, areas, ROIs, surfaces
  resample_fsaverage/   the spheres and area metrics that cross between
                        fs_LR and fsaverage
```

Run with `--config example_data/example.yaml`, or set it once:

```bash
export CIFTI_STATE_CONFIG=$PWD/example_data/example.yaml      # bash
$env:CIFTI_STATE_CONFIG = "$PWD\example_data\example.yaml"    # PowerShell
```

---

## `maps/` — the analysis data

Two group-level contrast maps and, for each, the cluster map that analysing it
should produce. The pair is what makes this a *regression* fixture rather than
a demo: the result is compared against a known answer, vertex for vertex.

| File | What it is |
|---|---|
| `group_mean_thresh_fdr_E_C.dscalar.nii` | Group mean z map, contrast E–C, already FDR-thresholded. 91 282 greyordinates. |
| `group_mean_thresh_fdr_E_C_cluster_extent20_thr1.039.dscalar.nii` | The expected cluster label map for it: threshold 1.039, extent ≥ 20. **32 clusters** (L 15 / R 17). |
| `group_mean_thresh_fdr_E_D.dscalar.nii` | Group mean z map, contrast E–D, FDR-thresholded. |
| `group_mean_thresh_fdr_E_D_cluster_extent20_thr1.09.dscalar.nii` | The expected cluster label map: threshold 1.09, extent ≥ 20. **76 clusters** (L 38 / R 38). |

Both maps are in the **91k** layout (29 696 + 29 716 cortical vertices with the
medial wall excluded, plus 19 subcortical structures). `tests/test_regression.py`
reproduces both cluster maps exactly, including the cluster numbering.

These come from gw's own analysis and are provided so the toolkit can be tested;
they are not a public dataset and carry no separate licence.

这两张图来自 gw 自己的分析，随包提供仅供测试之用，不是公开数据集。

## `fs_LR_32k/` — template files (third party)

The smallest set that makes the example self-contained. Not produced by this
project.

| File | Used for |
|---|---|
| `fs_LR.32k.{L,R}.midthickness.surf.gii` | Vertex coordinates for peak locations; surface-derived adjacency. |
| `fs_LR.32k.{L,R}.inflated.surf.gii` | The default rendering surface. |
| `fs_LR.32k.LR.sulc.dscalar.nii` | The greyscale sulcal underlay. |
| `Glasser_2016.32k.{L,R}.label.gii` | Region names for the anatomical report (HCP-MMP1, 180 areas per hemisphere). |
| `fs_LR.32k.{L,R}.sphere.surf.gii` | The registration sphere. Only resampling uses it — but without it fs_LR 32k cannot be one end of a conversion. |

**Source:** [DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32),
the standard fs_LR 32k template pack (left–right symmetric, ~32k vertices per
hemisphere). The surfaces and `sulc` derive from the HCP group average
(Van Essen et al., *Cerebral Cortex* 2012); the parcellation is Glasser et al.,
*Nature* 2016. Cite those works, not this repository, when you use them, and
check that repository's terms before redistributing the files further.

They are bundled here purely so `git clone` is enough to run the example. If
you would rather not redistribute them, delete `example_data/fs_LR_32k/` and
point `resources.root` at your own copy of the full template pack — the full
pack also gives you the other surfaces (`very_inflated`, `pial`, `white`,
`flat`, `sphere`), the other underlays, and the ~20 other atlases the toolkit
can read.

如果你不希望在自己的仓库里再分发这些第三方文件，删掉 `example_data/fs_LR_32k/`，
把 `resources.root` 指向你本地的完整 fs_LR 模板包即可；完整包还带有其他曲面、
其他底板和另外约 20 个图谱。

## `10k/` and `resample_fsaverage/` — the other meshes (third party)

What `cifti-state resample` needs to move data off fs_LR 32k. `cifti-state
meshes` reports which of them were found; `cifti-state meshes --routes` lists
the conversions they make possible.

Every file is located by its standard HCP or FreeSurfer name, so nothing here
is written down in `example.yaml` individually — the configuration only lists
the directories.

| File | Used for |
|---|---|
| `10k/{L,R}.sphere.10k_fs_LR.surf.gii` | The fs_LR 10k registration sphere. |
| `10k/S900.{L,R}.midthickness_MSMAll.10k_fs_LR_va.shape.gii` | Average vertex areas — what makes `ADAP_BARY_AREA` area-corrected rather than plain barycentric. |
| `10k/{L,R}.atlasroi.10k_fs_LR.shape.gii` | The medial wall, so it neither bleeds into the data nor lands in the output. |
| `10k/S900.{L,R}.{midthickness,inflated,very_inflated,flat}_MSMAll.10k_fs_LR.surf.gii` | Geometry and rendering at 10k. |
| `resample_fsaverage/fs_LR-deformed_to-fsaverage.{L,R}.sphere.{32k,59k,164k}_fs_LR.surf.gii` | The fs_LR mesh with its vertices on the fsaverage sphere — the only way across between the two families. |
| `resample_fsaverage/fs_LR.{L,R}.midthickness_va_avg.{32k,59k,164k}_fs_LR.shape.gii` | fs_LR average vertex areas. |
| `resample_fsaverage/fsaverage{4,5,6}_std_sphere.*.surf.gii` and their `midthickness_va_avg` metrics | The fsaverage side of the same pairs. |

**Source:** the HCP `standard_mesh_atlases` / `resample_fsaverage` pack, and the
fs_LR 10k mesh derived from the HCP S900 group average. Same terms as the 32k
files above: cite the original works, and check before redistributing further.

`resample_fsaverage/misc/` holds superseded copies of several of these, named
`old_…` and `fix_…`. They are never used: the file search does not recurse and
skips those prefixes, because resampling through a superseded sphere would give
a wrong answer that looks entirely normal.

`10k/` also contains a 15 MB `Template_space-fsLR_den-28k_bold.dtseries.nii`, a
couple of MATLAB neighbour scripts and some vertex-coordinate text files. None
of them are used by this package; they are left in place because they belong to
the original pack.

`resample_fsaverage/misc/` 里放的是被取代的旧版球面（`old_`、`fix_` 前缀），
本工具**绝不会**用到它们——文件搜索不递归、也跳过这两个前缀，
因为用错了球面得到的结果看起来完全正常，却是错的。

## What is *not* bundled

- **`lh/rh.neighbors_IndexStart0.txt`** (2.6 MB) — pre-computed neighbour tables.
  `example.yaml` sets `neighbor_source: surface` instead, which builds the same
  graph from the mesh. This was checked: the two adjacency matrices differ in
  **0** entries on fs_LR 32k, and both give the cluster counts above. Point
  `resources.neighbors` at your own tables if you would rather use them.
- **The atlas CSVs** (`Anatomical-labels-csv/`) — optional prettier region
  names. Without them the report uses the names in each atlas's own GIFTI label
  table, which is the authoritative source anyway.
- **Connectome Workbench** — only needed for `wb_command` operations and for
  opening results in `wb_view`.
