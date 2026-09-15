# Network figures

Full reference for `cifti_state.network` — the glass-brain node-and-edge
renderer. The tour is in the README; this is the part that answers "what does
this option actually do".

This module was written by Claude Code so that a figure tuned interactively in
**NeuroMArVL** can be reproduced from a script — the same input files, the same
edge-selection rule, the same settings JSON — and then made in batch, put under
version control, and re-made when the data changes.

> **Cite NeuroMArVL for any figure made with this module:**
> Adamson CL, Gajwani M, Klapperstueck M, Manley J, Dwyer T, Fornito A.
> *NeuroMArVL: An interactive and collaborative web-based tool for visualizing
> brain networks.* Network Neuroscience 2026; 10(3):683–705.
> doi:[10.1162/netn.a.569](https://doi.org/10.1162/netn.a.569)
>
> Tool: <https://immersive.erc.monash.edu/neuromarvl/> ·
> Source: <https://github.com/NSBLab/NeuroMArVL> (GPL v3)

This is an **independent implementation**, not a port: no NeuroMArVL code is
used or included, and this package is MIT-licensed. What it reproduces
deliberately is the behaviour the two tools must share to draw the same figure —
the four-file input format and the edge-count rule, checked against that tool's
own published `k → arrows` table. It covers only the 3D glass-brain view, and it
does not replace the interactive tool: exploring a network, and sharing one with
a collaborator, are what that tool is for, and its 2D, circular and topology
layouts have no equivalent here.

**Contents**

- [The four files](#the-four-files)
- [Which edges get drawn](#which-edges-get-drawn)
- [How it looks](#how-it-looks)
- [Reusing a NeuroMArVL settings file](#reusing-a-neuromarvl-settings-file)
- [The glass brain](#the-glass-brain)
- [Views, panels and output](#views-panels-and-output)
- [Tuning it by eye](#tuning-it-by-eye)
- [Command line](#command-line)
- [From Python](#from-python)
- [What can go wrong](#what-can-go-wrong)

---

## The four files

A dataset is four plain-text files. Only the first two are required.

| File | Contents |
|---|---|
| `coordinates.txt` | `n` rows of `x y z`, MNI millimetres, in the same space as the surfaces |
| `matrix_<name>.txt` | an `n × n` matrix; **`matrix[i][j]` is `i → j`** |
| `labels.txt` | `n` node names, one per line; a name may contain spaces |
| `attributes_<name>.txt` | `n` rows of numeric columns, with a header row of names |

Spaces, tabs and commas all separate columns; a header row is **detected, not
declared**, so a coordinates file with an `x y z` line and one without both
read the same. A matrix written with row and column names loses both. Columns
with no header get `col1`, `col2`, … so that every column can still be named on
the command line.

```python
from cifti_state.network import load_network_dir, load_network

data = load_network_dir("my_network/")                    # finds the four files
data = load_network_dir("my_network/", dataset="listen")  # matrix_listen.txt
data = load_network_dir("my_network/", absolute=True)     # the *_abs.txt pair
data = load_network("coords.txt", "M.txt", labels="L.txt", attributes="A.txt")
```

`load_network_dir` ignores `*_abs.txt` unless you ask for it, so a directory
holding both the signed matrix and its `|weight|` twin is still unambiguous.
With several genuinely different matrices present it refuses to guess and tells
you to name one.

**The matrix is never symmetrised.** `matrix[i][j] != matrix[j][i]` is the
signal in effective connectivity, Granger causality and any lagged measure, and
averaging it away is the one transformation this module will not do behind your
back. `data.is_directed` and `data.asymmetry` report how directed it is —
`asymmetry` is `Σ|M − Mᵀ| / Σ|M|`, zero for a symmetric matrix.

Everything a file disagrees about is refused by name rather than broadcast into
silence:

```
NetworkError: 9 coordinates but 12 labels
NetworkError: 9 coordinates but the matrix is 12 x 12; the two files
              describe different networks
```

### Degrees

If your attribute table does not already carry them:

```python
data.in_degree()                  # column sums of |M|, diagonal excluded
data.out_degree()                 # row sums
data.in_degree(absolute=False)    # signed, so positive and negative cancel
```

The two differ, sometimes a lot, and which one a figure is showing belongs in
its caption. A measure that sums `|weight|` calls a node with strong inhibitory
input a hub; a signed sum calls it nothing at all.

---

## Which edges get drawn

A 9-node directed matrix has 72 off-diagonal entries, and a figure with 72
arrows on it says nothing. So every network figure is a thresholded one, and
**what the threshold means is part of the result**. Three rules, and each names
itself in the generated caption.

### `--top N` — the N strongest directed edges

The rule to use when the figure's claim is "these are the strongest
connections", because that is exactly what it draws.

```bash
cifti-state network net/ --top 20 -o fig.png
cifti-state network net/ --top 20 --rank-abs -o fig.png   # rank by |weight|
```

`--rank-abs` ranks by magnitude, so a strong negative connection competes with
a strong positive one. The weights keep their sign either way, so
`--edge-color sign` can still colour them.

### `--threshold V` — every edge at or above `V`

Honest and boring. The right rule when the number means something: a
significance cutoff, a preregistered value, a number from the paper.

### `-k` / `--edge-count K` — NeuroMArVL's slider

Reproduced exactly, because a figure made here and a figure made in the browser
have to be the same figure. The rule:

1. For each unordered pair, take the **larger of the two directions** — the
   larger *signed* value, so a pair that is `+0.1` one way and `−0.4` the other
   ranks at `+0.1`.
2. Sort those `n(n−1)/2` values from large to small.
3. The `K`-th is the cutoff.
4. Draw **every directed edge** at or above it.

Because the cutoff comes from pairs but is applied to directions, **`K` is not
the number of arrows** — it is usually fewer. On a real 9-node matrix, `k=16`
draws 24 arrows. `--explain-k` prints the whole table for your matrix and stops
without drawing anything:

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

That last line matters. Past it the cutoff goes negative and **negative weights
start being drawn as ordinary edges, with nothing marking them as negative** —
an inhibitory connection rendered exactly like an excitatory one. The renderer
logs a warning when you cross it and the generated caption says so in the
figure. Two honest ways past it:

```bash
cifti-state network net/ -k 34 --drop-negative     # leave them out
cifti-state network net/ -k 34 --edge-color sign   # draw them, in another colour
```

### Verification

The rule is not approximated. `tests/test_network.py` carries NeuroMArVL's own
published `k → arrows` table for five effective-connectivity datasets — 29
numbers across `k ∈ {8, 12, 16, 20, 24, 28, 35}`, plus the largest positive `k`
for each — and checks all of them. Point `CIFTI_STATE_NETWORK_DATA` at a
directory holding those matrices and the test runs; without it, it skips and
the hand-worked four-node example still fixes the semantics.

---

## How it looks

### Nodes

| Option | What it does |
|---|---|
| `--size-by COLUMN` | sphere radius from an attribute column |
| `--size-range MIN MAX` | radius in **millimetres** (default `1.4 6.3`) |
| `--color-by COLUMN` | sphere colour from an attribute column |
| `--colors HEX …` | palette for a discrete attribute, in order of increasing value |
| `--labels-on` / `--no-labels` | node names beside the spheres |

An attribute with **20 or fewer distinct values** is treated as a grouping
variable and gets the discrete palette, in order of increasing value; more than
that and it is treated as a measurement and gets a colour map. That cutoff is
NeuroMArVL's. Force it either way with `NodeStyle(color_mode="discrete")` or
`"continuous"` from Python.

Size mapping spreads the observed range across the whole size range, which is
what the browser does and what makes small differences legible. It also
exaggerates them: an attribute running from 0.95 to 1.00 will produce a
smallest and a largest node. `NodeStyle(size_from_zero=True)` keeps radius
proportional to value instead, so a node twice the value is twice the radius.

### Edges

`--edge-color` picks what the colour says:

| Mode | |
|---|---|
| `none` | one colour for every edge (the default) |
| `weight` | a colour map over `\|weight\|`, ignoring the sign |
| `signed` | dark red for positive, dark purple for negative, depth within each carrying the strength |
| `node` | the **source** node's colour — so colour already carries direction |
| `node-transitioning` | source colour fading into target colour along the edge |
| `sign` | two flat colours, one per sign, with no strength information |

`signed` is the one to reach for when the weights have meaningful signs and you
want strength shown too. Its two ends are `--edge-colors NEGATIVE POSITIVE`
(default `#3f2071` and `#8e1616`), and `--signed-fade` sets how far towards
white the weakest edge of each sign is drawn — `0` makes every edge the full
dark colour and leaves strength entirely to the width.

> It is deliberately **not** a smooth diverging ramp through a neutral hue.
> Fit one symmetrically about zero to a set of weights running from 0.05 to
> 0.19 and the weakest positive edge lands a quarter of the way towards red —
> so it renders closer to the *negative* colour than the positive one, which is
> the opposite of what it means. Each sign gets its own ramp instead, so a
> positive edge is always red and a negative one always purple.

`--direction` picks how direction is shown:

| Mode | |
|---|---|
| `arrow` | a cone at the target end (the default) |
| `gradient` | a colour ramp from `start_color` to `end_color` |
| `taper` | thin at the source, thick at the target |
| `opacity` | fading in towards the target |
| `none` | a plain tube |

`gradient` overrides `--edge-color`, because a tube that varies by both weight
and direction shows neither. `opacity` makes the tubes translucent, which
composites unreliably on software OpenGL — `arrow` and `taper` do not, and the
module says so when you choose it.

Width: `--edge-width MM` for a fixed tube radius (default 0.55 mm), or
`--width-range MIN MAX` to scale it with `|weight|` between those two radii in
millimetres (`--width-by-weight` alone uses the default 0.25–1.4 mm).

When width follows the weight, the arrowhead is sized from the *middle* of the
width range and only grows past it for the thick edges. An arrowhead's job is
to show direction, and strength is already carried by the tube; sizing the head
from its own tube leaves the weakest edges with heads too small to read the
direction off at all.

The default edge colour is dark (`#39474f`) rather than mid-grey, because an
edge is seen *through* the translucent shell, which lightens it: a tube that
looks right on its own disappears once the brain is in front of it.

---

## Reusing a NeuroMArVL settings file

A settings JSON saved from the browser loads directly:

```bash
cifti-state network net/ --settings settings/size_in_degree.json -k 16 -o fig.png
```

```python
from cifti_state.network import load_marvl_settings
style = load_marvl_settings("settings/size_in_degree.json")
```

What comes across: the node size attribute and range, the colour attribute and
its discrete palette, the continuous colour ramp, the edge direction and colour
modes, the edge width and whether it follows the weight, the surface colour and
opacity, which hemispheres are drawn, whether labels are on, the view, and the
edge count from the first saved app. Any command-line option given alongside
`--settings` overrides that part of it.

What does not, because a still figure has no equivalent: the 2D layouts
(`layout2d`, `bundle2d`, `scale2d`), the topology network, `rotation`, and
`directionMode: "animation"` — which falls back to arrows. Each one logs a line
naming itself, so nothing disappears silently.

**Sizes are converted, not copied.** The browser's size range is in its own
renderer's units, where the whole brain is about 2 units across; here the scene
is in millimetres. `MARVL_SIZE_TO_MM = 3.5` maps its default `0.4–1.8` onto
1.4–6.3 mm radius, preserving the ratio. Pass `size_to_mm=` to change it.

`save_marvl_settings(style, "style.json")` writes the format back out, and
`--save-settings` does it from the command line. A round trip preserves
everything the format can carry.

> One detail worth naming, because it produces a figure that looks deliberate
> and is not: `edgeColorByNodeTransitionColor` is the colour of a *transition*
> and applies only when `edgeColorByNodeTransition` is on. Read
> unconditionally, it paints every plain grey edge bright red.

---

## The glass brain

The shell is a real cortical surface — whichever one the configuration provides
at the working mesh, `midthickness` by default. `--surface inflated` uses the
inflated one instead, which is a different claim about where a node is and
should be a deliberate choice: node coordinates are anatomical, and an inflated
surface is not.

```bash
cifti-state network net/ --brain-opacity 0.35 --hemisphere left -o fig.png
cifti-state network net/ --split 36 --views dorsal -o fig.png
```

| Option | |
|---|---|
| `--brain-opacity` | 0 hides the shell entirely, 1 makes it solid (default 0.55) |
| `--hemisphere left \| right \| both` | which sides to draw |
| `--split MM` | pull the hemispheres apart, for a medial view of a network |
| `--surface NAME` | which anatomical surface |

### Two things that make this work, in case you are reading the code

**Draw order.** A translucent surface has to be composited against what is
behind it, and VTK does that correctly only if what is behind it is already
drawn. So every opaque actor — edges, then nodes — is added first and the shell
last. Depth peeling would remove the constraint, but it is unavailable or
broken on software OpenGL, which is exactly where a batch of figures gets
rendered, so the scene is built not to need it. (This mirrors NeuroMArVL's own
`renderOrder = RENDER_ORDER_EDGE`.)

**Backface culling.** Cortex is folded, so a line of sight crosses a dozen
sulcal walls; at any honest per-surface opacity they stack up and the "glass"
brain comes out opaque. Drawing only the outward-facing triangles leaves a
single layer of glass, and the opacity you ask for is the opacity you see.
`BrainStyle(cull_backfaces=False)` turns it off.

---

## Views, panels and output

```bash
cifti-state network net/ --views left right anterior posterior dorsal ventral \
    --columns 3 -o fig.png
```

Six views: `left`, `right`, `anterior`, `posterior`, `dorsal`, `ventral`.
NeuroMArVL's names (`Top`, `Bottom`, `Front`, `Back`) and the obvious aliases
(`lateral`, `superior`, `lh`) are accepted.

**Every panel shows the same network from a different camera**, because the
edge selection is made once and shared — the panels cannot disagree about which
connections exist.

Panels are rendered offscreen one at a time, cropped to their content, padded
back to a single size and tiled with matplotlib. Cropping to content and
re-padding keeps every panel at the *same pixel scale* — a node 20 px across in
one view is 20 px across in the others — while throwing away the blank margin a
fitted camera always leaves. Sizing each panel to fill its own frame would be
prettier and would quietly rescale the brain between views. `crop=False` from
Python turns it off.

| Option | |
|---|---|
| `--size W H` | pixels per panel before cropping (default `1000 850`) |
| `--dpi` | figure resolution (default 200) |
| `--zoom` | camera zoom, if the default framing is too loose or too tight |
| `--columns N` | panels per row |
| `--title` | figure title |
| `--no-caption` | leave off the generated caption line |
| `--vector` | real vector output through gl2ps |

### The caption

Generated rather than typed, because the threshold rule is the part of a
network figure a reader cannot recover by looking at it:

```
9 nodes; 24 edges; NeuroMArVL edge count k=16; cutoff 0.045925;
directed (row -> column); node size: out_degree; node colour: group_id.
```

It grows a clause when it needs to — `7 of the drawn edges are negative and are
not marked as such` — and loses it when `--edge-color sign` makes that untrue.
`--no-caption` for a figure going into a manuscript with its own caption.

### PNG, PDF, SVG

By default the panels are rasterised at `--dpi` and matplotlib owns the page,
so `.pdf` and `.svg` work but the brain inside them is an image. `--vector`
takes PyVista's gl2ps route instead and writes real vector geometry — one view
only, and the shading is flattened. Which you want depends on whether the
journal wants vectors or the figure wants to look right.

---

## Tuning it by eye

A figure's numbers — how many edges, how transparent the shell, how thick the
tubes, whether labels help or crowd — are decided by looking, not by reasoning.
`--interactive` opens a control window: every option as a widget on the left, a
brain you can drag on the right.

![The network control window](images/network_window.png)

```bash
cifti-state network taskaverage/ --settings settings/size_out_degree.json \
    --edge-color signed --width-range 0.3 1.6 --labels-on --interactive
```

| Group | |
|---|---|
| **Which edges** | the rule (`edge count k` / `weight threshold` / `strongest N`) and its value, `rank by \|weight\|`, `drop negative edges`, and a readout of how many arrows that produces and at what cutoff |
| **Nodes** | size attribute and radius range, colour attribute, **one colour picker per level** of that attribute, the label toggle and label size |
| **Edges** | colour mode, the − / + colours as pickers, direction mode, fixed width or width-follows-weight with its range, arrow length |
| **Glass brain** | surface, hemispheres, opacity, split, and the camera view |

Two behaviours are deliberate rather than accidental:

**Refresh is a button.** Rebuilding a dense network takes a second or two, and a
panel that redraws on every spin-box click while you are setting four numbers
spends most of its time drawing states you did not want to see. So changes
accumulate, the button grows a `•` to say something is pending, and one click
applies them. `Auto` turns continuous redraw on for when you are nudging a
single value. The camera and the view menu are exempt — those are cheap, and
waiting for Refresh to look at the other side of the brain would be absurd.

**The command line is always visible.** The bar under the view shows the
`cifti-state network` line that reproduces exactly what is on screen, updated
as you go:

```
cifti-state network taskaverage/ -k 19 --views left --size-by out_degree \
    --color-by group_id --colors '#ff69b4' '#6AB4E3' '#4caf50' \
    --size-range 1.40 6.30 --labels-on --edge-color signed \
    --edge-colors '#3f2071' '#8e1616' --width-range 0.30 1.60 \
    --brain-opacity 0.45 -o figure.png
```

`Copy` puts it on the clipboard; `Save figure…` writes the file directly.
Nothing is stored implicitly — that line is the whole handoff, and it is what
keeps a figure in a paper traceable to something you can read, re-run and put
in a methods section rather than to a window you once had open. A test parses
the printed line back through the CLI to check it still means the same thing.

The readout turns red the moment `k` passes the largest value with a positive
cutoff, so you see the point where negative weights start being drawn as
ordinary edges rather than discovering it later in the caption.

This needs a real window — it cannot run offscreen — and it needs the `gui`
extra:

```bash
pip install -e ".[gui]"     # PySide6 and pyvistaqt
```

Without them, `--interactive` falls back to a plain PyVista window with three
sliders (edge count, shell opacity, edge width) drawn inside the scene. That
path depends on VTK's own widgets, which do not work everywhere; the control
window is the one to use.

`cifti-state-gui` is the desktop interface for the *statistic map* pipeline and
has no network panel.

---

## Command line

```
cifti-state network INPUT -o FIGURE [options]
```

`INPUT` is a directory holding the four files, or the matrix file itself (in
which case `coordinates.txt` beside it is found automatically, or
`--coordinates` names it).

```bash
# the common case
cifti-state network taskaverage/ -k 16 --size-by in_degree --color-by group_id \
    --labels-on -o in_degree.png

# from a saved browser session, with the view overridden
cifti-state network taskaverage/ --settings settings/size_out_degree.json \
    -k 16 --views left dorsal -o out_degree.png

# what does k mean for this matrix
cifti-state network taskaverage/ --explain-k

# one dataset out of several in the directory
cifti-state network Neuromarvl_input/ --dataset listen --top 20 -o listen.png
```

Full option list: `cifti-state network --help`.

---

## From Python

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
    title="Task-average effective connectivity",
)
print(figure.caption, len(figure.edges))
```

`render_network` returns a `NetworkFigure`: the path it wrote, the matplotlib
`Figure`, the panels as arrays, the caption, and the `EdgeSet` that was drawn.

Select edges yourself when you want to do something the three rules do not
cover — keep only edges into one node, keep the top edge per node, apply a
significance mask — and hand the result in:

```python
edges = select_edges(data.matrix, top=40, absolute=True)
keep = np.isin(edges.target, [0, 3])          # only edges into IFJ or B55post
edges = EdgeSet(edges.source[keep], edges.target[keep], edges.weight[keep],
                rule="strongest inputs to IFJ and B55post")
scene = build_network_scene(data, settings, style, edges=edges)
```

### Batch

```python
from cifti_state.network import load_network_dir, render_network_batch

datasets = [load_network_dir("Neuromarvl_input/", dataset=task)
            for task in ("listen", "naming", "reading", "writing")]
render_network_batch(datasets, settings, "figures/", style=style,
                     edge_count=16, views=["left"])
```

The point of a batch is comparability, so everything that would differ by
accident is held fixed — one style, one set of views, one selection rule — and
only the matrices and their attribute tables vary.

---

## What can go wrong

### The nodes are in the wrong space

Easily the most common failure, and the one that produces a figure that looks
fine. Node coordinates in voxel indices, in a different template, or
left-right flipped all give you spheres somewhere plausible-looking and a
completely wrong anatomy.

Every run checks it, and says so when it does not add up:

```
warning: 9 of 9 nodes fall outside the surface's bounding box (worst by
190.4 mm): IFJ, SFL, B55pre, B55post, Aud_primary, VentralMotor. The
coordinates are probably not in the same space as the mesh.
```

From Python, `check_coverage(data, settings)` returns the surface's bounding
box, how many nodes are inside it, the worst distance outside, and which nodes.
A bounding-box test is weak — a node in the middle of the ventricles passes it
— but it catches everything that is off by a coordinate system, which is what
actually happens.

### The brain came out opaque

Backface culling got turned off, or the surface is not closed. Check
`BrainStyle(cull_backfaces=True)` and lower `--brain-opacity`.

### The figure is blank

No offscreen OpenGL context. The same fix as the rest of the package — see
["If the 3D view is blank"](../README.md#if-the-3d-view-is-blank) in the
README. On a headless Linux box, `xvfb-run -a` in front of the command.

### The arrows disappeared

`--direction gradient`, `taper`, `opacity` and `none` all replace the
arrowhead: one direction cue at a time, which is also how the browser behaves.
`--direction arrow` brings them back.

### Nodes are all the same size

`--size-by` names a column that is constant, or names nothing. A constant
column gives every node the middle of the size range rather than dividing by
zero.

### Every edge came out bright red after loading a settings file

Fixed — see the note under [Reusing a NeuroMArVL settings
file](#reusing-a-neuromarvl-settings-file). If you see it, the settings file is
being read by something else.

---

## Try it without any data

```bash
python examples/network_example.py
```

Six figures in `results/network_example/`, covering the direction modes, the
colour modes, the split view and the six-view layout. The nodes are picked off
the bundled fs_LR 32k surface itself, so the coordinates are real cortical
positions in the mesh's own space; the matrix is generated with a fixed seed.
