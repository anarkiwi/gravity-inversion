# gravity-inversion

C64 SID playroutines translated from 6502 machine code to C with the
[llvm-mos SDK](https://github.com/anarkiwi/docker-mos-llvm-sdk). Each translation issues
the same SID register writes, in the same order and play call, as the original up to the
tune's end.

Tunes and their data are not part of this repository: each tune's build fetches its
original (MD5-pinned) into `fixtures/`, which CI caches, and generates the C data header
from it byte-identical.

## Use

    docker build .            # lint, build, compare, test

or, with `mos-c64-clang`, `acme` and `requirements-dev.txt` installed:

    make                      # tunes/*/build/*.prg
    make check                # compare each tune's SID writes against its original
    make test lint

## Layout

| Path | Content |
|---|---|
| `tunes/<name>/` | one translation: C player, data generator, tests, notes |
| `c64/driver.c` | shared C64 driver: `tune_init`, then `frame_wait` / `tune_play` per call |
| `common.mk` | shared make rules: fixtures, the C build |
| `tools/sidtrace.py` | SID write tracer and comparator per subtune, on pysidtracker's C64 (vagrant-moses machine, jennings CPU, power-on RAM) |
| `tools/disasm.py` | jennings coverage-driven disassembly listing of a PSID |
| `tools/survey.py` | rank tunes by the playroutine code they execute; branch outcome gaps |
| `tools/mutate.py` | mutation check: run a test command against source edits |
| `tools/header.py` | shared CLI of the tune data generators |

## Tunes

| Tune | Notes |
|---|---|
| [Commando](tunes/commando/README.md) | Rob Hubbard, 1985 |
| [Automatas](tunes/automatas/README.md) | Goto80, 2009, defMON, CIA-timed |
| [Ghouls'n'Ghosts](tunes/ghouls/README.md) | Tim Follin, 1989 |
| [Incoherent Nightmare (tune 2)](tunes/incoherent/README.md) | Linus & Fanta, 2016, GoatTracker 2 |
