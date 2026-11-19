# Readability ratchet

[wayward-eagle's census](https://github.com/anarkiwi/wayward-eagle/blob/main/docs/census.md)
lists each readability defect of C by class: clang-tidy's checks and checks over clang's
AST, keyed to a decompiler-fidelity taxonomy. [virtuous-cycles](https://github.com/anarkiwi/virtuous-cycles)
holds its counts, per generated file and class, and each file's total cognitive complexity,
to `budgets/readability.json`, which may only tighten. Each translation is read as one file
(`transliterate(psid, header)`, its single-text form): how it is split into translation units
changes no count.

    make readability          # generate, then virtuous-cycles check
    make readability AGAINST=origin/main   # also fail if a budget loosened
    virtuous-cycles tighten   # after an improvement; commit budgets/readability.json

`tools/readability.py [TARGET...]` writes the translation of each tune and fuzz player
(`fuzz-<style>-<seed>`, the residue census' targets) to `out/readability/<name>.c` and
`.h`; `virtuous-cycles.toml` runs the `wayward-census` adapter over them. Keys are
`<name>:<class>`, every class of every file held (zero where it has no site), and
`<name>:cognitive-complexity`, `<name>:locals`, and `<name>:lines` and `<name>:statements` (the
file's largest function).

CI runs `make readability AGAINST=origin/main` (`virtuous-cycles check --against origin/main`): no value may exceed its budget, and
no budget may loosen. Budget files merge by keeping the stricter value per key:

    git config merge.virtuous-cycles.driver 'virtuous-cycles merge-driver %O %A %B %P'
    git config merge.virtuous-cycles.name 'keep the stricter budget per key'

(`.gitattributes` assigns `budgets/*.json` to it.)

## Names

deity-informant names the C's memory and values (its docs/cgen.md, *Names*): each region's
atoms (arrays, per-voice fields, scalars, pointer pairs) are `.h` views named by the SID
register their data reaches (`g_ctrl[voice]`), SID addresses by register
(`SID_V1_CTRL + voice_reg`), and variables by label (`freq_hi`), role (`i`, `ptr`) or left
`vN`. `tools/namestats.py [TUNE...]` prints per tune the atoms by kind, the labelled ones,
the accesses by how they print (`straddling`, `unbounded` and the pointer kinds stay the
region's), the straddling address sets and the share of variables left `vN`, from
`info["names"]`.
