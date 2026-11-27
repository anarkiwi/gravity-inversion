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
    virtuous-cycles tighten   # after an improvement; commit budgets/*.json

`tools/readability.py [TARGET...]` writes the translation of each tune and fuzz player
(`fuzz-<style>-<seed>`, the residue census' targets) to `out/readability/<name>.c` and
`.h`; `virtuous-cycles.toml` runs the `wayward-census` adapter over them. Keys are
`<name>:<class>`, every class of every file held (zero where it has no site), and
`<name>:cognitive-complexity`, `<name>:locals`, `<name>:locals:<reason>` (definitions of
local variables by why they exist: `single-use`, the removable temporaries, `multi-use`,
`clobbered`, `check-only`, `merged`, `unused`; wayward-eagle's
[locals by reason](https://github.com/anarkiwi/wayward-eagle/blob/main/docs/census.md#locals-by-reason)),
and `<name>:statements` (the file's largest function), as held below; `<name>:lines` is not
held (`lines = ""`), since it follows clang-format's wrapping.
`opaque-name` and `raw-memory` are held as one key, `<name>:opacity`: their sites weighted
by the adapter's `combine` option in `virtuous-cycles.toml` (`opacity = opaque-name +
2 × raw-memory`, since a raw-memory subscript carries an opaque array name and a number).
`statements` is held as `<name>:size` (the statements of the file's largest function), and
locals by their total alone (`reasons = ""`): a rewrite trading one kind of local for
another moves no key, while `virtuous-cycles report` without the option still lists each.
`tools/liftcheck.py` reads the same weights and shows opacity beside its components.

The magnitude keys `*:size`, `*:locals` and `*:opacity` have a tolerance of 0.05
(`[metric.tolerance]`): `check` fails only on a value over its budget × 1.05, while `tighten`
still lowers a budget to any strictly better value and never raises one. Every other key,
counts of defect classes and `cognitive-complexity`, is exact.

## PRG bytes

The `prg` metric holds the bytes of each generated tune's `build/gen.prg` (tunes setting
`GEN := 1`) to `budgets/prg.json`, through the `command` adapter running
`tools/prgsize.py` (`{tune: bytes}` as JSON; a tune not built is an error). Bytes are
compiler output, judged only for large growth: tolerance 0.10 on every key.

    make prg                  # build, then virtuous-cycles -m prg check
    make prg AGAINST=origin/main

Whether a PRG fits the C64 memory map is the link's ([diagnostics.md](diagnostics.md)).

## CI

CI runs `make readability AGAINST=origin/main` and `make prg AGAINST=origin/main`
(`virtuous-cycles check --against origin/main` of each metric): no value may exceed its
budget beyond its tolerance, and no budget may loosen. Budget files merge by keeping the stricter value per key:

    git config merge.virtuous-cycles.driver 'virtuous-cycles merge-driver %O %A %B %P'
    git config merge.virtuous-cycles.name 'keep the stricter budget per key'

(`.gitattributes` assigns `budgets/*.json` to it.)
