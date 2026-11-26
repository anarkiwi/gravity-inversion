"""Translate a PSID's playroutine to C with deity-informant's cgen and vagrant-moses' C64 model.

Writes the player as deity-informant's translation units (the state's, each entry's, the
shared functions') and the C64 host (`tune_init`/`tune_play` for c64/driver.c) as its own unit
into OUT_DIR, with the song data in the header. Init runs on sidtrace's C64 (`c64_init`) with A
each song the PSID header counts; play is lifted from the state it leaves, which the header
holds, its bounds and branch decisions from the code and the read-only data alone (play's orbit
is `tools/liftcheck.py`'s check)."""

import argparse
import functools
import json
import os
import pathlib
import re
import subprocess
import sys

from deity_informant import cgen
import numpy as np
from pysidtracker.emu import observe_data_access, sync_ram, wire_mpu
from pysidtracker.header import parse_sid_header
from vagrant_moses.cgen import ENTRIES, HOST, SENSITIVE, TRAP, VOLATILE

from sidtrace import MAX_CYCLES, boot_ram, header_model, psid_parts

DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
#: deity-informant's overrun rule, as `cgen` takes it (`info["overruns"]` are the sites the rule
#: bounds, which `tools/sidtrace.py` applies to the reference).
OVERRUN = "trap"
#: The write bit of a key of jennings' access log (``pc << 17 | write << 16 | addr``).
STORE = np.uint64(1 << 16)
#: The stack page.
STACK = range(0x100, 0x200)


def c64_init(mem, pc, a, sid_model="6581"):
    """``postinit`` machine: init at ``pc`` with ``A = a`` on sidtrace's C64 (its `sid_model`
    SID answers hardware probes), ``mem`` left as init leaves it; (volatile writes, addresses
    stored). The data stores come from the core's access log; only the volatile writes and
    the stack page's (its pushes) reach Python."""
    io, stored = [], set()

    def on_write(addr, value):
        if STACK.start <= addr < STACK.stop:
            stored.add(addr)
        else:
            io.append((addr, value))

    mpu, _ = wire_mpu(mem, sid_model=sid_model)
    writes = [STACK, *(range(lo, hi) for lo, hi in VOLATILE)]
    observe_data_access(mpu, on_write=on_write, writes=writes)
    _, log = mpu.run_to_rts_accesses(pc, a, MAX_CYCLES)
    sync_ram(mpu)
    log = np.asarray(log, np.uint64)
    return io, stored | set((log[log & STORE != 0] & 0xFFFF).tolist())


def transliterate(psid, header, lines=False, split=False):
    """(c, h, info) of `psid` on sidtrace's PSID boot RAM; the C includes `header`; `lines`:
    with deity-informant's `#line` markers (docs/sizes.md, *Byte ownership*); `split`: `c` the
    translation units `{file: text}` with the host's (:func:`host_unit`), else one text.
    """
    load, init, play, image = psid_parts(psid)
    c, h, info = cgen.translate(
        boot_ram(psid),
        list(zip((init, play), ENTRIES)),
        VOLATILE,
        SENSITIVE,
        loaded=(load, load + len(image)),
        header=header,
        init=0,
        songs=range(parse_sid_header(psid).songs),
        machine=functools.partial(c64_init, sid_model=header_model(psid)),
        lines=lines,
        split=split,
        overrun=OVERRUN,
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


#: A ``static const`` table deity-informant names by the address of its first byte, and its data.
TABLE = re.compile(r"\btable_[0-9a-f]{4}(?:_[0-9a-f]{4})?\b(\[\] = \{[^}]*\})?")


def code(c):
    """The code of generated C `c` (a text, or units by name): its constant tables renamed in
    order of first use and their data elided, what a lift of the same program from data at
    other addresses keeps (the tables' names carry their addresses, their data the data's
    pointers)."""
    text = "".join(c[k] for k in sorted(c)) if isinstance(c, dict) else c
    names = {}

    def sub(m):
        name = m[0][: len(m[0]) - len(m[1] or "")]
        return names.setdefault(name, f"table{len(names)}") + (
            "[] = {}" if m[1] else ""
        )

    return TABLE.sub(sub, text)


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
    ap.add_argument("--overruns", help="write info['overruns'] as JSON to this file")
    ap.add_argument(
        "--song-data",
        help="write info['song_data'] (null where the lifter has none) here",
    )
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
    if args.overruns:
        pathlib.Path(args.overruns).write_text(
            json.dumps(info["overruns"]), encoding="utf-8"
        )
    if args.song_data:
        pathlib.Path(args.song_data).write_text(
            json.dumps(info.get("song_data")), encoding="utf-8"
        )
    times = " ".join(f"{k} {v:.2f}s" for k, v in info["times"].items())
    size = sum(len(t) for t in c.values())
    print(f"{out}: {len(c)} units, {size} B, {len(info['traps'])} traps; {times}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
