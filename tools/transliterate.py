"""Translate a PSID's playroutine to C with deity-informant's cgen and vagrant-moses' C64 model.

Writes the player as deity-informant's translation units (the state's, each entry's, the
shared functions') and the C64 host (`tune_init`/`tune_play` for c64/driver.c) as its own unit
into OUT_DIR, with the song data in the header. Init runs on sidtrace's C64 (`c64_init`) with A
each song the PSID header counts; play is lifted from the state it leaves, which the header
holds."""

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


def transliterate(psid, header, lines=False, split=False):
    """(c, h, info) of `psid` loaded over power-on RAM; the C includes `header`; `lines`:
    with deity-informant's `#line` markers (docs/sizes.md, *Byte ownership*); `split`: `c` the
    translation units `{file: text}` with the host's (:func:`host_unit`), else one text.
    """
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
        lines=lines,
        split=split,
    )
    if split:
        return {**c, f"{stem(header)}_host.c": host_unit(header)}, h, info
    return c + HOST, h, info


def stem(header):
    """The name the units of a translation including `header` share."""
    return pathlib.PurePath(header).stem


def host_unit(header):
    """vagrant-moses' C64 host as a translation unit: the entries' prototypes before it."""
    protos = "".join(f"void {name}(uint8_t a);\n" for name in ENTRIES)
    return f'#include "{header}"\n{protos}{HOST}'


def write(out_dir, c, h, header):
    """The C paths of translation `c` (units `{file: text}`, or one text as <stem>.c) written
    into out_dir with its `header` text `h`."""
    out = pathlib.Path(out_dir)
    (out / header).write_text(h, encoding="utf-8")
    units = {f"{stem(header)}.c": c} if isinstance(c, str) else c
    for name, text in units.items():
        (out / name).write_text(text, encoding="utf-8")
    return [out / name for name in units]


def trap_source(out_dir):
    """vagrant-moses' C64 `trap`, written as out_dir/cgen_trap.c: its own translation unit."""
    path = pathlib.Path(out_dir) / "cgen_trap.c"
    path.write_text(TRAP, encoding="utf-8")
    return path


def build(psid, out_dir, name="gen", flags=()):
    """(PRG path, units, h) of the C generated from `psid` as translation units of out_dir/name.h,
    built for the C64 by `compile_prg`."""
    c, h, _ = transliterate(psid, f"{name}.h", split=True)
    return compile_prg(write(out_dir, c, h, f"{name}.h"), name, flags), c, h


#: $CC flags of a unit's bitcode left for the whole program's optimization (`-Os` kept).
UNIT = ("-flto", "-Xclang", "-disable-llvm-passes", "-c")
#: $CC flags linking units' bitcode into one module, unoptimized.
MERGE = ("-flto", "-r", "-nostdlib", "-Wl,--lto-O0,--lto-emit-llvm")


def compile_prg(units, name="gen", flags=()):
    """name.prg beside `units` (C paths) built by $CC (default mos-c64-clang) with cgen_trap.c
    and c64/driver.c from the units' bitcode linked into one module (name.bc), optimized as one
    file is; symbols in PRG.elf."""
    out = pathlib.Path(units[0]).parent
    prg, module = out / f"{name}.prg", out / f"{name}.bc"
    cc = [
        os.environ.get("CC", "mos-c64-clang"),
        "-Os",
        *flags,
        f"-I{out}",
        f"-I{DRIVER}",
    ]
    bcs = [f"{u}.bc" for u in units]
    for unit, bc in zip(units, bcs):
        subprocess.run([*cc, *UNIT, "-o", bc, unit], check=True)
    subprocess.run([cc[0], *MERGE, "-o", module, *bcs], check=True)
    subprocess.run(
        [*cc, "-o", prg, module, trap_source(out), DRIVER / "driver.c"], check=True
    )
    return prg


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out", help="output directory (cgen_trap.c in it too)")
    ap.add_argument("--header", required=True, help="the units' header name")
    ap.add_argument("--list", help="write the units' paths, one per line, to this file")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    c, h, info = transliterate(
        pathlib.Path(args.psid).read_bytes(), args.header, split=True
    )
    paths = write(out, c, h, args.header)
    trap_source(out)
    if args.list:
        pathlib.Path(args.list).write_text(
            "".join(f"{p}\n" for p in paths), encoding="utf-8"
        )
    times = " ".join(f"{k} {v:.2f}s" for k, v in info["times"].items())
    size = sum(len(t) for t in c.values())
    print(f"{out}: {len(c)} units, {size} B, {len(info['traps'])} traps; {times}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
