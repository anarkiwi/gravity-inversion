# gravity-inversion

Rob Hubbard's *Commando* (title) playroutine translated from 6502 assembly to C,
built with the [llvm-mos SDK](https://github.com/anarkiwi/docker-mos-llvm-sdk). The C
player issues the same SID register writes, in the same order and frame, as the
original.

The song data is not part of this repository. The build fetches the
[annotated original](https://github.com/realdmx/c64_6581_sid_players/blob/main/Hubbard_Rob/Hubbard_Rob_Commando.asm)
(pinned commit), assembles it with ACME, and generates `build/commando_data.h` with
the tables byte-identical to the assembled image.

## Use

    docker build .            # lint, build, compare, test

or, with `mos-c64-clang`, `acme` and `requirements-dev.txt` installed:

    make                      # build/commando.prg (C64, plays at raster line $ff)
    make check                # compare SID writes against the original
    make test lint

## Layout

| Path | Content |
|---|---|
| `src/commando.c` | C playroutine |
| `src/main.c` | C64 PRG driver |
| `tools/gen_data.py` | assembled PSID + labels → data header |
| `tools/sidtrace.py` | SID write tracer and comparator (jennings) |
| `tests/` | byte identity of tables, SID write identity of tune and patched variants |

See [docs/translation.md](docs/translation.md) for how the original's behaviour is
preserved.
