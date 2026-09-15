# 皮层网格与重采样 —— 完整参考

[English](MESHES.md) · [中文](MESHES.zh-CN.md)

简版在主 README 的[在别的分辨率上工作](../README.zh-CN.md#在别的分辨率上工作)。
这份是完整参考：所有网格、所有配置项、所有命令行选项、所有 Python 接口，
以及每条报错是什么意思。

---

## 目录

- [1. 这里说的「网格」是什么](#1-这里说的网格是什么)
- [2. 网格清单](#2-网格清单)
- [3. 模板文件](#3-模板文件)
- [4. 配置](#4-配置)
- [5. `cifti-state meshes`](#5-cifti-state-meshes)
- [6. `cifti-state resample`](#6-cifti-state-resample)
- [7. 在别的密度上跑整个流程](#7-在别的密度上跑整个流程)
- [8. Python 接口](#8-python-接口)
- [9. 报错信息对照](#9-报错信息对照)
- [10. 一个真实例子：个体语言网络](#10-一个真实例子个体语言网络)
- [11. 这个模块不做什么](#11-这个模块不做什么)

---

## 1. 这里说的「网格」是什么

一个网格是**一对**信息：多少个顶点，以及这些顶点是在哪个配准空间里对齐的。
两半都不能省。

| | |
|---|---|
| **密度** | 每半球 10 242 / 32 492 / 163 842… 个顶点。决定数组形状。 |
| **配准空间** | `fsLR` 或 `fsaverage`。决定第 5000 号顶点**在皮层上的哪个位置**。 |

两者互相独立，而且配对会撞车：

```
fs_LR 10k    每半球 10 242 顶点        fsaverage5   每半球 10 242 顶点
fs_LR 164k  每半球 163 842 顶点        fsaverage   每半球 163 842 顶点
```

一个长度 10 242 的数组不说明自己是哪一个。把 fsaverage5 的数据当 fs_LR 10k 用，
得到的图尺寸对、渲染不报错、解剖位置全乱——
[第 10 节](#10-一个真实例子个体语言网络)有真实数据上的样子。

所以这个包**从不猜**。网格一律写成 `族:密度`（`fsLR:32k`）
或 fsaverage 自己的名字（`fsaverage5`），顶点数有歧义时直接报错，不挑一个。

> **一个值得知道的命名撞车。**「59k」有两个意思。
> **59k CIFTI 布局**（59 412 灰坐标）是 fs_LR **32k** 网格去掉内侧壁——
> 那是一种布局，不是网格。**fs_LR 59k 网格**是另一个确实更密的网格，
> 每半球 59 292 顶点。两者只有数字一样。

---

## 2. 网格清单

| 名字 | 别名 | 每半球顶点 | dense CIFTI | 配准空间 |
|---|---|---|---|---|
| `fsLR:10k` | `10k`、`fslr10k`、`fs_LR_10k` | 10 242 | 20 484 | fs_LR |
| `fsLR:32k` | `32k`、`fslr32k`、`fs_LR_32k` | 32 492 | 64 984 | fs_LR |
| `fsLR:59k` | `59k`、`fslr59k` | 59 292 | 118 584 | fs_LR |
| `fsLR:164k` | `164k`、`fslr164k` | 163 842 | 327 684 | fs_LR |
| `fsaverage4` | `fsavg4`、`fsaverage-3k` | 2 562 | 5 124 | fsaverage |
| `fsaverage5` | `fsavg5`、`fs5`、`fsaverage-10k` | 10 242 | 20 484 | fsaverage |
| `fsaverage6` | `fsavg6`、`fs6`、`fsaverage-41k` | 40 962 | 81 924 | fsaverage |
| `fsaverage` | `fsavg`、`fsaverage7`、`fsaverage-164k` | 163 842 | 327 684 | fsaverage |

名字不分大小写，`-`、`_`、`:` 都忽略，所以 `FS_LR_32K`、`fslr32k`、`fsLR:32k`
是同一个东西。**裸写密度就是 fs_LR**——这个包的配置文件里
`mesh: "32k"` 一直就是这个意思。

加一个密度 = 在 `cifti_state/mesh/spaces.py` 里加一行，
再在 `templates.py` 里加它的文件名模式。包里其他任何地方都没有硬编码顶点数。

### 哪些对可以互转

两个网格能一步转，条件是它们的球面能表达在同一个空间里：

- **同族** → 各用自己的球面。`fsLR:32k ↔ fsLR:10k`，在 fs_LR 球面上。
- **fs_LR ↔ fsaverage** → 一边用 fs_LR 网格的
  `fs_LR-deformed_to-fsaverage` 球面，另一边用 fsaverage 标准球面。
  会合的空间**总是 fsaverage**，因为 HCP 只提供了这个方向的变形球面。

没有直达对时，路线会经过一个中间网格。fs_LR 10k 没有变形球面，
所以 `fsLR:10k → fsaverage5` 会经 fs_LR 32k 走两跳，
多出来的那次插值会写进 `result.warnings`。

---

## 3. 模板文件

每个网格每半球最多七个文件。只有前两个是参与转换的**必需项**。

| 角色 | 什么时候必需 | 是什么 |
|---|---|---|
| `sphere` | 转换 | 配准球面。没有它，这个网格不能作为转换的一端。 |
| `area` | 转换 | 平均 midthickness 顶点面积。`ADAP_BARY_AREA` 不给这个会悄悄退化成接近普通重心插值，所以在这里**从不可选**。 |
| `sphere@fsaverage` | 跨族时 | fs_LR 网格的顶点放到 fsaverage 球面上的版本。只有 fs_LR 网格有。 |
| `roi` | 建议有 | 内侧壁掩膜。把壁挡在插值之外，也挡在输出布局之外。 |
| `midthickness` | 分析 | 顶点面积、邻接、cluster 面积、峰值坐标。 |
| `inflated` | 出图 | 默认渲染曲面。 |
| `very_inflated`、`flat` | 出图 | 可选的另外两种。 |

### 它按什么文件名去找

文件按 HCP / FreeSurfer 的标准名去找，所以配置里列的是**目录**而不是文件。
`{H}` 是 `L` 或 `R`。第一个命中的模式生效；每个角色还有这里没列出的宽松兜底模式。

**fs_LR 网格**（`<den>` 是 `10k`、`32k`、`59k` 或 `164k`）：

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

**fsaverage 网格**（`<name>` 是 `fsaverage4/5/6/fsaverage`，
`<den>` 是 `3k`/`10k`/`41k`/`164k`）：

```
sphere              <name>_std_sphere.{H}.<den>_fsavg_{H}.surf.gii
area                <name>.{H}.midthickness_va_avg.<den>_fsavg_{H}.shape.gii
roi                 <name>.{H}.atlasroi.<den>_fsavg_{H}.shape.gii
midthickness        <name>.{H}.midthickness.<den>_fsavg_{H}.surf.gii
inflated            <name>.{H}.inflated.<den>_fsavg_{H}.surf.gii
```

### 两条保证搜索不出错的硬规则

1. **不递归。** HCP 的 `resample_fsaverage` 包把被取代的旧球面放在 `misc/` 子目录里。
2. **跳过任何以 `old_` 或 `fix_` 开头的文件名。** 同样的理由。

用错了球面得到的是一张看起来完全正常的错图，所以这两条是刻意设计，不是优化。
测试里有一条断言：命中的文件不会来自 `misc/`，也不会带这两个前缀。

### 去哪里拿这些文件

| 包 | 内容 |
|---|---|
| HCP `standard_mesh_atlases` | fs_LR 球面、atlasroi 掩膜、32k/59k/164k 的组平均曲面 |
| HCP `resample_fsaverage` | `fs_LR-deformed_to-fsaverage` 球面、fsaverage 标准球面、所有 `midthickness_va_avg` 度量 |
| [DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32) | 一套精简的 fs_LR 32k |
| FreeSurfer `$FREESURFER_HOME/subjects/fsaverage*` | fsaverage 曲面，需转成 GIFTI |

`example_data/` 里随包的量够 fs_LR 32k、fs_LR 10k 和 fsaverage5 用。

---

## 4. 配置

```yaml
resources:
  root: fs_LR_32k                              # 不变：工作网格
  atlas_dir: fs_LR_32k

  # 新增：去哪里找网格模板。相对路径按本文件所在目录解析。
  # 不写的话，会搜索 root 和它的同级目录。
  mesh_dirs: [fs_LR_32k, 10k, resample_fsaverage]

  # 新增，一般用不到：钉死一个名字不合惯例的文件。角色见第 3 节。
  mesh_files:
    fsaverage6:
      sphere:
        left:  /data/atlas/my_fsavg6_lh_sphere.surf.gii
        right: /data/atlas/my_fsavg6_rh_sphere.surf.gii
      area:
        left:  /data/atlas/my_fsavg6_lh_va.shape.gii
        right: /data/atlas/my_fsavg6_rh_va.shape.gii

  surfaces:                                    # 不变
    left:
      midthickness: fs_LR.32k.L.midthickness.surf.gii
      inflated:     fs_LR.32k.L.inflated.surf.gii
    right:
      ...

defaults:
  mesh: "32k"      # 工作网格，同时也是顶点数有歧义时的定夺依据
```

### 两套曲面来源怎么配合

`resources.surfaces` 对**工作网格**（`defaults.mesh` 指的那个）仍然优先，
所以已有的配置行为完全不变。别的网格走模板搜索。
`settings.surface_for(hemi, kind, mesh=...)` 的顺序是：

1. `resources.surfaces`——如果请求的就是工作网格，而且那种曲面在里面列了
2. `mesh_dirs` 里的模板搜索
3. 再退回 `resources.surfaces`，作为报错前的最后一次尝试

### 邻接表

`resources.neighbors` 按网格索引，两种写法都认：

```yaml
resources:
  neighbors:
    "32k":       # 或 "fsLR:32k"，都能解析
      left:  lh.neighbors_IndexStart0.txt
      right: rh.neighbors_IndexStart0.txt
```

用 `defaults.neighbor_source: surface` 则完全不需要邻接表文件，
邻接直接从数据所在密度的网格上建。

---

## 5. `cifti-state meshes`

```bash
cifti-state meshes            # 有哪些网格，本机上找到了哪些
cifti-state meshes --routes   # 再加上模板允许的所有转换
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

`ok` 表示这个网格既有球面又有面积度量，因此可以作为转换的一端。
`cifti-state check` 里会用一行报告同一份清单。

---

## 6. `cifti-state resample`

```bash
cifti-state resample 输入文件 --to 目标网格 [选项]
```

| 选项 | 默认 | 作用 |
|---|---|---|
| `--to MESH` | 必填 | 目标网格：`fsLR:10k`、`fsaverage5`、`32k`… |
| `--from MESH` | 从文件读 | 源网格。**顶点数有歧义时必填**（10 242 和 163 842）。 |
| `-o, --output PATH` | 输入文件旁边 | 输出文件。默认在文件名后加目标网格名。 |
| `--via MESH` | 自动 | 强制指定中间网格，可重复。 |
| `--method` | `ADAP_BARY_AREA` | 或 `BARYCENTRIC`。没有特别理由就用默认。 |
| `--nan` | `propagate` | `mask` 把 NaN 当缺失数据，不让它扩散。 |
| `--coverage` | `0.5` | 目标顶点的汇集区里要有多少比例是真数据，才保留取值。 |
| `--continuous` | 关 | 强制把整数取值的数据当测量值来平均。 |
| `--discrete` | 关 | 强制走分区图路径，即使取值看起来不像标签。 |
| `--medial-wall` | `auto` | `auto` 跟随输入；`exclude` / `include` 强制。 |
| `--no-roi` | 关 | 不把壁作为 `-current-roi` 传进去。用来和跳过这一步的老流程逐位对齐。 |
| `--backend` | `wb` | 或 `neuromaps`，见[第 11 节](#11-这个模块不做什么)。 |
| `--print-commands` | 关 | 打印跑过的每一条 `wb_command`。 |

### 底下实际跑了什么

每个半球：

```
wb_command -cifti-separate  IN COLUMN -metric CORTEX_LEFT lh.func.gii [-roi lh.roi.shape.gii]
wb_command -metric-resample lh.func.gii  <源球面> <目标球面> ADAP_BARY_AREA lh_out.func.gii \
                            -area-metrics <源面积> <目标面积> \
                            [-current-roi lh.roi.shape.gii] [-valid-roi-out ...]
wb_command -cifti-create-dense-scalar OUT -left-metric ... [-roi-left ...] -right-metric ... [-name-file ...]
```

dtseries 用 `-cifti-create-dense-timeseries`（timestep、timestart、unit 都保留），
dlabel 用 `-cifti-create-label`。dscalar 的 map 名字通过 `-name-file` 带过去。

### `--nan`：阈值化的图

阈值化的统计图用 NaN 表示没过阈值。重心插值会把每个 NaN 摊到
所有三角形碰到它的目标顶点上，于是存活的 blob 被**侵蚀**。
随包例子图，32k → 10k：

| | 有值的皮层比例 |
|---|---|
| 输入 | 8.0 % |
| `--nan propagate`（`wb_command` 自己的行为） | 1.3 % |
| `--nan mask` | 6.7 % |

`mask` 的做法：把 NaN 折进源 ROI，让它完全不参与加权；
然后把 ROI 自己**用同一套权重再重采样一次**，得到每个目标顶点的**覆盖比例**；
覆盖比例低于 `--coverage` 的地方写回 NaN。既不膨胀也不侵蚀。

> 两个更简单的做法试过并否掉了：只做掩膜给出 21.2%（远处的值被填进来）；
> 只用 `-valid-roi-out` 也膨胀——它把碰到一个有效源点的顶点都算有效。

### `--discrete`：分区图和网络掩膜

网络掩膜、分区图里存的是标签**编号**。取平均会造出数据里不存在的标签：
3 号和 5 号之间插值，沿着每条边界都会出来一个 4 号。

`wb_command` 有 `-label-resample … -largest` 专门干这个，
但**只对真正的 `.dlabel.nii` 有效**。以普通 dscalar 交出来的分区图——
大多数个体分区流程就是这么写的——用不了它。

所以默认是 `discrete: "auto"`：整数取值、且不同层级不超过 64 个的图会被识别出来，
每个目标顶点取**覆盖它最多的那个标签**。算法是把每个标签的 0/1 指示函数
用普通权重重采样一次——重采样后的指示函数**就是**被覆盖的比例——取最大的那个，
没有标签覆盖过半时背景胜出。结果与 `-largest` 一致。

发生了这件事会有一条警告。`--continuous` 强制回到取平均，
`--discrete` 强制走标签路径。

### 退出行为

网格未知、有歧义、或者用现有模板走不通时，非零退出，原因打到 stderr。
只有成功时才会写输出文件。

---

## 7. 在别的密度上跑整个流程

什么都不用配。密度是从输入文件里读的。

```bash
cifti-state run zmap_10k.dscalar.nii --method fixed --threshold 1.039 --extent 5
```

```
loaded zmap_10k.dscalar.nii [18722 greyordinates]: L 9362/10242, R 9360/10242
input is on fsLR:10k (10242 vertices per hemisphere)
resampling atlas Glasser_2016 from fsLR:32k to fsLR:10k
found 40 clusters (left 22, right 18) at fixed(+1.039), extent >= 5
```

跟着数据走的东西：

| 环节 | 取自 |
|---|---|
| 邻接 | 该网格的 midthickness（或配置了的邻接表） |
| 阈值、cluster、extent | 该网格 |
| 出图 | 该网格的 inflated |
| 沟回底板 | 重采样到该网格，然后缓存 |
| 组水平统计 | 平滑度、resel、置换检验都在该密度上做 |
| **报表** | **fs_LR 32k**——见下 |

缓存放在 `runtime.tmp_dir` 下（默认系统临时目录的
`cifti_state/atlases/` 和 `cifti_state/underlays/`）。按网格索引，
源文件更新了会自动失效。

`--mesh` 可以覆盖自动检测，也是 `run`（而不是 `resample`）时
解决顶点数歧义的办法。

### 报表一律出在 fs_LR 32k 上

图谱是按 fs_LR 32k 发布的。把图谱**往下**转去迁就更粗的数据也能跑，
但这恰好丢掉了你要的东西：一个 180 区的分区图放到 10k 网格上，
整块脑区只剩几个顶点，报表里的逐区百分比也就跟着变粗。

所以报表反过来走。cluster 在数据自己的密度上找出来之后，
用 `wb_command -label-resample … -largest` **向上**投影到 fs_LR 32k
（每半球一次调用，所有 cluster 一起过去），在那里量、在那里命名，
用的是原样发布的图谱。这样一来，不管分析是在什么密度上做的，
一张表的含义都一样，两个不同密度的研究可以直接并排看。

哪个数在哪儿量，不是随便定的——按每个数**是什么**来分：

| 列 | 在哪量 | 为什么 |
|---|---|---|
| `peak_value`、`mean_value`、`sd_value`、`min_value`、`max_value` | 分析所在密度 | 这些是数据本身。插值出来的峰高是一个在你手上任何文件里都找不到的数。 |
| `size_mm2`、`peak_x/y/z`、`centroid_x/y/z` | fs_LR 32k | 更细的网格量面积更准，坐标也和图谱对得上。 |
| `peak_region`、`primary_region`、`primary_region_percent`、`regions` | fs_LR 32k | 图谱定义在那儿，用它自己的分辨率。 |
| `peak_vertex` | fs_LR 32k | 真实峰值所在位置对应的那个顶点——通过共享配准球面映射过去的，不是重采样后取 argmax（那可能偏一个顶点，而且带着另一个值）。 |
| `size_vertices` | fs_LR 32k | 报表网格。 |
| `size_vertices_native` | 分析所在密度 | `--extent` 阈值实际作用的那个数。只有发生了投影才有这一列。 |

cluster 图会写两份：一份是输入自己的布局，一份是报表网格上的
`..._fsLR-32k.dscalar.nii`，这样 `peak_vertex` 那一列才有东西可以核对。
**图仍然画在分析所在密度上**，用那个网格自己的曲面。

本来就在 fs_LR 32k 上的分析什么都不会变：投影直接跳过，
也不会多出 `size_vertices_native` 这一列。

```yaml
defaults:
  report_mesh: "fsLR:32k"     # 默认
  # report_mesh: native       # 改成每张报表都在自己的密度上量
```

单次运行用 `--report-mesh native` 也一样。如果报表网格够不着——
它的模板不在搜索路径上——运行不会失败：表会退回到分析密度上量、
图谱往下转（也就是这个包以前的做法），并给一条警告说明。

---

## 8. Python 接口

```python
from cifti_state import load_settings
from cifti_state.mesh import (
    MeshSpace, MeshLibrary, MeshFiles, MeshError,
    parse_mesh, identify_mesh, list_meshes,
    plan_route, resample_cifti, resample_metric, resample_label_gifti,
)
```

### 命名

```python
parse_mesh("32k")                  # MeshSpace(name='fsLR:32k', ...)
parse_mesh("fs5").n_vertices       # 10242
list_meshes("fsaverage")           # 所有 fsaverage 网格，按大小排

identify_mesh(32492)               # fsLR:32k
identify_mesh(10242)               # 抛 MeshError：有歧义
identify_mesh(10242, prefer="fsLR:32k")   # fsLR:10k（同族者胜）
```

`MeshSpace` 的字段：`name`、`family`、`density`、`n_vertices`、`n_both`、
`registration`、`aliases`、`note`，另有 `.describe()`。

### 找模板

```python
settings = load_settings()
library = settings.mesh_library()          # 建一次，缓存在 settings 上
print(library.report())

files = library.files_for("fsLR:32k")      # 一个 MeshFiles
files.can_resample                         # 球面和面积都在
files.sphere("left")                       # 自己的球面
files.sphere("left", "fsaverage")          # fsaverage 变形版
files.area("left")
files.roi("left")                          # 没有就是 None
files.surface("left", "inflated")
files.surface_kinds()                      # ['midthickness', 'inflated']
files.describe()                           # dict，可直接 JSON
library.available()                        # 能参与转换的网格
```

所有查找失败都抛 `MeshError`，带[第 9 节](#9-报错信息对照)里那些信息，
不是 `KeyError`。

### 规划与转换

```python
plan_route("fsLR:10k", "fsaverage5", library)
# [ResampleStep(fsLR:10k -> fsLR:32k, 'fsLR'),
#  ResampleStep(fsLR:32k -> fsaverage5, 'fsaverage')]

result = resample_cifti(
    "zmap.dscalar.nii", "zmap_10k.dscalar.nii", "fsLR:10k", settings,
    source=None,           # 不给就从文件读，除非有歧义
    library=None,          # 默认用 settings.mesh_library()
    method="ADAP_BARY_AREA",
    use_roi=True,
    medial_wall="auto",    # auto | exclude | include
    nan="propagate",       # propagate | mask
    coverage=0.5,
    discrete="auto",       # "auto" | True | False
    via=None,
    backend="wb",
    workdir=None,          # 给了就保留中间文件
    progress=None, cancel=None,
)
```

`ResampleResult` 的字段：

| 字段 | |
|---|---|
| `output` | 写出的文件 |
| `source`、`target` | `MeshSpace` |
| `steps` | 走了哪几跳 |
| `method`、`kind`、`n_maps` | |
| `used_roi` | 有没有传 `-current-roi` |
| `medial_wall` | `excluded` / `included` / `unchanged` |
| `commands` | 每一条 `wb_command`，可以直接粘到终端 |
| `warnings` | 两跳路由、NaN 处理、标签识别、缺 atlasroi |
| `.describe()`、`.to_dict()` | 一行摘要，和可 JSON 的 dict |

### 单个半球

```python
step = plan_route("fsLR:32k", "fsLR:10k", library)[0]
resample_metric("lh.func.gii", "lh_10k.func.gii", step, "left",
                library, settings, method="ADAP_BARY_AREA")
resample_label_gifti("lh.label.gii", "lh_10k.label.gii", step, "left",
                     library, settings)
```

### Settings 上的三个方法

```python
settings.mesh_library()                             # MeshLibrary，缓存
settings.mesh_of(10242)                             # 用 defaults.mesh 定夺歧义
settings.surface_for("left", "inflated", mesh="fsLR:10k")
```

### 其他地方都接受 `mesh=`

```python
load_hemisphere_surfaces(settings, "midthickness", mesh="fsLR:10k")
load_neighbors(settings, "left", mesh="fsLR:10k", source="surface")
load_atlas("Glasser_2016", settings, mesh="fsLR:10k")     # 自动转换 + 缓存
```

### 把跑完的分析投影到报表网格

```python
from cifti_state.mesh import (
    project_for_report, project_stat_map, project_clusters,
    nearest_target_vertices,
)

projection = project_for_report(stat_map, clusters, "fsLR:32k", settings)
projection.stat_map        # fs_LR 32k 上的 SurfaceStatMap
projection.clusters        # fs_LR 32k 上的 ClusterResult，id 和符号都不变
projection.surfaces        # 32k 的 midthickness，左右各一
projection.native_sizes    # cluster id → 分析密度下的顶点数
projection.peak_vertices   # cluster id → 真实峰值在报表网格上的顶点
projection.warnings        # 例如某个 cluster 太小、向下投影时没了
```

分析本来就在目标网格上时，`project_for_report` 返回 `None`。
三个基本件也可以单独用：`project_stat_map`（连续值，带 ROI）、
`project_clusters`（标签，每半球一次 `-largest`，返回
`(ClusterResult, warnings)`）、`nearest_target_vertices`（顶点地址，走共享球面）。

---

## 9. 报错信息对照

**顶点数有歧义**

```
10242 vertices per hemisphere is ambiguous -- it could be fsLR:10k or
fsaverage5. They have the same mesh size but different registration spaces,
so resampling one as the other would be wrong. Say which with mesh=/--mesh,
or set defaults.mesh in the configuration.
```

加 `--from fsLR:10k`（`run` 的话是 `--mesh`）。
如果你也不知道是哪个，[第 10 节](#10-一个真实例子个体语言网络)有判断办法。

**网格名不认识**

```
unknown mesh 'fsLR:7k'; known meshes are fsLR:10k, fsLR:164k, fsLR:32k,
fsLR:59k, fsaverage, fsaverage4, fsaverage5, fsaverage6 (a bare density such
as '32k' means the fs_LR mesh of that density)
```

**缺模板文件**

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

**没有路线**

```
cannot get from fsLR:32k to fsaverage6 with the templates available.
<后面跟完整的网格报告>
```

要么两端有一端缺球面或面积度量，要么这对需要一个中间网格、而中间网格也不全。
下面那份报告会说清楚是哪种。

**文件的网格和配置不一致**——这不是错误。文件说了算，运行记录里留一条注记：

```
the map is on fsLR:10k (10242 vertices per hemisphere) but the configuration
says fsLR:32k; using fsLR:10k
```

---

## 10. 一个真实例子：个体语言网络

一张 MS-HBM 式个体分区出来的语言网络掩膜，每半球 10 242 顶点，二值。
它在哪个空间？文件名没说，顶点数也说不了。

**用解剖来判断。** 两个假设各转到 fs_LR 32k，看掩膜落在一个你信得过的分区图的哪些区里：

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
按 fsLR:10k 读    L: TPOJ1, STSdp, A5, TPOJ2, STSda, STSva, STV, PGi, STGa
                  R: TPOJ1, STSdp, STSda, STV, STSva, STGa, 55b, PSL
按 fsaverage5 读  L: V1, FFC, TF, PF, 3b, PH, TE2a, AIP, V3
                  R: TF, V2, FFC, 24dd, V3, TE1p, 5L
```

第一个是语言网络。第二个散落在 V1、梭状回、中央后回。
**这个文件是 fs_LR 10k。** 两个输出都渲染成像样的脑图，
只有解剖能区分它们——这正是为什么这个歧义是报错而不是取默认值。

**然后转。** 它是二值掩膜，所以标签路径会自动生效：

```bash
cifti-state resample sub1_LAN.dscalar.nii --from fsLR:10k --to fsLR:32k
cifti-state resample sub1_LAN.dscalar.nii --from fsLR:10k --to fsaverage5
```

| | 顶点 | 面积 | 占全皮层 |
|---|---|---|---|
| fs_LR 10k（输入） | 776 | 3 716 mm² | 3.31 % |
| → fs_LR 32k | 2 435 | 4 452 mm² | 3.58 % |
| → fsaverage5 | 771 | — | — |
| ← 往返回 fs_LR 10k | 773 | 3 704 mm² | 3.30 % |

往返 **Dice 0.998**——20 484 个顶点里变了三个。
面积要按**占皮层的比例**比，不要直接比 mm²：网格越粗，三角形越平直，
测出来的总面积大约少 10%，那是网格的性质，不是转换的问题。

**检查 fsaverage 这条路。** 随包的模板里没有 fsaverage5 的解剖曲面，
所以用「出去再回来」验：

```
10k → 32k              直达 : 2435 顶点
10k → fsaverage5 → 32k      : 2428 顶点     Dice 0.965
```

Glasser 脑区完全一致、排序也一致。Dice 是 0.965 而不是 0.998，
是两次额外插值**加上跨配准空间**的真实代价——
fs_LR 和 fsaverage 确实是两套不同的对齐，这个差异是真的，不是误差。

---

## 11. 这个模块不做什么

**不重新实现重采样。** 算术是 `wb_command -metric-resample` 做的。
这个包做的是：选球面、坚持要面积度量、处理内侧壁和 NaN、规划路线、留下命令记录。
Connectome Workbench 必须装好并在 `PATH` 上（或在 `workbench.wb_command` 里指明）。

**neuromaps。** [neuromaps](https://github.com/netneurolab/neuromaps)
解的是同一个问题，做得也好，它的面变换底下调的就是同一批 `wb_command`——
它多出来的价值是从 OSF 下载模板文件。而这些文件本来就在这儿了，
所以默认 `backend="wb"`：少一个依赖、不用下载（连不上 OSF 的机器上要紧）、
调用链看得见。`backend="neuromaps"` 这个参数接受，
但目前会带着解释报错，而不是悄悄绕一层翻译去做同一件事。

**体数据。** 只处理皮层面结构。91k 文件里的皮层下结构不会跟着转换走；
两边都需要的话，转完皮层部分再自己合回去。

**被试自身空间的曲面。** 这里的一切都经过组模板。
用各被试自己的 `sphere.reg` 重采样没有实现。

**FreeSurfer 格式。** 输入输出是 CIFTI 和 GIFTI。
`.mgh`/`.mgz` 和 `.annot` 不读不写；有些流程最后那步 `mri_convert`，
这里目前还没有对应的东西。

**不打算做的：猜。** 顶点数有歧义时，它会一直报错。
