"""Byte ownership of a translation's code (docs/sizes.md, *Byte ownership*).

Each tune is translated with deity-informant's `#line` markers and built with `-g`; the DWARF
line table gives each `.text` byte of the translation to an original instruction or an
artifact (a check, trap, access helper, copy, frame or runtime)."""

import argparse
import json
import os
import pathlib
import subprocess
from collections import Counter

from deity_informant.cgen import cost, marks
from elftools.elf.elffile import ELFFile
from jennings.opcodes import OPCODES
from tqdm import tqdm

import sizes
from constructs import ROOT, load, memory
from transliterate import compile_prg, transliterate, write

CLASSES = ("load/store", "ALU", "branch", "indirect", "other")
MOVES = frozenset(
    ("LDA", "LDX", "LDY", "STA", "STX", "STY", "LAX", "SAX", "SHA", "SHX", "SHY")
)
TRANSFERS = frozenset(("JMP", "JSR", "RTS", "RTI", "BRK", "JAM"))
ALU = frozenset(
    "ADC SBC AND ORA EOR CMP CPX CPY BIT INC DEC INX INY DEX DEY ASL LSR ROL ROR SLO RLA SRE "
    "RRA DCP ISC ANC ALR ARR SBX ANE LXA LAS TAS".split()
)
ARTIFACTS = ("check", "trap", "access", "copy", "frame", "runtime")
OUT = ROOT / "out" / "owners"
#: The artifact sources listed by size.
TOP = 10


def opclass(op):
    """The class of opcode `op`: a move of a byte, arithmetic or logic, a transfer of control,
    an access through a zero-page pointer, or other (register transfers, stack, flags).
    """
    mnemonic, mode = OPCODES.get(op, ("JAM", "impl"))
    if mode in ("indx", "indy"):
        return "indirect"
    if mode == "rel" or mnemonic in TRANSFERS:
        return "branch"
    if mnemonic in MOVES:
        return "load/store"
    return "ALU" if mnemonic in ALU else "other"


def owners(elf):
    """{(file, line): bytes}: the bytes the DWARF line table gives each line of each source
    file (by name, over every unit: link-time optimization moves code between them); a row of
    line 0 takes the line before it."""
    out = Counter()
    with open(elf, "rb") as f:
        dwarf = ELFFile(f).get_dwarf_info()
        for cu in dwarf.iter_CUs():
            prog = dwarf.line_program_for_CU(cu)
            files = [pathlib.Path(e.name.decode()).name for e in prog["file_entry"]]
            at, key = None, ("", 0)
            for e in (e for e in prog.get_entries() if e.state is not None):
                st = e.state
                if at is not None:
                    out[key] += st.address - at
                idx = st.file if prog.header.version >= 5 else st.file - 1
                key = (files[idx], st.line) if st.line else key
                at = None if st.end_sequence else st.address
    return out


def hand_bytes(tune, out):
    """`.text` bytes of `tune`'s hand C, built with `-g` under `out`, by the line table."""
    b = pathlib.Path(out).resolve() / "hand"
    cflags = "-Os -Wall -Wextra -Werror -g"
    cmd = ["make", "-s", "-C", str(ROOT / "tunes" / tune), f"B={b}", f"CFLAGS={cflags}"]
    subprocess.run(
        [*cmd, str(b / f"{tune}.prg")], check=True, stdout=subprocess.DEVNULL
    )
    own = owners(b / f"{tune}.prg.elf")
    return sum(n for (f, _), n in own.items() if f == f"{tune}.c")


def build(tune, out):
    """(info, {(file, line): bytes} of the translation) of `tune` built under `out` with its
    `#line` markers and `-g`."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path, where = ROOT / load(tune)["psid"], ROOT / "tunes" / tune
    if not path.exists():
        subprocess.run(
            ["make", "-s", "-C", str(where), str(path.relative_to(where))], check=True
        )
    c, h, info = transliterate(path.read_bytes(), "gen.h", lines=True, split=True)
    units = write(out, c, h, "gen.h")
    own = owners(pathlib.Path(f"{compile_prg(units, 'gen', ('-g',))}.elf"))
    return info, Counter(
        {k: n for k, n in own.items() if k[0] in marks.FILES or k[0] in c}
    )


def ownership(tune, out):
    """The byte ownership of `tune`'s translation built under `out` (docs/sizes.md)."""
    info, own = build(tune, out)
    mem, e = memory((ROOT / load(tune)["psid"]).read_bytes()), info["emission"]
    found = {"classes": {k: [0, 0] for k in CLASSES}, "artifacts": Counter()}
    redundant, checks = set(cost.redundant(e)), Counter()
    for (f, n), size in own.items():
        if f == "6502":
            row = found["classes"][opclass(int(mem[n]))]
            row[0] += size
            row[1] += 1
        elif f == "check":
            checks["sites" if n - 1 in redundant else "least"] += size
        found["artifacts"][f if f in ARTIFACTS else "unmarked"] += size * (f != "6502")

    def what(f, n):
        if f == "check":
            c = e["checks"][n - 1]
            return f"{c['kind']} at ${c['at']:04X}{' (site)' if n - 1 in redundant else ''}"
        return f"trap({n - 1})" if f == "trap" else f

    largest = sorted(
        ((n, k) for k, n in own.items() if k[0] in ARTIFACTS), reverse=True
    )
    found["artifacts"] = {k: n for k, n in found["artifacts"].items() if n}
    return {
        **found,
        "largest": [[size, what(*k)] for size, k in largest[:TOP]],
        "checks": dict(checks),
        "total": sum(own.values()),
        "original": sizes.original(tune)[0],
        "hand": hand_bytes(tune, out),
    }


def table(found):
    """Markdown rows of `{tune: ownership}`."""
    head = [
        "Tune",
        *(f"{k} B/insn" for k in CLASSES),
        *ARTIFACTS,
        "check sites",
        "total",
    ]
    rows = [
        "| " + " | ".join(head + ["original", "hand"]) + " |",
        "|---" * (len(head) + 2) + "|",
    ]
    for tune, r in found.items():
        per = [f"{b / n:.1f} ({n})" if n else "-" for b, n in r["classes"].values()]
        arts = [str(r["artifacts"].get(k, 0)) for k in ARTIFACTS]
        rest = [
            str(r["checks"].get("sites", 0)),
            str(r["total"]),
            str(r["original"]),
            str(r["hand"]),
        ]
        rows.append("| " + " | ".join([tune.capitalize(), *per, *arts, *rest]) + " |")
    return rows


def main(argv=None):
    """CLI entry point: the byte ownership of each tune's translation."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tunes", nargs="+")
    ap.add_argument("--out", default=OUT, type=pathlib.Path, help="build directory")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    found = {
        t: ownership(t, args.out / t)
        for t in tqdm(args.tunes, desc="ownership", unit="tune")
    }
    print(json.dumps(found, indent=1) if args.json else "\n".join(table(found)))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    raise SystemExit(main())
