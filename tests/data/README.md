# Test reference data

## `palm_tfce_reference.npz`

The output of PALM's own `palm_tfce.m`, used by `tests/test_tfce.py` to check
this package's TFCE against the implementation it is modelled on.

It was produced by running the unmodified function from
[PALM](https://github.com/andersonwinkler/PALM) (Winkler et al.) in GNU Octave
8.4.0 over four synthetic meshes, and printing the result with `%.17g` so that
nothing is lost to the default eight significant digits of Octave's ASCII save.

| case | vertices | faces | mask     | H   | E    |
|------|----------|-------|----------|-----|------|
| c1   | 960      | 1798  | all      | 2.0 | 0.5  |
| c2   | 960      | 1798  | 816 kept | 2.0 | 0.5  |
| c3   | 675      | 1248  | all      | 2.0 | 1.0  |
| c6   | 440      | 798   | all      | 3.0 | 0.25 |

Each case stores `faces`, `area`, `mask`, `values` (the statistic, over the
masked vertices, in PALM's own order), `palm` (PALM's TFCE score for those
vertices) and `params` = `[H, E, dh, n_vertices]`.

`dh` is 0 in every case, meaning PALM's automatic ladder of `max/100`: its
surface branch never assigns the fixed-`dh` variable it later multiplies by,
so that path raises an error in PALM itself and there is no output to compare
against. See the note in `cifti_state.stats.tfce`.
