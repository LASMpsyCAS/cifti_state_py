# One configuration file per machine

Workbench, the fs_LR template pack and the data sit in different places on
every machine. Rather than editing one shared file back and forth, each
machine gets its own profile here, named after its hostname, and the right one
is picked up automatically.

```
configs/
  settings.windows.yaml          platform template — file names, defaults
  settings.linux.yaml            platform template — file names, defaults
  machines/
    laptop-ghlod870.yaml         ← Windows laptop, Workbench 1.5.0
    example-linux-server.yaml    ← copy this for the lab server
    <your-hostname>.yaml
```

A machine file starts with `extends:` pointing at its platform template, so it
only needs to state what is actually machine-specific — usually the Workbench
executables, the template pack location, and the temp directory.

## Set up a new machine

```bash
cifti-state config --init-machine        # writes machines/<hostname>.yaml
```

Then open the generated file, fill in the paths, and check:

```bash
cifti-state check --workbench --render
```

`check` prints which profile is active, whether every file it needs exists,
what `wb_command -version` reports, and whether offscreen rendering works.

To see which file will be used and why:

```bash
cifti-state config --show-chain
```

## How the file is chosen

First hit wins:

| # | Source | Path |
|---|---|---|
| 1 | `--config PATH` | explicit |
| 2 | `$CIFTI_STATE_CONFIG` | environment variable |
| 3 | **this machine** | `configs/machines/<hostname>.yaml` |
| 4 | this user | `~/.config/cifti_state/settings.yaml` (Windows: `%APPDATA%\cifti_state\settings.yaml`) |
| 5 | platform fallback | `configs/settings.<platform>.yaml` |

The hostname is lowercased and the domain is stripped:
`lasm-node01.lab.example.edu` → `lasm-node01.yaml`.

On a cluster where nodes have different hostnames but identical paths, set
`CIFTI_STATE_MACHINE=lasm-cluster` in your shell profile and name the file
`lasm-cluster.yaml`.

## Writing a machine file

```yaml
extends: ../settings.linux.yaml

machine:
  name: lasm-node01
  description: Lab GPU node

workbench:
  wb_command: /usr/local/workbench/bin_linux64/wb_command
  wb_view:    /usr/local/workbench/bin_linux64/wb_view

resources:
  root:          /shared/templates/fs_LR_32-master
  atlas_dir:     /shared/templates/fs_LR_32-master
  atlas_csv_dir: /shared/templates/Anatomical-labels-csv
  neighbors:
    "32k":
      left:  /shared/templates/neighbors/lh.neighbors_IndexStart0.txt
      right: /shared/templates/neighbors/rh.neighbors_IndexStart0.txt
```

Rules worth knowing:

- **A blank value inherits.** `wb_command:` with nothing after it keeps whatever
  the parent template set — it does not clear it. So you can delete any line
  you are not changing, and a freshly scaffolded file with empty placeholders is
  harmless.
- **Relative paths are relative to the file that writes them.** A machine file
  saying `root: ../../templates` resolves against `configs/machines/`, not
  against wherever the platform template lives. Absolute paths are simplest.
- **Surface file names are relative to `resources.root`**, not to the config
  file — those live in the platform template and rarely need overriding.
- **Windows paths**: forward slashes work fine in YAML and avoid escaping
  (`D:/workbench-.../wb_command.exe`). If you use backslashes, quote the string.
- `extends:` may chain (a group template extending a platform template), up to
  8 levels; a cycle is reported rather than hanging.

## Machine-specific things worth putting here

| Setting | Why it differs per machine |
|---|---|
| `workbench.wb_command` / `wb_view` | different install path, `.exe` on Windows |
| `resources.root`, `atlas_dir`, `atlas_csv_dir` | local copy vs shared storage |
| `resources.neighbors` | wherever the `*.neighbors_IndexStart0.txt` files ended up |
| `runtime.tmp_dir` | fast local disk, not a network mount |
| `runtime.n_jobs` | laptop cores vs server cores |
| `render.backend` | a headless server may need `xvfb-run` around the whole command |
| `render.dpi` | 300 for figures, 150 while iterating |
