<div align="center">

# cifti_state

**fs_LR 32k 皮层表面的 cluster 分析、脑区报表与可视化。**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-112%20passing-brightgreen.svg)](#测试)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#安装)

[English](README.md) · [中文](README.zh-CN.md)

</div>

![cifti_state 界面](docs/images/gui.png)

---

## 它做什么

你手上有一张 fs_LR 32k 表面上的统计图——任何软件出的 z 图或 t 图都行。
这个工具找出过阈值后的连通 cluster，告诉你它们落在哪些脑区，并把它们画出来。

```
map.dscalar.nii  ──▶   阈值    ──▶  cluster  ──▶   峰值 + 图谱脑区   ──▶   表格
     91k/59k/64k     固定/FDR/       连通域        Glasser、AAL、Yeo、      csv/xlsx/md
                      百分位                        Schaefer、Desikan…       + 图
```

| | |
|---|---|
| **三种 fs_LR CIFTI 布局都能读** | 91k、59k、64k 说的都是同一套左右 32k 皮层。顶点映射从每个文件自己的 brain model 里读，不对灰坐标总数做任何假设。GIFTI 半球对也支持。 |
| **能逐顶点复现 MATLAB 的结果** | `legacy_mode` 下 cluster 标签图与原结果完全相同，连编号顺序都一样。有测试逐顶点对拍，见 [MATLAB 对拍](#matlab-对拍)。 |
| **同时修掉了原来的错** | 负向 cluster 现在真的能找到，`-inf` 不再凭空造出 cluster，AAL 右半球用的是右半球的标签。[完整清单](#与-matlab-版本的差异)。 |
| **脑区名来自图谱本身** | 每个 `label.gii` 自带的 LabelTable 就是权威来源，所以模板包里的图谱全都能用，不只是配了 CSV 的那几个。 |
| **三个入口，一套核心** | 命令行 `cifti-state`、notebook 里 `import cifti_state`、界面 `cifti-state-gui`，调的是同一批函数。 |
| **能出版的图 + 能转的 3D** | surfplot 负责导出的静态图，PyVista 负责可旋转的交互视图——同一批数组，都带沟回底板。 |
| **clone 完就能跑例子** | `example_data/` 里既有数据也有最小模板文件。克隆、安装、直接跑。 |

## 安装

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

`scripts\install_windows.cmd` 一步做完同样的事。
[`docs/INSTALL.md`](docs/INSTALL.md) 是十步走的完整实录，每条命令该看到什么输出都写了，
也包含国内 pip 镜像的设置。

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

无头服务器上 VTK 需要离屏上下文——命令放在 `xvfb-run -a` 下跑，
或者装一个用 OSMesa/EGL 编译的 VTK。

</details>

> **为什么用 conda 建环境却用 pip 装包？** conda 解一整套科学计算栈要几分钟，
> pip 解同一套只要几秒。这里 conda 的唯一职责就是提供一个隔离的解释器。

有两个 wheel 很大（`vtk` 和 `PySide6`，各约 100 MB）。如果只要做分析，
单独 `pip install -e .` 只会拉 numpy/scipy/pandas/nibabel/matplotlib/PyYAML，
其余按需装：

```bash
pip install -e ".[viz]"        # surfplot 出图
pip install -e ".[gui]"        # 桌面界面和 3D 视图
pip install -e ".[all]"        # 全部，含测试工具
```

**Connectome Workbench 是可选的。** cluster、报表和两个渲染器都不需要它；
只有调用 `wb_command` 做数据操作、或者用 `wb_view` 打开结果时才用得上。

## 快速上手

仓库里自带了例子需要的全部文件，装完即可直接运行——不用下载，不用改路径。

```bash
export CIFTI_STATE_CONFIG=$PWD/example_data/example.yaml       # bash
# $env:CIFTI_STATE_CONFIG = "$PWD\example_data\example.yaml"   # PowerShell

cifti-state check                                              # 东西都齐了吗？

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

这 32 个 cluster 就是 MATLAB 流程给出的答案，
而 `example_data/maps/` 里放着它的输出，可以直接核对：

```python
import numpy as np, nibabel as nib
name = "group_mean_thresh_fdr_E_C_cluster_extent20_thr1.039.dscalar.nii"
mine      = nib.load(f"results/E_C/{name}").get_fdata().ravel()
reference = nib.load(f"example_data/maps/{name}").get_fdata().ravel()
np.array_equal(mine, reference)      # True —— 0 个顶点不同
```

报表长这样：

| cluster_id | hemi | peak_region | primary_region | % | regions | size | mm² | peak | x | y | z |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | L | V1_ROI | V1_ROI | 70.3 | V1_ROI (70.3%); ProS_ROI (29.7%) | 37 | 92.0 | 1.089 | −19 | −56 | 0 |
| 2 | L | a32pr_ROI | p32pr_ROI | 41.7 | p32pr_ROI (41.7%); a32pr_ROI (41.7%) | 36 | 64.0 | 1.092 | −6 | 23 | 39 |
| 3 | L | 23c_ROI | PCV_ROI | 64.3 | PCV_ROI (64.3%); 23c_ROI (35.7%) | 28 | 43.0 | 1.108 | −7 | −45 | 51 |

然后试试另外两个入口：

```bash
python examples/worked_example.py        # 十步走，把本文所有说法都跑一遍给你看
cifti-state-gui                          # 界面
```

## 各种用例

<details open>
<summary><b>1 · 精确复现一次 MATLAB 分析</b></summary>

`--legacy` 把所有行为差异一次性钉回原样：只做正向、原来的无穷值处理、原来的 FDR 单尾。

```bash
cifti-state run map.dscalar.nii --method fixed --threshold 1.09 --extent 20 --legacy
```

输出文件名沿用 MATLAB 脚本的命名
（`<输入名>_cluster_extent<N>_thr<T>.dscalar.nii`），可以直接放进已有的目录结构。

</details>

<details>
<summary><b>2 · FDR 阈值、双向、换图谱、导出 Excel</b></summary>

```bash
cifti-state run map.dscalar.nii \
    --method fdr --q 0.05 --direction two_sided \
    --extent 20 --atlas Desikan \
    --format xlsx --format csv --report-style long \
    --render --render-layout grid_8 --render-format pdf \
    -o results/fdr_two_sided
```

`--direction two_sided` 会同时找负向 cluster，编号与正向连续。
`--report-style long` 每个 cluster × 脑区一行，而不是把脑区挤进一个单元格。

</details>

<details>
<summary><b>3 · 输入是 t 图而不是 z 图</b></summary>

统计量类型必须声明，因为 p 值换算依赖它。把 t 图当 z 图读会低估 p 值——
这在 MATLAB 版本里是个真实存在的 bug。

```bash
cifti-state run tmap.dscalar.nii --statistic t --df 29 --method fdr --q 0.05
```

</details>

<details>
<summary><b>4 · 一批图，一套参数</b></summary>

```bash
cifti-state batch "derivatives/*/stats/*_zstat.dscalar.nii" \
    --method fdr --q 0.05 --extent 20 --atlas Glasser_2016 \
    --format csv -o results/batch
```

每张图有自己的输出目录；其中一张失败不会中断其余的。

</details>

<details>
<summary><b>5 · 把一次运行记录下来，以便重跑</b></summary>

每次分析都会写出 `*_analysis.json`，里面是完整的参数集；分析也可以直接由 spec 文件驱动：

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

所有字段见 `examples/spec_example.yaml`。

</details>

<details>
<summary><b>6 · 桌面界面</b></summary>

```bash
cifti-state-gui                                  # 或：cifti-state-gui map.dscalar.nii
```

左侧三步：**Data**（选文件、声明 z 还是 t）→ **Cluster**（阈值、方向、extent、legacy）
→ **Anatomical report**（图谱、每个 cluster 报几个区、最小占比、表格版式）。
右侧上方是预览，下方是 cluster 表格和日志。

- 报表参数**只**重跑标注这一步。勾上 *Update as I change these*，表格就跟着控件实时更新，
  不用重新 cluster。
- 表格里选中若干行 → **Save selection as mask…**，把选中的 cluster 写成二值 `dscalar.nii`，
  **与输入同一种灰坐标布局**，可以直接回喂给任何流程。勾 *keep cluster ids* 则写各自的编号而不是 1。
- **Open in wb_view** 把 cluster 图写到临时目录，连同统计图和模板面一起调起 Workbench。
- 预览有两个页签：可旋转的 **3D view**，和导出用的 **Figure**——后者调的就是命令行用的那个函数。
- 所有耗时操作都在 worker 线程上，带进度条和 Cancel。

快捷键：`Ctrl+O` 打开 · `Ctrl+R` cluster · `Ctrl+D` 渲染 · `Ctrl+E` 导出报表 ·
`Ctrl+M` 存 mask · `Ctrl+W` wb_view。

</details>

<details>
<summary><b>7 · Python 接口——八行跑完整个流程</b></summary>

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
result.report                    # 一个 pandas DataFrame
result.outputs                   # {"cluster_map": Path(...), "report_csv": Path(...), …}
result.to_json("run.json")       # 足以复现这次运行
```

</details>

<details>
<summary><b>8 · Python 接口——需要拆开用的时候</b></summary>

```python
from cifti_state import load_settings
from cifti_state.io import load_surface_stat_map, save_like, load_hemisphere_surfaces
from cifti_state.core import (compute_threshold, find_clusters, cluster_peaks,
                              annotate_clusters, build_report, clusters_mask)
from cifti_state.io.atlas import load_atlas
from cifti_state.pipeline import build_adjacency
from cifti_state.results import AnalysisSpec

settings = load_settings("example_data/example.yaml")

# 1 · 读取——布局是检测出来的，不是假设的
stat_map = load_surface_stat_map(
    "example_data/maps/group_mean_thresh_fdr_E_D.dscalar.nii", statistic="z"
)
stat_map.describe()
# {'layout': '91k greyordinates (32k cortex without medial wall + subcortex)',
#  'n_vertices_left': 32492, 'n_present_left': 29696, 'medial_wall_excluded': True, …}
stat_map.left.values        # (32492,) 完整网格上的值，文件没覆盖的地方是 NaN
stat_map.left.present       # (32492,) bool —— 文件实际带了哪些顶点

# 2 · 阈值
values = stat_map.finite_values()
thr = compute_threshold(values, method="fixed", value=1.09, direction="positive")
# 或  method="fdr", q=0.05, statistic="z", df=None
# 或  method="percentile", percentile=95
thr.describe(), thr.positive, thr.n_suprathreshold
# ('fixed(+1.09)', 1.09, 11634)

# 3 · cluster
adjacency = build_adjacency(stat_map, settings, AnalysisSpec(mesh="32k"))
clusters = find_clusters(stat_map, adjacency, thr, extent=20, legacy_mode=True)
clusters.n_clusters, clusters.n_left, clusters.n_right     # (76, 38, 38)
clusters.labels_left        # (32492,) int，0 表示不属于任何 cluster
clusters.sizes()            # {1: 114, 2: 408, 3: 707, …}
clusters.info(3)            # ClusterInfo(cluster_id=3, hemisphere='left', …)

# 4 · 峰值与脑区
surfaces = load_hemisphere_surfaces(settings, "midthickness")
peaks   = cluster_peaks(stat_map, clusters, surfaces=surfaces)         # (76, 17)
atlas   = load_atlas("Glasser_2016", settings)
regions = annotate_clusters(clusters, atlas, peaks=peaks,
                            top_n=2, min_percent=5.0)                  # (147, 10)
report  = build_report(peaks, regions, style="wide")                   # (76, 21)

# 5 · 把选中的 cluster 写成 mask，布局与输入一致
left, right = clusters_mask(clusters, [3, 7, 12])      # label_values=True 保留编号
save_like("clusters_3_7_12.dscalar.nii", left, right, stat_map.template,
          map_name="clusters 3, 7, 12")
```

每一步都接受可选的 `progress=` 和 `cancel=` 回调——界面就是这样在 worker 线程上驱动它们的。

</details>

<details>
<summary><b>9 · Python 接口——出图与 3D</b></summary>

```python
from cifti_state.viz.render import render_stat_map, render_clusters, save_figure

fig = render_stat_map(
    stat_map, settings,
    clusters=clusters,
    mask_to_clusters=True,        # 只在存活的 cluster 内部显示统计值
    outline_clusters=True,        # 在上面描出 cluster 边界
    surface="inflated",
    layout="grid_4",              # grid_4 row_4 grid_8 dorsal_ventral
                                  # left_only right_only flat
    underlay="sulc",              # 灰度沟回底板
    colorbar_label="z",
)
save_figure(fig, "clusters.pdf", settings=settings, dpi=300)
```

`render_stat_map` 返回的就是一个普通的 matplotlib `Figure`——可以存盘，
也可以用 `FigureCanvasQTAgg` 把同一个对象嵌进 Qt 窗口。
界面里看到的和导出的来自同一条代码路径。

![带沟回底板的 grid_4 图](docs/images/figure_grid4.png)

想要能转的：

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

`build_scene` 返回的是与工具包无关的 `pyvista.PolyData` 网格加一份配色说明，
所以同一个场景既能交给脚本里的 `pyvista.Plotter`，也能交给界面里的 `pyvistaqt.QtInteractor`。

![交互 3D 视图](docs/images/view_3d.png)

</details>

<details>
<summary><b>10 · 调用 Connectome Workbench</b></summary>

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

参数以列表传入，**从不经过 shell**；每次调用都完整记入日志，
所以出错时你能看到出错的那条命令本身。

</details>

## 配置：一台机器一个文件

机器之间不一样的只有路径，所以机器文件里也就只写路径。

```
configs/
  settings.windows.yaml          平台模板：文件命名约定 + 分析默认值
  settings.linux.yaml            （两者都不含任何机器路径）
  machines/
    <hostname>.yaml              ← 你的，用下面的命令生成
    example-linux-server.yaml    ← 服务器模板，复制改名
example_data/example.yaml        ← 针对自带数据的、完全自包含的配置
```

```bash
cifti-state config --init-machine     # 生成 configs/machines/<hostname>.yaml
cifti-state config --show-chain       # 看哪个文件生效、为什么
cifti-state check --workbench --render
```

```yaml
# configs/machines/my-laptop.yaml
extends: ../settings.windows.yaml

workbench:
  wb_command: D:/workbench/bin_windows64/wb_command.exe
  wb_view:    D:/workbench/bin_windows64/wb_view.exe

resources:
  root:      D:/data/fs_LR_32-master      # 模板包
  atlas_dir: D:/data/fs_LR_32-master
  neighbors:
    "32k":
      left:  D:/data/lh.neighbors_IndexStart0.txt
      right: D:/data/rh.neighbors_IndexStart0.txt
```

**解析顺序**，先命中者胜：`--config` → `$CIFTI_STATE_CONFIG` →
`configs/machines/<hostname>.yaml` → 用户级配置 → `configs/settings.<platform>.yaml`。

几个值得知道的行为：

- 机器档案里**留空的值是继承**，不是清空。所以脚手架生成的半空文件可以直接用，
  不需要改的行删掉即可。
- 相对路径按**写下它的那个文件**所在目录解析，父子模板不会为工作目录打架。
- Windows 盘符路径（`D:/…`）在 Linux 上读也不会被悄悄改写——跨平台检查配置是安全的。
- 集群上各节点 hostname 不同但路径相同时，设 `CIFTI_STATE_MACHINE=shared`，
  档案命名为 `shared.yaml`。

## 输入格式

| 灰坐标数 | 是什么 | medial wall |
|---|---|---|
| **91282** | 皮层 29696 + 29716，外加 19 个皮层下结构 | 排除 |
| **59412** | 只有皮层，同样的顶点 | 排除 |
| **64984** | dense surface，32492 + 32492 | 包含 |

三者说的都是同一套左右 32k 皮层，处理是透明的：顶点映射来自每个文件自己的 brain model。
下游拿到的一律是完整网格数组加一个 `present` 掩码，所以顶点索引与曲面、图谱天然对齐，
不需要额外记账——输出也按输入自身的布局写回。

其他网格密度（10k、59k、164k）同理，只要图谱和邻接表对得上。
一对 `*.func.gii` 度量文件可以用 `load_surface_stat_map_from_gifti` 读入。

## 图谱

```bash
cifti-state atlases          # 你的模板包里有哪些
cifti-state atlases --all    # 连没登记进 registry 的也列出来
```

脑区名来自每个 `label.gii` 自带的 LabelTable，所以只要符合
`<name>.32k.{L,R}.label.gii` 命名约定的图谱**都能用**——Glasser 2016、AAL、Yeo 7/17、
Schaefer、Gordon、Power、Desikan、Destrieux、Fan、Shen、Baldassano、Wang、
各种 Icosahedron，以及模板包里的其余图谱。
配了 CSV 的，CSV 作为显示名覆盖叠在上面。

## 沟回底板

两个渲染器都会在统计图下面画出灰度的沟回形态，这样一个 blob 是落在脑回顶上还是沟底，
一眼就能看出来，而不是浮在一个没有特征的壳上。

```yaml
render:
  underlay: sulc            # sulc | curvature | none
  underlay_style: binary    # binary（Workbench 风格）| continuous
  underlay_orient: auto     # auto | negative | positive
  underlay_dark: 0.38
  underlay_light: 0.72
```

底板的哪个符号代表**沟**并不是常数——FreeSurfer 的 `?h.sulc` 正值在沟里，
而 fs_LR 的这个文件负值在沟里——搞反了整张图会里外颠倒，偏偏看上去还挺像回事。
所以这里是测出来的，不是猜的：从曲面网格算出凹凸度，再据此决定灰度方向。
万一自动判断错了，用 `underlay_orient` 钉死。

## 字体与编码

字体在 Python 里定死，不交给桌面环境：按一份明确的优先级列表去找，
然后把同一个答案同时给 Qt 和 matplotlib，这样窗口和图不会各写各的。
`axes.unicode_minus` 关掉了（matplotlib 默认的 U+2212 在很多中文字体里缺字形，
会画成方框——负数显示不对多半就是这个原因），stdout 强制 UTF-8，
免得中文路径在 GBK 的 Windows 控制台上抛 `UnicodeEncodeError`。

字号用**像素**、只在一处定义，样式表读同一个数，
所以桌面的 DPI 缩放不可能只放大文字而不放大它所在的框。
数字输入框另外用等宽字体和 **C locale**，区域设置就不可能把本地数字或逗号小数点塞进阈值里。

**如果数字显示成了别的字符**——`0.1160` 变成 `O.ıı ϗ OO`——
那是字符串没错而字体错了：某些字体（Qt DirectWrite 后端下的 variable font、
损坏的子集字体）会把字符映到不属于它的字形上。这类字体族现在会被自动跳过，另外：

```bash
cifti-state check --fonts                      # 看解析结果
cifti-state check --fonts --sample spec.png    # 同一串数字在每个候选字体族里各画一行
```

想一劳永逸，就在机器档案里指定：

```yaml
interface:
  ui_font: Microsoft YaHei UI    # 中英兼顾
  mono_font: Consolas
  font_size_px: 13
  numeric_font: mono             # mono | ui
```

把 `.ttf`/`.otf` 丢进 `resources/fonts/`，结果就完全不依赖机器了——
自带字体会注册进两个工具包，并且优先级高于任何已安装的字体。

## 3D 视图是空白的怎么办

```bash
python examples/check_3d.py              # 或加 --offscreen，改成存 PNG
```

它自底向上逐层测试——imports、OpenGL 上下文、裸的 PyVista 窗口、你自己的皮层，
最后是界面用的 Qt 内嵌控件——并停在第一个失败的地方，
所以你知道是*哪一层*坏了，而不用猜。最常见的答案是 VTK 拿不到 OpenGL 上下文：
远程桌面一般给不了，显卡驱动太老也可能给不了。Figure 页签不需要它。

## MATLAB 对拍

`tests/test_regression.py` 与 `example_data/maps/` 里的参考 cluster 图逐顶点对拍：

| 数据 | 阈值 | extent | MATLAB | cifti_state | 不同的顶点数 |
|---|---|---|---|---|---|
| `group_mean_thresh_fdr_E_C` | 1.039 | 20 | 32 (L 15 / R 17) | 32 (L 15 / R 17) | **0** |
| `group_mean_thresh_fdr_E_D` | 1.09 | 20 | 76 (L 38 / R 38) | 76 (L 38 / R 38) | **0** |

包括 cluster 编号——编号按（半球，符号，最小顶点索引）确定，是确定性的。

### 与 MATLAB 版本的差异

是修正，不是重构。前四条会改变结果：

1. **负向 cluster 现在真的能找到。** `get_clusters_fsLR32k.m` 先用
   `map .* (map > threshold)` 掩蔽，再用 `threshold = 0` 去搜，
   于是所有低于阈值的顶点都变成了正好 0，负向 cluster 永远不可能存活。
   `direction` 可选 `positive` / `negative` / `two_sided`。
2. **无穷值的处理改成对称的。** `map(isinf(map)) = max(...)` 把 `-inf` 变成了一个很大的
   *正* 值，凭空造出 cluster。`inf_policy="clip"` 把 `+inf` 送到最大有限值，
   `-inf` 送到最小有限值。
3. **AAL 右半球的标签是错的。** `w_find_brain_region.m` 两个半球都加载了
   `data_aal_L.label.gii`。
4. **cluster 之间的状态会串。** `label_index_name` 从不清空，
   于是只匹配到一个脑区的 cluster 会继承上一个 cluster 的第二个脑区。
5. **脑区名来自每个 `label.gii` 自己的 LabelTable**，所以模板包里的图谱全都能用，
   而不只是配了 CSV 的那些——写死的 `+1` / `+36` / `+max(L)` 半球偏移也没有了。
6. **`zstat` 原本是 cluster 均值而不是峰值。** 现在两者都报，名字如实，
   另加 SD、min/max、以 mm² 计的面积，以及峰值和质心坐标。
7. **统计量类型必须声明。** `1 - normcdf` 把输入当作 z 分数，
   而 `surfstate.m` 喂给它的是 `slm.t`。现在 t 图必须给 `df`。
8. **cluster 是线性时间的。** 原来把邻居入队时不检查是否已经在队里；
   现在连通域由 `scipy.sparse.csgraph` 给出。
9. **只匹配到一个脑区的 cluster 不再靠复制自己那一行来凑数。**

对于有限数值的纯正向分析，新旧结果完全一致。数据里有负值或无穷值、
或者用了 AAL 的地方会不一致——那是在修 bug，不是回归。

`lh/rh.neighbors_IndexStart0.txt` 仍然是默认的邻接来源。
它们与网格拓扑对拍过，逐顶点一致（32480 个顶点 6 邻居，12 个顶点 5 邻居），
所以 `neighbor_source: surface` 是精确的等价替换，且在任何网格密度下都成立——
自带例子用的就是它，因此不必随包分发那 2.6 MB 的表。

## 目录结构

```
cifti_state/
  config.py     Settings 数据类、平台检测、路径解析
  wb.py         wb_command / wb_view 封装，每次调用完整记入日志
  io/           cifti · surface · neighbors · atlas
  core/         threshold · cluster · peaks · annotate · report · mask
  fonts.py      字体解析与 UTF-8，Qt 和 matplotlib 共用
  viz/          render (surfplot) · interactive (PyVista) · underlay (沟回)
                · colormaps · layouts
  gui/          theme · widgets · state · workers · panels/ · main_window
  pipeline.py   CLI 和界面共用的编排层
  results.py    AnalysisSpec / AnalysisResult，都可序列化
  cli.py
```

`core/` 按六条规矩写，好让 Qt 前端能直接调用：对数组做纯函数运算、
不打印（只用 `logging`）、慢的东西带 `progress` 回调、长的东西带 `cancel` 令牌、
配置作为参数传入而不是读全局、每个结果都携带产生它的那份参数快照——
`AnalysisResult.to_json()` 足以复现一次运行。

## 测试

```bash
pytest          # 112 项；裸 clone 下会跳过 3 项（它们需要邻接表和 Desikan 图谱，
                # 这两样没有随包分发）
```

不需要任何准备：依赖数据的测试——**包括 MATLAB 对拍**——默认就跑在 `example_data/` 上。
要改用你自己的数据：

```bash
export CIFTI_STATE_TEST_CONFIG=configs/machines/<hostname>.yaml
export CIFTI_STATE_TEST_EXAMPLES=/path/to/Example_test
pytest
```

覆盖的内容：在答案可以手算的合成图上做连通域；BH-FDR 对照教科书定义；
三种 CIFTI 布局及其往返；`extends`/机器档案的解析，含空值继承和在 Linux 上读 Windows 盘符；
用桩程序验证 `wb_command` 封装；标注与报表组装；cluster mask；
底板的灰度与符号判定；字体解析和数字框的几道防线；
界面的 headless 测试——包括「worker 回调必须落在 GUI 线程」这一条断言，
所有控件更新都依赖它；以及上面那张 MATLAB 对拍表。

## 还没做的

- **RFT cluster 校正。** 公式不是障碍——已经用模拟验证过，
  在场的平滑度已知时 FWE 正好是 0.05。障碍在于 SurfStat 的平滑度取自 GLM 残差，
  而改从统计图本身估会系统性偏低（模拟中偏低 6–17%），把 FWE 推到 0.06–0.18。
  要做就得有残差，或者由用户提供 FWHM。
- 为 wb_view 生成 `.spec` / `.scene` 文件；目前是直接传一串文件名调起来的。
- 局部次峰（`find_local_peaks`）已实现，但还没接进界面和 pipeline。
- 批处理没有界面入口，用 `cifti-state batch`。

## 示例数据

`example_data/` 里有两张统计图、MATLAB 的参考输出，
以及跑通上面一切所需的最小 fs_LR 32k 模板文件，总共约 8 MB。
每个文件是什么、第三方模板出自哪里，见
[`example_data/README.md`](example_data/README.md)。

## 致谢

`example_data/fs_LR_32k/` 里的 fs_LR 32k 模板来自
[DiedrichsenLab/fs_LR_32](https://github.com/DiedrichsenLab/fs_LR_32)。
曲面来自 HCP 组平均（Van Essen et al., *Cerebral Cortex* 2012），
分区来自 Glasser et al., *Nature* 2016——使用时请引用这些工作。
出图用的是 [surfplot](https://github.com/danjgale/surfplot) 和
[BrainSpace](https://github.com/MICA-MNI/BrainSpace)；
交互视图用 [PyVista](https://pyvista.org/)；
CIFTI / GIFTI 读写用 [NiBabel](https://nipy.org/nibabel/)。

## 许可

MIT，见 [LICENSE](LICENSE)。`example_data/fs_LR_32k/` 里的文件属于第三方，
适用它们自己的条款，见 [`example_data/README.md`](example_data/README.md)。
