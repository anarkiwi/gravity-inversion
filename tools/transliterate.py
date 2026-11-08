"""Translate a PSID's playroutine to C with deity-informant's cgen and vagrant-moses' C64 model.

Writes the player and C64 host (`tune_init`/`tune_play` for c64/driver.c) to OUT.c and
the song data to the header. Init is called once, before any play call, as c64/driver.c
does, with A each song the PSID header counts: it is lifted as code like play."""

import argparse
import os
import pathlib
import subprocess
import sys

from deity_informant import cgen
from pysidtracker.emu import observe_data_access, run_to_rts, sync_ram, wire_mpu
from pysidtracker.header import parse_sid_header
from vagrant_moses.c64 import poweron_ram
from vagrant_moses.cgen import ENTRIES, HOST, SENSITIVE, TRAP, VOLATILE

from sidtrace import MAX_CYCLES, psid_parts

DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"


def c64_init(mem, pc, a):
    """``postinit`` machine: init at ``pc`` with ``A = a`` on sidtrace's C64 (its SID answers
    hardware probes), ``mem`` left as init leaves it; (volatile writes, addresses stored).
    """
    io, stored = [], set()
    mpu, bus = wire_mpu(mem)

    def on_write(addr, value):
        stored.add(addr)
        if any(lo <= addr < hi for lo, hi in VOLATILE):
            io.append((addr, value))

    observe_data_access(mpu, on_write=on_write)
    run_to_rts(mpu, bus, pc, a, MAX_CYCLES)
    sync_ram(mpu)
    return io, stored


def transliterate(psid, header):
    """(c, h, info) of `psid` loaded over power-on RAM; the .c includes `header`."""
    load, init, play, image = psid_parts(psid)
    ram = bytearray(poweron_ram())
    ram[load : load + len(image)] = image
    c, h, info = cgen.translate(
        ram,
        list(zip((init, play), ENTRIES)),
        VOLATILE,
        SENSITIVE,
        loaded=(load, load + len(image)),
        header=header,
        init=0,
        songs=range(parse_sid_header(psid).songs),
        machine=c64_init,
    )
    return c + HOST, h, info


def trap_source(out_dir):
    """vagrant-moses' C64 `trap`, written as out_dir/cgen_trap.c: its own translation unit."""
    path = pathlib.Path(out_dir) / "cgen_trap.c"
    path.write_text(TRAP, encoding="utf-8")
    return path


def build(psid, out_dir, name="gen", flags=()):
    """(PRG path, c, h) of the C generated from `psid` as out_dir/name.c and .h, built for
    the C64 by `compile_prg`."""
    out = pathlib.Path(out_dir)
    c, h, _ = transliterate(psid, f"{name}.h")
    (out / f"{name}.c").write_text(c, encoding="utf-8")
    (out / f"{name}.h").write_text(h, encoding="utf-8")
    return compile_prg(out, name, flags), c, h


def compile_prg(out_dir, name="gen", flags=()):
    """out_dir/name.prg built from out_dir/name.c with cgen_trap.c and c64/driver.c by $CC
    (default mos-c64-clang); symbols in PRG.elf."""
    out = pathlib.Path(out_dir)
    prg = out / f"{name}.prg"
    subprocess.run(
        [
            os.environ.get("CC", "mos-c64-clang"),
            "-Os",
            *flags,
            f"-I{out}",
            f"-I{DRIVER}",
        ]
        + ["-o", prg, out / f"{name}.c", trap_source(out), DRIVER / "driver.c"],
        check=True,
    )
    return prg


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out", help="output .c (cgen_trap.c beside it)")
    ap.add_argument("--header", required=True, help="output .h")
    args = ap.parse_args(argv)
    header = pathlib.Path(args.header)
    c, h, info = transliterate(pathlib.Path(args.psid).read_bytes(), header.name)
    pathlib.Path(args.out).write_text(c, encoding="utf-8")
    header.write_text(h, encoding="utf-8")
    trap_source(pathlib.Path(args.out).parent)
    times = " ".join(f"{k} {v:.2f}s" for k, v in info["times"].items())
    print(f"{args.out}: {len(c)} B, {len(info['traps'])} traps; {times}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
