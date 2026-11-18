"""Ghidra's decompilation of each target as reference C, and the readability census of it
beside ours (docs/ghidra_ref.md).

``python tools/ghidra_ref.py [targets...] [--out out/ghidra] [-j N]``: Ghidra headless (6510
SLEIGH from deity-informant) seeded at the PSID's init and play and the lifter's entries,
then auto-analysis; every function's C, one combined C file over ``ghidra.h``, a JSON index.
``--compare [--ours LABEL=DIR]``: census counts of Ghidra's C and ours, matched by entry.
"""

import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from tqdm import tqdm
from wayward_eagle.census import COGNITIVE, census

import ghidra_types
from constructs import ROOT
from residue import FUZZ, TUNES, target
from sidtrace import psid_parts
from transliterate import transliterate

OUT = ROOT / "out" / "ghidra"
DOCKERFILE = ROOT / "docker" / "ghidra.Dockerfile"
SCRIPTS = ROOT / "tools" / "ghidra"
REQUIREMENTS = ROOT / "requirements.txt"
IMAGE = "gi-ghidra"
PROCESSOR = "6510:LE:16:default"
GHIDRA = "/opt/ghidra"
HEADER = "ghidra.h"
TIMEOUT = 3600
OURS = "ours"
C64 = ROOT / "c64"
PSID, LIFTER, FOUND = "psid", "lifter", "ghidra"


def pin(requirements=REQUIREMENTS):
    """The deity-informant version pinned in ``requirements``."""
    text = pathlib.Path(requirements).read_text(encoding="utf-8")
    return re.search(r"^deity-informant==(\S+)", text, re.M).group(1)


def image_tag(version=None):
    """The image tag of a deity-informant version (default: GI's pin)."""
    return f"{IMAGE}:{version or pin()}"


def ensure_image(tag):
    """Build image ``tag`` from :data:`DOCKERFILE` unless docker already has it."""
    have = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=False
    )
    if have.returncode:
        version = tag.rsplit(":", 1)[1]
        subprocess.run(
            ["docker", "build", "-f", str(DOCKERFILE), "-t", tag]
            + ["--build-arg", f"DEITY_INFORMANT={version}", str(DOCKERFILE.parent)],
            check=True,
        )
    return tag


def provenance(info):
    """``{name: {"entry", "pcs"}}`` of the lifter's functions from its ``info``: each one's
    original entry and the addresses of the instructions it translates."""
    return {
        n: {"entry": f["entry"], "pcs": f["pcs"]}
        for n, f in info["provenance"]["functions"].items()
    }


def lifter_entries(psid):
    """Original entry addresses of the lifter's functions of ``psid``."""
    prov = provenance(transliterate(psid, "x.h")[2])
    return sorted({f["entry"] for f in prov.values()})


def seeds(psid, entries):
    """``{address: seed}``: the PSID's init and nonzero play are :data:`PSID`, the other
    ``entries`` :data:`LIFTER`."""
    _, init, play, _ = psid_parts(psid)
    out = dict.fromkeys(entries, LIFTER)
    out.update(dict.fromkeys((a for a in (init, play) if a), PSID))
    return out


def headless(work, load, addresses, scripts="/scripts"):
    """The ``analyzeHeadless`` argv over work dir ``work`` (``image.bin`` in, ``dump.json``
    out) of the image loaded at ``load``, seeded at ``addresses``."""
    return [
        f"{GHIDRA}/support/analyzeHeadless",
        work,
        "ref",
        "-import",
        f"{work}/image.bin",
        "-loader",
        "BinaryLoader",
        "-loader-baseAddr",
        f"0x{load:04X}",
        "-processor",
        PROCESSOR,
        "-scriptPath",
        scripts,
        "-preScript",
        "GhidraRefSeed.java",
        *(f"{a:04X}" for a in sorted(addresses)),
        "-postScript",
        "GhidraRefDecompile.java",
        f"{work}/dump.json",
        "-deleteProject",
    ]


def run_ghidra(psid, addresses, work, tag):
    """Ghidra's dump (GhidraRefDecompile.java) of ``psid`` seeded at ``addresses``, run in
    image ``tag`` over the fresh directory ``work``."""
    load, _, _, image = psid_parts(psid)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        (work / "image.bin").write_bytes(image[: 0x10000 - load])
        argv = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
        argv += [
            "-v",
            f"{work.resolve()}:/work",
            "-v",
            f"{SCRIPTS.resolve()}:/scripts:ro",
        ]
        argv += ["-e", "HOME=/work", "-e", "JAVA_TOOL_OPTIONS=-Duser.home=/work", tag]
        r = subprocess.run(
            argv + headless("/work", load, addresses),
            capture_output=True,
            encoding="latin-1",
            timeout=TIMEOUT,
            check=False,
        )
        dump = work / "dump.json"
        if r.returncode or not dump.exists():
            raise RuntimeError(
                f"analyzeHeadless failed ({r.returncode}):\n{r.stdout[-4000:]}"
            )
        return json.loads(dump.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def externs(functions):
    """``extern`` declarations of what the decompiled ``functions`` name undeclared, by
    name: of each its widest typing by Ghidra, one called through first, then by text.
    """
    decl = {}
    for f in functions:
        for name, d, size, call in f.get("globals", ()):
            decl.setdefault(name, []).append((-size, not call, d))
    return [f"extern {min(decl[n])[2]};" for n in sorted(decl)]


def combined(dump):
    """One C file of a dump: ``ghidra.h``, Ghidra's composite types, the globals, every
    function's prototype, then every decompiled function's C with the token rewrites that
    make it parse (GhidraRefDecompile.java)."""
    fs = dump["functions"]
    parts = [f'#include "{HEADER}"', dump["types"].strip()]
    parts += externs(fs) + [f["signature"] for f in fs]
    parts += [f["body"].strip() + "\n" for f in fs if f["ok"]]
    return "\n".join(p for p in parts if p) + "\n"


def index(dump, seeded):
    """The JSON index of a dump: per function its entry, seed, callees and whether it
    decompiled; and the ``seeded`` addresses Ghidra holds no function at."""
    have = {f["entry"] for f in dump["functions"]}
    return {
        "functions": {
            f["name"]: {
                "entry": f["entry"],
                "seed": seeded.get(f["entry"], FOUND),
                "calls": f["calls"],
                "pcs": f["pcs"],
                "ok": f["ok"],
                "error": f["error"],
            }
            for f in dump["functions"]
        },
        "unseeded": sorted(set(seeded) - have),
    }


def write(name, dump, seeded, out=OUT):
    """Write a target's per-function C, combined C and JSON index under ``out``."""
    out = pathlib.Path(out)
    shutil.rmtree(out / name, ignore_errors=True)
    (out / name).mkdir(parents=True)
    for f in dump["functions"]:
        if f["ok"]:
            (out / name / f"{f['entry']:04x}.c").write_text(f["c"], encoding="utf-8")
    (out / f"{name}.c").write_text(combined(dump), encoding="utf-8")
    js = index(dump, seeded)
    (out / f"{name}.json").write_text(json.dumps(js, indent=1) + "\n", encoding="utf-8")
    return js


def write_header(out=OUT):
    """Write :func:`ghidra_types.header` as ``out/ghidra.h``."""
    path = pathlib.Path(out) / HEADER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ghidra_types.header(), encoding="utf-8")
    return path


def generate(name, out=OUT, tag=None):
    """Decompile target ``name`` with Ghidra and write its outputs; its JSON index."""
    psid = target(name)[0]
    seeded = seeds(psid, lifter_entries(psid))
    work = pathlib.Path(out) / ".work" / pathlib.Path(name).stem
    dump = run_ghidra(psid, seeded, work, tag or image_tag())
    return write(pathlib.Path(name).stem, dump, seeded, out)


def counts(js):
    """``(found, decompiled, failed)`` function counts of a JSON index."""
    ok = sum(f["ok"] for f in js["functions"].values())
    return len(js["functions"]), ok, len(js["functions"]) - ok


def generate_all(names, out=OUT, jobs=8, tag=None):
    """:func:`generate` over ``names`` in ``jobs`` processes; ``{name: index}``."""
    tag = ensure_image(tag or image_tag())
    write_header(out)
    done = {}
    with concurrent.futures.ProcessPoolExecutor(jobs) as ex:
        futs = {ex.submit(generate, n, out, tag): n for n in names}
        progress = tqdm(
            concurrent.futures.as_completed(futs), total=len(futs), unit="target"
        )
        for fut in progress:
            done[futs[fut]] = fut.result()
            progress.set_postfix_str(futs[fut])
    return {n: done[n] for n in names}


def report(name, js):
    """Lines of a target's counts, failures and seeds without a function."""
    found, ok, failed = counts(js)
    lines = [f"{name}: {found} functions, {ok} decompiled, {failed} failed"]
    lines += [
        f"  {fn} @{f['entry']:04X}: {f['error']}"
        for fn, f in js["functions"].items()
        if not f["ok"]
    ]
    return lines + [f"  seed {a:04X}: no function" for a in js["unseeded"]]


# Comparison.


def owner(name, functions):
    """The lifter function ``name`` comes from: itself, or the longest name of ``functions``
    it extends by ``_<suffix>`` (the reducer's extracted and split pieces); else None.
    """
    found = [f for f in functions if name == f or name.startswith(f + "_")]
    return max(found, key=len) if found else None


def units(js, entries):
    """``{entry: Ghidra function names}``: for each of ``entries`` Ghidra holds a function
    at, it and every function it reaches by calls through functions at none of ``entries``
    (those the lifter inlined)."""
    at = {f["entry"]: n for n, f in js["functions"].items()}
    out = {}
    for e in sorted(set(entries) & set(at)):
        seen, todo = {at[e]}, [at[e]]
        while todo:
            for c in js["functions"][todo.pop()]["calls"]:
                if c in at and c not in entries and at[c] not in seen:
                    seen.add(at[c])
                    todo.append(at[c])
        out[e] = seen
    return out


def defined(rep):
    """The functions of census report ``rep`` with a site or a cognitive complexity."""
    return set(rep.complexity) | {s.function for s in rep.sites if s.function}


def tally(rep, names):
    """Sites per class of the functions ``names`` of census report ``rep``, their cognitive
    complexity under :data:`COGNITIVE`."""
    out = {}
    for s in rep.sites:
        if s.function in names and s.cls != COGNITIVE:
            out[s.cls] = out.get(s.cls, 0) + 1
    out[COGNITIVE] = sum(v for f, v in rep.complexity.items() if f in names)
    return out


def compare(js, ghidra, ours):
    """Ghidra's census report ``ghidra`` (of index ``js``) beside each of ``ours``
    (``{label: (report, provenance)}``), in total and per original entry.

    ``{"total": {label: {class: n}}, "entries": {entry: {label: [functions], "counts":
    {label: {class: n}}, "missing": {label: n}}}, "better": [(label, entry, class, Ghidra's,
    ours)], "unmatched": {label: [functions]}}``. ``missing`` counts the instructions our
    functions at an entry translate that Ghidra's there do not hold; ``better`` lists, where
    none are missing, each class Ghidra's C has fewer sites of."""
    total = {"ghidra": tally(ghidra, defined(ghidra))}
    total.update({k: tally(r, defined(r)) for k, (r, _) in ours.items()})
    mine = {
        k: {f: owner(f, prov) for f in defined(rep)} for k, (rep, prov) in ours.items()
    }
    starts = {f["entry"] for _, prov in ours.values() for f in prov.values()}
    entries, better = {}, []
    for e, names in units(js, starts).items():
        held = {pc for n in names for pc in js["functions"][n]["pcs"]}
        row = {"ghidra": sorted(names), "counts": {"ghidra": tally(ghidra, names)}}
        row["missing"] = {}
        for k, (rep, prov) in ours.items():
            bases = {o for o in mine[k].values() if o and prov[o]["entry"] == e}
            own = {f for f, o in mine[k].items() if o in bases}
            row[k] = sorted(own)
            row["counts"][k] = got = tally(rep, own)
            gone = {pc for o in bases for pc in prov[o]["pcs"]} - held
            row["missing"][k] = len(gone)
            better += [
                (k, e, c, row["counts"]["ghidra"].get(c, 0), n)
                for c, n in sorted(got.items())
                if own and not gone and row["counts"]["ghidra"].get(c, 0) < n
            ]
        entries[e] = row
    unmatched = {
        k: sorted(f for f, o in m.items() if o is None) for k, m in mine.items()
    }
    return {
        "total": total,
        "entries": entries,
        "better": better,
        "unmatched": unmatched,
    }


def ours_c(name, out):
    """Write the lifter's C of target ``name`` to ``out``/<name>.c and .h, and its
    :func:`provenance` to <name>.provenance.json; the directory."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c, h, info = transliterate(target(name)[0], f"{name}.h")
    (out / f"{name}.h").write_text(h, encoding="utf-8")
    (out / f"{name}.c").write_text(c, encoding="utf-8")
    (out / f"{name}.provenance.json").write_text(
        json.dumps(provenance(info)) + "\n", encoding="utf-8"
    )
    return out


def compare_target(name, ours, out=OUT):
    """:func:`compare` of target ``name``. ``ours`` maps labels to directory templates
    (``{target}`` the target's name) holding <name>.c and <name>.provenance.json (the
    :func:`provenance` of the lift that made it); when empty, the lifter's C is written to
    ``out``/ours."""
    out = pathlib.Path(out)
    js = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
    dirs = {k: pathlib.Path(v.format(target=name)) for k, v in ours.items()}
    dirs = dirs or {OURS: ours_c(name, out / OURS)}
    reps = {
        k: (
            census(d / f"{name}.c", (d, C64)),
            json.loads((d / f"{name}.provenance.json").read_text(encoding="utf-8")),
        )
        for k, d in dirs.items()
    }
    return compare(js, census(out / f"{name}.c", (out,)), reps)


def compare_all(names, ours, out=OUT, jobs=8):
    """:func:`compare_target` over ``names`` in ``jobs`` processes; ``{name: comparison}``."""
    done = {}
    with concurrent.futures.ProcessPoolExecutor(jobs) as ex:
        futs = {ex.submit(compare_target, n, ours, out): n for n in names}
        progress = tqdm(
            concurrent.futures.as_completed(futs), total=len(futs), unit="target"
        )
        for fut in progress:
            done[futs[fut]] = fut.result()
            progress.set_postfix_str(futs[fut])
    return {n: done[n] for n in names}


def compare_lines(name, got):
    """Lines of a comparison: totals per class, then where Ghidra's C is better."""
    labels = list(got["total"])
    classes = sorted({c for t in got["total"].values() for c in t})
    lines = [f"{name}: " + " | ".join(labels)]
    lines += [
        f"  {c}: " + " | ".join(str(got["total"][k].get(c, 0)) for k in labels)
        for c in classes
    ]
    for e, row in got["entries"].items():
        lines += [
            f"  @{e:04X} Ghidra's C lacks {n} instructions of {k}'s [{', '.join(row[k])}]"
            for k, n in row["missing"].items()
            if n
        ]
    for label, e, c, g, n in got["better"]:
        fns = ", ".join(got["entries"][e][label])
        lines.append(f"  better than {label} @{e:04X} [{fns}] {c}: {g} < {n}")
    return lines


def main(argv=None):
    """Generate the targets' reference C (all tunes and fuzz targets by default)."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="*", help="tune, fuzz target or PSID path")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument(
        "--image", help="image tag (default: gi-ghidra:<deity-informant pin>)"
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="census Ghidra's C beside ours (from earlier generation) instead",
    )
    ap.add_argument(
        "--ours",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="our C and provenance in DIR ({target} expands; default: the lifter's, generated)",
    )
    args = ap.parse_args(argv)
    names = args.targets or TUNES + FUZZ
    if args.compare:
        ours = dict(o.split("=", 1) for o in args.ours)
        got = compare_all(names, ours, args.out, args.jobs)
        (args.out / "compare.json").write_text(
            json.dumps(got, indent=1) + "\n", encoding="utf-8"
        )
        for name, row in got.items():
            print("\n".join(compare_lines(name, row)))
        return 0
    for name, js in generate_all(names, args.out, args.jobs, args.image).items():
        print("\n".join(report(name, js)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
