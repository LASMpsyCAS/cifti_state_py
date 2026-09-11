# Bundled fonts

Drop `.ttf` / `.otf` / `.ttc` files here to pin the typeface regardless of what
is installed on the machine. Anything found is registered with **both** Qt and
matplotlib at start-up and takes priority over installed families, so the
window and the figures use the same glyphs everywhere.

This directory is empty by default: fonts are large and their licences vary, so
the package resolves an installed family instead (see `cifti_state/fonts.py`
for the ordered list, and `cifti-state check --fonts` for what it picked).

If you want full determinism across machines — and you have both English and
Chinese text — a single family covering both is the simplest answer:

| Font | Licence | Covers | Size |
|---|---|---|---|
| Noto Sans SC | SIL OFL 1.1 | Latin + Simplified Chinese | ~10 MB |
| Source Han Sans SC | SIL OFL 1.1 | Latin + Simplified Chinese | ~16 MB |
| Inter + Noto Sans SC | SIL OFL 1.1 | Latin, then CJK fallback | ~11 MB |

Download the static `.otf`/`.ttf` (not the variable font — Qt handles static
faces more predictably), put it here, and restart. `cifti-state check --fonts`
will list it under "bundled" and show it as the resolved interface font.
