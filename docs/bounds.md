# Bounds check (Frama-C EVA)

An independent check that the generated C cannot access outside its objects:
[Frama-C](https://frama-c.com)'s EVA plugin (abstract interpretation) runs each tune's
single-file translation, `out/readability/<tune>.c` (`tools/readability.py`), under a
harness, and lists as an alarm each operation it cannot prove defined.

    make bounds                       # generate the C, run EVA, virtuous-cycles check
    make bounds AGAINST=origin/main   # also fail if the budget loosened
    tools/eva.py [TUNE...] [-v]       # analyse tunes (default: every GEN := 1 tune)
    tools/eva.py --metric             # {"<tune>:alarms": count} from the reports

`tools/eva.py` runs every tune in parallel, each in a `framac/frama-c:30.0` container
limited to 2 CPUs (`--image`, `--cpus`; `--frama-c PATH` runs a local frama-c instead),
as `frama-c -machdep gcc_x86_32 harness.c -eva -eva-precision 0`. The metric holds only
reports at precision 0, where every tune finishes (`--precision` runs another); zero alarms
is a proof at any precision. Each tune has a timeout
(`--timeout`, seconds); a tune that hits it, a frama-c that fails, a log with no analysis
summary, an analyzer error, a summary count unlike the alarms listed, or a `tune_play` that
never returns fails the run: none of them is a count of zero.

## Harness

`tools/eva_harness.c`, with the tune substituted for `@TUNE@`:

- `CGEN_HOST` is defined and the tune's `.c` included, so the C is analysed as one file
  with its static initialisers (song data and initial state) as the initial state;
- `tune_init` is called with any song (`Frama_C_unsigned_char_interval(0, 255)`), then
  `tune_play` any number of times (`while (Frama_C_nondet(0, 1))`);
- `io_read` returns any byte on every call, `io_write` does nothing, and `trap` is `abort()`.

## What it proves

Zero alarms for a tune means: for every song number, every number of play calls and every
sequence of I/O read values, no execution indexes, reads or writes outside an object, or
performs another undefined operation EVA checks (invalid shift, signed overflow, division
by zero, uninitialised read, invalid pointer). EVA over-approximates, so an alarm is an
operation it could not prove defined, not necessarily one an execution reaches. An unbounded
access in a lift is an error to fix in the lifter, so the goal is zero alarms on every tune.

## What it assumes

- The C's semantics on a 32-bit host (`gcc_x86_32`): `CGEN_IX` takes its host form, so an
  index is checked against its object's extent, not wrapped to the 6502's 16-bit space.
- I/O writes have no effect on state: SID registers are write-only to play, and every I/O
  read is already any value.
- A trap ends its path. Whether `trap` is reachable is reported (`trap_reached`: its final
  state is non-terminating) but not counted; accesses after a trap are not analysed.
- The single-file form is the same lift as the translation units `make` builds
  ([readability.md](readability.md)); the driver's `frame_wait` and the 6502 soft stack are
  outside the C analysed.

## Reports

`out/eva/<tune>/` holds the harness, the analysed sources, `eva.log` and `report.json`:
`alarms` (each `file`, `line`, `kind` the message, such as `out of bounds read`, and
`assertion` EVA's predicate), `count` (the summary's), `errors` per analyzer, `coverage`
(functions analysed and statements reached, each with its total), `trap_reached`,
`play_returns`, `seconds`, `returncode` and `source` (the sha256 of the analysed C, which
`--metric` checks against the current `out/readability/<tune>.c`).

## Ratchet

The `bounds` metric of `virtuous-cycles.toml` (`command` adapter, `tools/eva.py --metric`)
holds each tune's `<tune>:alarms` to `budgets/bounds.json`, exactly: no tolerance.

    virtuous-cycles -m bounds tighten   # after an improvement; commit budgets/bounds.json

## CI

The `bounds` job generates the C in the deps image, runs `tools/eva.py` on the runner with
the `framac/frama-c:30.0` image, then `virtuous-cycles -m bounds check --against
origin/main` in the deps image.
