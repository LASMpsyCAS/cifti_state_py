# Installing cifti_state on Windows — a complete worked example

conda creates the interpreter; pip installs everything else. Solving a full
scientific stack in conda takes minutes, and pip resolves the same set in
seconds, so conda's only job here is isolation.

Every command below is meant to be pasted as-is into **Anaconda Prompt**. The
output shown is what you should actually see.

---

## 0. Before you start

You need:

- Anaconda or Miniconda installed
- Connectome Workbench at `D:\workbench-windows64-v1.5.0\workbench\bin_windows64`
- the template pack and data under `D:\Code_renew\Cifti_state`
- this package at `D:\Code_renew\cifti_state_py`

About **1.5 GB** of downloads: VTK and PySide6 are ~100 MB wheels each, the rest
is small.

---

## 1. Make pip fast (mainland China)

If pip is crawling, point it at a domestic mirror once — this setting is global
and persists, so you only ever do it once per machine:

```cmd
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
```

```
Writing to C:\Users\<you>\AppData\Roaming\pip\pip.ini
```

Alternatives if Tsinghua is slow: `https://mirrors.aliyun.com/pypi/simple/`,
`https://mirrors.cloud.tencent.com/pypi/simple/`. To undo it later:
`pip config unset global.index-url`.

Outside China, skip this step entirely.

---

## 2. Create the environment

```cmd
conda create -n cifti-state python=3.11 pip -y
```

```
## Package Plan ##
  environment location: C:\Users\<you>\anaconda3\envs\cifti-state
  added / updated specs: python=3.11, pip
...
done
#
# To activate this environment, use
#     $ conda activate cifti-state
```

This is the only conda step, and it takes under a minute because it resolves
nothing but Python itself.

```cmd
conda activate cifti-state
python --version
```

```
Python 3.11.x
```

> `conda env create -f environment.yml` does exactly the same thing if you
> prefer the file.

---

## 3. Install the dependencies with pip

```cmd
cd /d D:\Code_renew\cifti_state_py
pip install -r requirements.txt
```

```
Collecting numpy>=1.24 ...
Collecting scipy>=1.10 ...
Collecting nibabel>=5.0 ...
Collecting vtk>=9.2 ...
Collecting PySide6>=6.5 ...
...
Successfully installed PySide6-6.x.x brainspace-0.1.x matplotlib-3.x.x
nibabel-5.x.x numpy-2.x.x openpyxl-3.x.x pandas-2.x.x PyYAML-6.x.x
scipy-1.x.x shiboken6-6.x.x surfplot-0.2.0 tabulate-0.9.0 vtk-9.x.x
```

A few minutes on a normal connection; the two big wheels dominate.

---

## 4. Install the package itself

```cmd
pip install -e .
```

```
Obtaining file:///D:/Code_renew/cifti_state_py
  Installing build dependencies ... done
  ...
Successfully installed cifti-state-0.1.0
```

`-e` (editable) means edits to the source take effect immediately — no
reinstall after a change.

---

## 5. Check it imports

```cmd
python -c "import cifti_state, numpy, scipy, pandas, nibabel, matplotlib, surfplot, vtk, PySide6; print('cifti_state', cifti_state.__version__); print('vtk', vtk.vtkVersion.GetVTKVersion()); print('PySide6', PySide6.__version__)"
```

```
cifti_state 0.1.0
vtk 9.x.x
PySide6 6.x.x
```

---

## 6. Check the machine profile

Your profile (`configs/machines/laptop-ghlod870.yaml`) already ships with this
package, and is picked up automatically from the hostname:

```cmd
cifti-state config --show-chain
```

```
machine  : laptop-ghlod870
platform : windows

 -> machine (laptop-ghlod870)  D:\Code_renew\cifti_state_py\configs\machines\laptop-ghlod870.yaml
  x user                       C:\Users\<you>\AppData\Roaming\cifti_state\settings.yaml
    platform (windows)         D:\Code_renew\cifti_state_py\configs\settings.windows.yaml

  -> selected    x missing    (blank) present but outranked

extends chain:
     D:\Code_renew\cifti_state_py\configs\settings.windows.yaml
```

Then the full check:

```cmd
cifti-state check --workbench --render
```

```
=== machine =================================================
hostname          : laptop-ghlod870
platform          : windows
profile           : Windows laptop (Connectome Workbench 1.5.0)
configuration     : D:\Code_renew\cifti_state_py\configs\machines\laptop-ghlod870.yaml
    extends       : D:\Code_renew\cifti_state_py\configs\settings.windows.yaml

=== resources ===============================================
resources.root        : ok D:/Code_renew/Cifti_state/fs_LR_32-master
atlas_dir             : ok D:/Code_renew/Cifti_state/fs_LR_32-master
atlas_csv_dir         : ok D:/Code_renew/Cifti_state/Anatomical-labels-csv
tmp_dir               : will be created D:/Code_renew/cifti_state_py/.tmp
surface L/inflated    : ok fs_LR.32k.L.inflated.surf.gii
surface L/midthickness: ok fs_LR.32k.L.midthickness.surf.gii
surface R/inflated    : ok fs_LR.32k.R.inflated.surf.gii
surface R/midthickness: ok fs_LR.32k.R.midthickness.surf.gii
neighbors left        : ok D:/Code_renew/Cifti_state/lh.neighbors_IndexStart0.txt
neighbors right       : ok D:/Code_renew/Cifti_state/rh.neighbors_IndexStart0.txt

configuration looks complete.

=== workbench ===============================================
wb_command        : D:\workbench-windows64-v1.5.0\workbench\bin_windows64\wb_command.exe
wb_view           : D:\workbench-windows64-v1.5.0\workbench\bin_windows64\wb_view.exe
                    Connectome Workbench
                    Version: 1.5.0

=== rendering ===============================================
surfplot          : yes
vtk               : 9.x.x
offscreen render  : True
```

**Everything must say `ok` and there must be no PROBLEMS section.** Notes about
optional surfaces (pial, white, flat, sphere) are fine — nothing uses them
unless you pick them in the preview.

---

## 7. Run the analysis you already know the answer to

```cmd
cifti-state run D:\Code_renew\Cifti_state\Example_test\group_mean_thresh_fdr_E_D.dscalar.nii --method fixed --threshold 1.09 --extent 20 --legacy --atlas Glasser_2016 -o D:\Code_renew\results\E_D
```

```
group_mean_thresh_fdr_E_D.dscalar: 76 clusters (L 38 / R 38) at fixed(+1.09), extent >= 20, atlas Glasser_2016
  cluster_map      D:\Code_renew\results\E_D\group_mean_thresh_fdr_E_D_cluster_extent20_thr1.09.dscalar.nii
  report_csv       ...__report.csv
  peaks_csv        ...__peaks.csv
  annotations_csv  ...__regions.csv
  record           ...__analysis.json
```

**76 clusters, 38 left and 38 right** is the number your MATLAB run produced on
this file. If you get that, the install is correct end to end.

---

## 8. Run the ten-step worked example

```cmd
python examples\worked_example.py --out D:\Code_renew\results\worked_example
```

Key lines to look for:

```
re-read as 64k: 64k dense surface (32k cortex including medial wall)  identical=True
txt == mesh   : True (0 vertices differ)
legacy (positive only) : 76 clusters (L 38 / R 38)
  csv      ... bytes  report.csv
  xlsx     ... bytes  report.xlsx
```

Step 10 will now actually run, because you have Workbench:

```
command  : "D:\workbench-windows64-v1.5.0\...\wb_command.exe" -cifti-smoothing ...
exit     : 0 in 2.3s
```

---

## 9. Run the tests

```cmd
set CIFTI_STATE_TEST_CONFIG=D:\Code_renew\cifti_state_py\configs\machines\laptop-ghlod870.yaml
set CIFTI_STATE_TEST_EXAMPLES=D:\Code_renew\Cifti_state\Example_test
pip install -r requirements-dev.txt
pytest -q
```

```
.......................................................................
................
87 passed in ~15s
```

Without the two environment variables the data-backed tests skip and you get
about 62 passed, 25 skipped — which is also fine, it just means the MATLAB
regression did not run.

---

## 10. Open the interface

```cmd
cifti-state-gui
```

or straight onto a file:

```cmd
cifti-state-gui D:\Code_renew\Cifti_state\Example_test\group_mean_thresh_fdr_E_D.dscalar.nii
```

Then: **Load map** → **Find clusters** → the report builds itself → **Render**.
Select a row in the table to enable **Save selection as mask…**, and
**Open in wb_view** to inspect the cluster map in Workbench.

---

## Every day after this

```cmd
conda activate cifti-state
cifti-state-gui
```

That is the whole routine. Steps 1–4 are once per machine.

---

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `'cifti-state' is not recognized` | The environment is not active. `conda activate cifti-state`. |
| `ModuleNotFoundError: cifti_state` | `pip install -e .` was run outside the environment, or not at all. Check `where python` points inside `envs\cifti-state`. |
| pip download stalls | Set the mirror (step 1), then retry. `pip install --no-cache-dir -r requirements.txt` if a partial download got cached. |
| `check` says wb_command NOT FOUND | The path in `configs\machines\laptop-ghlod870.yaml` does not match your install. Fix `workbench.wb_command` and `wb_view`. |
| `check` says a resource is MISSING | Same file, `resources.root` / `neighbors`. Everything else is inherited from the platform template. |
| `offscreen render : False` | VTK cannot get an OpenGL context. On a laptop, update the graphics driver; if you are on Remote Desktop, VTK often cannot render — use a local session. |
| GUI opens but the preview stays empty | Press **Render**. It only draws on request (or when you change a view control), because rendering takes a couple of seconds. |
| `ImportError: DLL load failed` on PySide6 | Missing Visual C++ runtime. Install the Microsoft Visual C++ Redistributable, or run the `vcredist_x64.exe` already sitting in the Workbench `bin_windows64` folder. |
| The number boxes show the wrong characters (`0.1160` as `O.ıı ϗ OO`) | A font whose digits do not map to their own glyphs — usually a hand-installed variable font. Run `cifti-state check --fonts --sample specimen.png`, open the PNG, and see which family is wrong; then set `interface.ui_font` / `interface.mono_font` in `configs\machines\<hostname>.yaml` to one that draws them correctly (`Microsoft YaHei UI` and `Consolas` are safe on Windows). |
| Text is too large or too small for its box | Set `interface.font_size_px` in the machine profile. Both the widgets and the stylesheet read that one number, so they cannot drift apart. |
| The brain looks like a plain grey shell | `render.underlay` is `none`, or `fs_LR.32k.LR.sulc.dscalar.nii` is not in `resources.root`. The **Underlay** control in the preview toolbar switches it per render. |
| The folding pattern looks inside out — gyri dark, sulci light | The underlay file uses the opposite sign convention and the automatic measurement was fooled. Set `render.underlay_orient: positive` (or `negative`) in the machine profile. |
| Everything works but a t map gives odd thresholds | Set **Values are: t value** and fill in **df**. Read as z, a t map's p-values are overstated. |

## Starting over

```cmd
conda deactivate
conda env remove -n cifti-state -y
```

Then go back to step 2. Nothing outside the environment is touched.
