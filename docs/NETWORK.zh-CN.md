# 脑网络出图

`cifti_state.network` 的完整参考——玻璃脑上的节点/连边渲染模块。README 里是导览，
这里回答「这个选项到底做了什么」。

这个模块由 Claude Code 编写，目的是让你在 **NeuroMArVL** 里交互调好的一张图，能够用
脚本原样复现出来——同一套输入文件、同一条选边规则、同一个 settings JSON——然后批量出图、
纳入版本管理、数据更新后重跑。

> **凡是用这个模块出的图，请引用 NeuroMArVL：**
> Adamson CL, Gajwani M, Klapperstueck M, Manley J, Dwyer T, Fornito A.
> *NeuroMArVL: An interactive and collaborative web-based tool for visualizing
> brain networks.* Network Neuroscience 2026; 10(3):683–705.
> doi:[10.1162/netn.a.569](https://doi.org/10.1162/netn.a.569)
>
> 工具：<https://immersive.erc.monash.edu/neuromarvl/> ·
> 源码：<https://github.com/NSBLab/NeuroMArVL>（GPL v3）

这里是**独立实现**，不是移植：没有使用也没有包含 NeuroMArVL 的任何代码，本工具包是
MIT 协议。它刻意复现的是那些「两边必须一致、否则画不出同一张图」的行为——四文件输入
格式，以及 edge count 规则（对着那个工具自己公布的 `k → 箭头数` 表逐一核对过）。
它只覆盖 3D 玻璃脑那一部分，也不打算取代那个交互工具：探索一个网络、把一张图分享给
合作者，正是那个工具的用途，而它的 2D、环形和 topology 布局在这里没有对应物。

**目录**

- [四个文件](#四个文件)
- [画哪些边](#画哪些边)
- [长什么样](#长什么样)
- [复用 NeuroMArVL 的 settings 文件](#复用-neuromarvl-的-settings-文件)
- [玻璃脑](#玻璃脑)
- [视角、分栏与输出](#视角分栏与输出)
- [用肉眼调](#用肉眼调)
- [命令行](#命令行)
- [从 Python 调用](#从-python-调用)
- [可能出的问题](#可能出的问题)

---

## 四个文件

一个数据集是四个纯文本文件，只有前两个是必需的。

| 文件 | 内容 |
|---|---|
| `coordinates.txt` | `n` 行 `x y z`，MNI 毫米，必须与脑表面同一空间 |
| `matrix_<名字>.txt` | `n × n` 矩阵；**`matrix[i][j]` 是 `i → j`** |
| `labels.txt` | `n` 个节点名，一行一个；名字里可以有空格 |
| `attributes_<名字>.txt` | `n` 行数值列，首行是列名 |

空格、Tab、逗号都能作分隔符；表头是**自动识别的，不用声明**，所以带 `x y z` 首行的
坐标文件和不带的读出来一样。带行名列名的矩阵两者都会被去掉。没有列名的列会拿到
`col1`、`col2`……这样在命令行上每一列都叫得出名字。

```python
from cifti_state.network import load_network_dir, load_network

data = load_network_dir("my_network/")                    # 自动找四个文件
data = load_network_dir("my_network/", dataset="listen")  # matrix_listen.txt
data = load_network_dir("my_network/", absolute=True)     # 那一对 *_abs.txt
data = load_network("coords.txt", "M.txt", labels="L.txt", attributes="A.txt")
```

`load_network_dir` 默认忽略 `*_abs.txt`，所以一个目录里同时放着有符号矩阵和它的
`|权重|` 版本仍然不会有歧义。如果目录里有好几个**确实不同**的矩阵，它不会替你猜，
会让你指定用哪个。

**矩阵永远不会被对称化。** 在有效连接、Granger 因果和任何带时滞的指标里，
`matrix[i][j] != matrix[j][i]` 就是信号本身，把它平均掉是这个模块唯一不会背着你做的
变换。`data.is_directed` 和 `data.asymmetry` 会告诉你它有多「有向」——`asymmetry`
是 `Σ|M − Mᵀ| / Σ|M|`，对称矩阵为 0。

文件之间任何对不上的地方都会被点名拒绝，而不是悄悄放过：

```
NetworkError: 9 coordinates but 12 labels
NetworkError: 9 coordinates but the matrix is 12 x 12; the two files
              describe different networks
```

### 度值

如果你的属性表里还没有：

```python
data.in_degree()                  # |M| 的列和，不含对角
data.out_degree()                 # 行和
data.in_degree(absolute=False)    # 有符号，正负会抵消
```

两者会不一样，有时差得很多，**一张图用的是哪一种应该写进图注**。对 `|权重|` 求和的
口径会把一个强抑制性输入很多的节点判成 hub；有符号求和则根本不会。

---

## 画哪些边

9 个节点的有向矩阵有 72 个非对角元素，把 72 条箭头都画上去等于什么都没说。所以每一张
网络图都是阈值化过的，而**阈值是什么含义，本身就是结果的一部分**。三条规则，每一条都
会把自己写进自动生成的图注里。

### `--top N`——最强的 N 条有向边

当一张图想说的就是「这是最强的几条连接」时用它，因为它画的正是这个。

```bash
cifti-state network net/ --top 20 -o fig.png
cifti-state network net/ --top 20 --rank-abs -o fig.png   # 按 |权重| 排
```

`--rank-abs` 按绝对值排序，于是一条强的负连接可以和强的正连接竞争。无论哪种方式
权重都保留原来的符号，所以 `--edge-color sign` 仍然能把它们分开上色。

### `--threshold V`——权重 ≥ `V` 的全画

朴素、无趣。当那个数字本身有意义时就该用它：一个显著性阈值、一个预注册的值、
论文里写死的那个数。

### `-k` / `--edge-count K`——NeuroMArVL 的滑块

**精确复现**，因为在这里出的图和在浏览器里出的图必须是同一张图。规则是：

1. 每一对节点取**两个方向里较大的那个**——是较大的**有符号**值，所以一对里
   `+0.1` 和 `−0.4` 的，排的是 `+0.1`；
2. 把这 `n(n−1)/2` 个值从大到小排序；
3. 第 `K` 个就是阈值；
4. 然后把**所有**达到这个阈值的有向边都画出来。

因为阈值来自「对」、却作用在「方向」上，**`K` 不是箭头条数**——通常箭头更多。在一张
真实的 9 节点矩阵上，`k=16` 画出 24 条箭头。`--explain-k` 会把你这张矩阵的整张表打
出来然后停下，不画任何东西：

```bash
cifti-state network net/ --explain-k
```

```
   k       cutoff   arrows
   1     0.193582        1
  ...
  16     0.045925       24
  ...
  30     0.001554       51
  31    -0.001691       52  <- cutoff is negative

largest k with a positive cutoff: 30
```

最后一行很重要。越过它之后阈值变成负数，**负权重就会被当作普通边画出来，而且图上没有
任何东西标明它是负的**——一条抑制性连接和一条兴奋性连接长得一模一样。越过时渲染器会
记一条警告，自动生成的图注也会在图上写明。两种诚实的越过方式：

```bash
cifti-state network net/ -k 34 --drop-negative     # 干脆不画
cifti-state network net/ -k 34 --edge-color sign   # 画，但换个颜色
```

### 怎么验证的

这条规则不是近似的。`tests/test_network.py` 里存着 NeuroMArVL 自己公布的、五个有效
连接数据集的 `k → 箭头数` 对照表——`k ∈ {8, 12, 16, 20, 24, 28, 35}` 共 29 个数字，
外加每个数据集「阈值仍为正的最大 k」——并且全部逐一核对。把 `CIFTI_STATE_NETWORK_DATA`
指向放着那些矩阵的目录，这个测试就会跑；没有它就跳过，而手算的四节点例子仍然把语义
钉死。

---

## 长什么样

### 节点

| 选项 | 作用 |
|---|---|
| `--size-by 列名` | 用某一属性列决定球半径 |
| `--size-range MIN MAX` | 半径，单位**毫米**（默认 `1.4 6.3`） |
| `--color-by 列名` | 用某一属性列决定球颜色 |
| `--colors HEX …` | 离散属性的配色，按取值从小到大 |
| `--labels-on` / `--no-labels` | 球旁边是否写节点名 |

一个属性如果**不同取值不超过 20 个**，就被当作分组变量，按取值从小到大套离散配色；
超过就被当作测量值，套色标。这个 20 的界限是 NeuroMArVL 的。想强行指定，在 Python 里
用 `NodeStyle(color_mode="discrete")` 或 `"continuous"`。

大小映射会把观测到的范围铺满整个 size range——浏览器就是这么做的，好处是小差异也看得
见。代价是它同样会放大小差异：一列从 0.95 到 1.00 的属性照样会画出一个最小球和一个
最大球。`NodeStyle(size_from_zero=True)` 改成半径与数值成正比，数值翻倍半径才翻倍。

### 连边

`--edge-color` 决定颜色在说什么：

| 模式 | |
|---|---|
| `none` | 所有边一个颜色（默认） |
| `weight` | 按 `\|权重\|` 上色标，不区分正负 |
| `signed` | 正边深红、负边深紫，各自内部再用深浅表示强度 |
| `node` | **源**节点的颜色——于是颜色本身就带了方向 |
| `node-transitioning` | 沿着边从源节点颜色渐变到目标节点颜色 |
| `sign` | 按符号两个平色块，不带强度信息 |

权重有正负、同时又想表达强度时，用 `signed`。两端由
`--edge-colors 负 正` 指定（默认 `#3f2071` 和 `#8e1616`），
`--signed-fade` 控制每一侧最弱的边往白色靠多少——设成 `0` 则每条边都是十足的深色，
强度完全交给粗细表达。

> 它**故意不是**那种经过中性色的平滑发散色标。把发散色标按零点对称地套在一组
> 从 0.05 到 0.19 的权重上，最弱的那条正边只走到偏红方向的四分之一处——
> 结果它离**负**色比离正色还近，和它的含义正好相反。所以这里是按符号各走一条梯度：
> 正边永远是红的，负边永远是紫的。

`--direction` 决定方向怎么表示：

| 模式 | |
|---|---|
| `arrow` | 目标端一个锥形箭头（默认） |
| `gradient` | 从 `start_color` 到 `end_color` 的颜色渐变 |
| `taper` | 源端细、目标端粗 |
| `opacity` | 向目标端逐渐变实 |
| `none` | 一根素管子 |

`gradient` 会覆盖 `--edge-color`，因为一根管子同时按权重和按方向变色，两件事都说不
清楚。`opacity` 会让管子变成半透明，在软件 OpenGL 上合成不可靠——`arrow` 和 `taper`
不会，选它的时候模块会提示一句。

粗细：`--edge-width MM` 固定管半径（默认 0.55 mm），或 `--width-range MIN MAX`
让它随 `|权重|` 在这两个毫米值之间变化（只给 `--width-by-weight` 用默认的
0.25–1.4 mm）。

当粗细跟着权重走时，箭头是按 width range 的**中点**来定大小的，只有粗边的箭头才会
继续长大。箭头的职责是表示方向，强度已经由管子的粗细承担了；按各自管子的粗细来定，
最弱那几条边的箭头会小到根本看不出方向。

默认边色是深色（`#39474f`）而不是中灰，因为边是**透过**半透明脑壳看的，脑壳会把它
提亮：一根单看正合适的管子，脑壳一挡就没了。

---

## 复用 NeuroMArVL 的 settings 文件

从浏览器里存下来的 settings JSON 可以直接加载：

```bash
cifti-state network net/ --settings settings/size_in_degree.json -k 16 -o fig.png
```

```python
from cifti_state.network import load_marvl_settings
style = load_marvl_settings("settings/size_in_degree.json")
```

**能带过来的**：节点大小属性与范围、颜色属性与它的离散配色、连续色标的两端、边的方向
模式与颜色模式、边宽以及是否随权重变化、脑表面颜色与透明度、画哪几个半球、是否显示
标签、视角，以及第一个 saveApp 里的 edge count。任何与 `--settings` 同时给出的命令行
选项都会覆盖对应那一项。

**带不过来的**（静态图没有对应物）：2D 布局（`layout2d`、`bundle2d`、`scale2d`）、
topology network、`rotation`，以及 `directionMode: "animation"`——它会退回成箭头。
每一项都会打一条日志点自己的名，不会有东西悄悄消失。

**大小是换算过的，不是照抄。** 浏览器的 size range 用的是它自己渲染器的单位，整个脑
大约 2 个单位宽；这里的场景是毫米。`MARVL_SIZE_TO_MM = 3.5` 把它默认的 `0.4–1.8`
映射成 1.4–6.3 mm 半径，比例不变。想改就传 `size_to_mm=`。

`save_marvl_settings(style, "style.json")` 把这个格式写回去，命令行上是
`--save-settings`。往返一圈，这个格式能表达的东西都保留。

> 有一个细节值得单独点出来，因为它做出来的图看着像是故意的、其实不是：
> `edgeColorByNodeTransitionColor` 是**渐变**的颜色，只在 `edgeColorByNodeTransition`
> 打开时才生效。无条件读它，会把每一条本该是灰色的边刷成亮红。

---

## 玻璃脑

脑壳是真的皮层表面——配置在当前网格下提供的那一张，默认 `midthickness`。
`--surface inflated` 换成充气面，但那是对「节点在哪」的另一种主张，应该是个有意的
选择：节点坐标是解剖坐标，而充气面不是解剖面。

```bash
cifti-state network net/ --brain-opacity 0.35 --hemisphere left -o fig.png
cifti-state network net/ --split 36 --views dorsal -o fig.png
```

| 选项 | |
|---|---|
| `--brain-opacity` | 0 完全不画脑壳，1 完全不透明（默认 0.55） |
| `--hemisphere left \| right \| both` | 画哪几个半球 |
| `--split MM` | 把两个半球拉开，方便看内侧面上的网络 |
| `--surface 名字` | 用哪张解剖面 |

### 让这件事成立的两个细节，万一你要读代码

**绘制顺序。** 半透明表面必须和它背后的东西合成，而 VTK 只有在「背后的东西已经画完」
时才做得对。所以所有不透明的 actor——先边、再节点——都先加进去，脑壳最后加。深度剥离
（depth peeling）本可以免掉这个约束，但它在软件 OpenGL 上要么没有要么是坏的，而批量
出图恰恰就在那种环境里跑，所以场景是按「不需要它」来搭的。（这和 NeuroMArVL 自己的
`renderOrder = RENDER_ORDER_EDGE` 是同一回事。）

**背面剔除。** 皮层是折叠的，一条视线要穿过十来层脑沟壁；在任何诚实的单面透明度下，
它们叠起来之后「玻璃」脑就变成不透明的了。只画朝向镜头的三角形，就只剩一层玻璃，
你要的透明度就是你看到的透明度。`BrainStyle(cull_backfaces=False)` 可以关掉。

---

## 视角、分栏与输出

```bash
cifti-state network net/ --views left right anterior posterior dorsal ventral \
    --columns 3 -o fig.png
```

六个视角：`left`、`right`、`anterior`、`posterior`、`dorsal`、`ventral`。
NeuroMArVL 的叫法（`Top`、`Bottom`、`Front`、`Back`）和常见别名（`lateral`、
`superior`、`lh`）也都认。

**每一栏画的都是同一个网络、只换相机**，因为选边只做一次、各栏共用——各栏之间不可能
对「有哪些连接」产生分歧。

各栏是逐个离屏渲染的，然后裁到内容、补边到统一尺寸，再用 matplotlib 拼版。「裁到内容
再补边」保证了每一栏的**像素比例一致**——一个节点在一个视角里是 20 px，在别的视角里
也是 20 px——同时把相机自动取景一定会留下的空白边扔掉。让每一栏各自填满自己的画框会更
好看，但那样会悄悄地在视角之间改变脑的大小。Python 里传 `crop=False` 可以关掉。

| 选项 | |
|---|---|
| `--size W H` | 裁剪前每栏的像素尺寸（默认 `1000 850`） |
| `--dpi` | 图像分辨率（默认 200） |
| `--zoom` | 相机缩放，默认取景太松或太紧时用 |
| `--columns N` | 每行几栏 |
| `--title` | 图标题 |
| `--no-caption` | 不要那行自动生成的图注 |
| `--vector` | 走 gl2ps 输出真正的矢量 |

### 那行图注

是生成的而不是手写的，因为阈值规则恰恰是读者光看图**看不出来**的那一部分：

```
9 nodes; 24 edges; NeuroMArVL edge count k=16; cutoff 0.045925;
directed (row -> column); node size: out_degree; node colour: group_id.
```

需要的时候它会多长出一句——`7 of the drawn edges are negative and are not marked as
such`——而当 `--edge-color sign` 让这句话不再成立时，它会自己去掉。要投稿、图注另写的
话用 `--no-caption`。

### PNG、PDF、SVG

默认各栏按 `--dpi` 栅格化、由 matplotlib 排版，所以 `.pdf` 和 `.svg` 能写出来，但里面
的脑是一张位图。`--vector` 改走 PyVista 的 gl2ps 路线，写出真正的矢量几何——只能一个
视角，而且明暗会被压平。选哪个，取决于是期刊要矢量、还是这张图要好看。

---

## 用肉眼调

一张图的那几个数——画多少条边、脑壳多透、管子多粗、标签是帮忙还是添乱——是看出来的，
不是想出来的。`--interactive` 会打开一个控制窗口：左边是所有选项的控件，右边是可以
拖着转的脑。

![网络控制窗口](images/network_window.png)

```bash
cifti-state network taskaverage/ --settings settings/size_out_degree.json \
    --edge-color signed --width-range 0.3 1.6 --labels-on --interactive
```

| 分组 | |
|---|---|
| **Which edges** | 选边规则（`edge count k` / `weight threshold` / `strongest N`）及其数值、`rank by \|weight\|`、`drop negative edges`，以及「当前画出多少条箭头、阈值是多少」的实时读数 |
| **Nodes** | 大小属性与半径范围、颜色属性、**该属性每一个取值一个取色器**、标签开关和标签字号 |
| **Edges** | 颜色模式、− / + 两个取色按钮、方向模式、固定粗细或随权重变化及其范围、箭头长度 |
| **Glass brain** | 表面、半球、透明度、左右分离，以及相机视角 |

有两处行为是刻意这么设计的，不是顺手写成这样：

**刷新是一个按钮。** 重建一个密集网络要一两秒，而一个「改什么就立刻重画」的面板，在你
连着设四个数的时候，大部分时间都在画你并不想看的中间状态。所以改动会累积起来，按钮上
长出一个 `•` 提示有待应用的改动，点一下全部生效。`Auto` 可以切回「改什么就重画」，
适合只微调一个值的时候。相机和视角下拉框不受此限——它们很便宜，为了看另一侧还要按一下
刷新就太荒唐了。

**命令行始终可见。** 视图下方那一栏显示的就是复现当前画面的那行 `cifti-state network`
命令，随改随更新：

```
cifti-state network taskaverage/ -k 19 --views left --size-by out_degree \
    --color-by group_id --colors '#ff69b4' '#6AB4E3' '#4caf50' \
    --size-range 1.40 6.30 --labels-on --edge-color signed \
    --edge-colors '#3f2071' '#8e1616' --width-range 0.30 1.60 \
    --brain-opacity 0.45 -o figure.png
```

`Copy` 把它放进剪贴板，`Save figure…` 直接写文件。窗口里的任何设置都**不会**被隐式
保存——那行命令就是全部交接内容，也正是它让论文里的一张图可以追溯到一段能读、能重跑、
能写进方法部分的文字，而不是某次开着的一个窗口。有一条测试会把这行命令再塞回 CLI
解析一遍，确认含义没变。

`k` 越过「阈值仍为正的最大值」的那一刻，读数会变红，于是「负权重开始被当普通边画出来」
这件事你当场就看得见，而不是事后在图注里才发现。

这需要真实窗口（不能离屏跑），也需要 `gui` 这一组可选依赖：

```bash
pip install -e ".[gui]"     # PySide6 和 pyvistaqt
```

没装的话，`--interactive` 会退回到一个朴素的 PyVista 窗口，三个滑块（边数、脑壳透明度、
边宽）画在场景里面。那条路依赖 VTK 自带的 widget，并不是在哪都能用；**推荐用控制窗口**。

`cifti-state-gui` 是**统计图**那条线的桌面界面，里面没有网络出图的面板。

---

## 命令行

```
cifti-state network 输入 -o 图片 [选项]
```

`输入` 可以是放着那四个文件的目录，也可以是矩阵文件本身（这时会自动找它旁边的
`coordinates.txt`，或者用 `--coordinates` 指定）。

```bash
# 最常见的情况
cifti-state network taskaverage/ -k 16 --size-by in_degree --color-by group_id \
    --labels-on -o in_degree.png

# 复现浏览器里存下的那一套，但换个视角
cifti-state network taskaverage/ --settings settings/size_out_degree.json \
    -k 16 --views left dorsal -o out_degree.png

# 这张矩阵上 k 到底是什么意思
cifti-state network taskaverage/ --explain-k

# 目录里有好几个数据集，指定一个
cifti-state network Neuromarvl_input/ --dataset listen --top 20 -o listen.png
```

完整选项：`cifti-state network --help`。

---

## 从 Python 调用

```python
from cifti_state.config import load_settings
from cifti_state.network import (
    load_network_dir, load_marvl_settings, render_network,
    NetworkStyle, NodeStyle, EdgeStyle, BrainStyle, select_edges,
)

settings = load_settings()
data = load_network_dir("taskaverage/")

style = NetworkStyle(
    node=NodeStyle(size_by="out_degree", color_by="group_id",
                   size_range=(1.6, 6.0), labels=True),
    edge=EdgeStyle(color_mode="node-transitioning", direction_mode="arrow"),
    brain=BrainStyle(opacity=0.5, hemispheres=("left", "right")),
)

figure = render_network(
    data, settings, style=style, out="network.png",
    views=["left", "dorsal"], edge_count=16,
    title="跨任务平均的有效连接",
)
print(figure.caption, len(figure.edges))
```

`render_network` 返回一个 `NetworkFigure`：写出去的路径、matplotlib 的 `Figure`、
各栏的数组、图注，以及实际画出来的那个 `EdgeSet`。

当那三条规则覆盖不了你要的东西时——只保留进入某个节点的边、每个节点只留最强的一条、
套一个显著性 mask——自己选边然后传进去：

```python
edges = select_edges(data.matrix, top=40, absolute=True)
keep = np.isin(edges.target, [0, 3])          # 只要指向 IFJ 和 B55post 的边
edges = EdgeSet(edges.source[keep], edges.target[keep], edges.weight[keep],
                rule="strongest inputs to IFJ and B55post")
scene = build_network_scene(data, settings, style, edges=edges)
```

### 批量

```python
from cifti_state.network import load_network_dir, render_network_batch

datasets = [load_network_dir("Neuromarvl_input/", dataset=task)
            for task in ("listen", "naming", "reading", "writing")]
render_network_batch(datasets, settings, "figures/", style=style,
                     edge_count=16, views=["left"])
```

批量的意义在于可比，所以凡是可能因为疏忽而各图不同的东西都被固定住了——一套样式、
一组视角、一条选边规则——只有矩阵和它们的属性表在变。

---

## 可能出的问题

### 节点坐标不在同一个空间

到目前为止最常见的错误，而且它做出来的图**看起来完全正常**。坐标是体素索引、是另一个
模板、或者左右翻了，都会给你一堆位置看着像模像样、解剖学上完全错误的球。

每次运行都会检查，对不上就说出来：

```
warning: 9 of 9 nodes fall outside the surface's bounding box (worst by
190.4 mm): IFJ, SFL, B55pre, B55post, Aud_primary, VentralMotor. The
coordinates are probably not in the same space as the mesh.
```

Python 里 `check_coverage(data, settings)` 会返回脑表面的包围盒、有几个节点在里面、
最远的那个在外面多少毫米、以及是哪几个节点。包围盒检验是弱检验——一个落在脑室正中的
节点照样通过——但它能抓住所有「坐标系搞错了」的情况，而现实中出问题的就是这一类。

### 脑壳画出来是不透明的

背面剔除被关掉了，或者那张表面不是闭合的。检查 `BrainStyle(cull_backfaces=True)`，
并把 `--brain-opacity` 调低。

### 图是空白的

没有离屏 OpenGL 上下文。和这个工具包其他部分是同一个问题，见 README 的
[「3D 视图是空白的怎么办」](../README.zh-CN.md#3d-视图是空白的怎么办)。
无头 Linux 上在命令前加 `xvfb-run -a`。

### 箭头不见了

`--direction gradient`、`taper`、`opacity` 和 `none` 都会取代箭头：一次只用一种方向
线索，浏览器里也是这个行为。`--direction arrow` 就回来了。

### 所有节点一样大

`--size-by` 指的那一列是常数，或者根本没指。常数列会让每个节点都拿到 size range 的
中点，而不是去除以零。

### 加载 settings 文件之后所有边都变成亮红色

已经修好了，见[复用 NeuroMArVL 的 settings 文件](#复用-neuromarvl-的-settings-文件)
里那条注记。如果你还看得到这个现象，那是别的程序在读那个文件。

---

## 手上没有数据也能试

```bash
python examples/network_example.py
```

会在 `results/network_example/` 下写出六张图，覆盖各种方向模式、颜色模式、分离半球
视图和六视角排版。节点是直接从随包的 fs_LR 32k 表面上取的，所以坐标是网格自己空间里
真实的皮层位置；矩阵是用固定随机种子生成的。
