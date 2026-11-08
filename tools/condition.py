"""Lift one tune per player family, condition the C by hand where the lift still falls short,
check its SID writes against the original and write its value-flow graphs (docs/flowgraph.md).
"""

import argparse
import ast
import json
import operator
import os
import pathlib
import re
import subprocess
import sys

from tqdm import tqdm

import flowgraph
import flowsegment
import sidtrace
import transliterate

ROOT = pathlib.Path(__file__).resolve().parent.parent
HVSC = pathlib.Path(os.environ.get("HVSC", "/scratch/datasets/hvsc/C64Music"))
#: ``{family: (tune, conditioning)}``: a tune is HVSC-relative or under fixtures/.
FAMILIES = {
    "goattracker": ("DEMOS/0-9/10_Orbyte.sid", ()),
    "defmon": ("DEMOS/A-F/Fading_Lights.sid", ()),
    "dmc": ("DEMOS/0-9/2Pac_Intro.sid", ()),
    "jch": ("DEMOS/0-9/1st_Chaff.sid", ()),
    "futurecomposer": ("MUSICIANS/A/A-Man/Future_Composer_4-Demo_07.sid", ()),
    "sidwizard": ("Emomyst.sid", ()),
    "musicassembler": ("DEMOS/0-9/7_Years.sid", ("direct",)),
    "soundmonitor": ("DEMOS/0-9/1988_Carat_tune_1.sid", ()),
}
ORG = re.compile(r"#define (\w+)_ORG 0x([0-9A-F]+)")
OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}


def origins(h):
    """``{span: origin}`` of a generated .h."""
    return {m[1]: int(m[2], 16) for m in ORG.finditer(h)}


def calls(text, name):
    """``(start, end, [argument text])`` of each call of ``name`` in C ``text``."""
    for m in re.finditer(rf"(?<![\w]){name}\(", text):
        i, depth, args, start = m.end(), 1, [], m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            if text[i] == "," and depth == 1:
                args.append(text[start:i])
                start = i + 1
            i += 1
        args.append(text[start : i - 1])
        yield m.start(), i, [a.strip() for a in args]


def base(expr, org):
    """The address of C expression ``expr`` with every variable 0 and spans at ``org``."""
    expr = re.sub(r"\(uint(?:8|16)_t\)", "", expr)

    def ev(n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return org.get(n.id[:-4], 0) if n.id.endswith("_ORG") else 0
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError(ast.dump(n))

    return ev(ast.parse(expr, mode="eval").body) & 0xFFFF


def direct(c, h):
    """``(c, stores, checks)``: init's and play's ``wr(a, v)`` as stores into the code span
    holding ``a`` with every variable 0, ``codechk(...)`` as 0; for an index register that
    reaches its stores through stack-chained calls, its range lost (deity #455)."""
    org = origins(h)
    spans = sorted((o, k) for k, o in org.items() if re.fullmatch(r"C\d+", k))
    cut = c.index("void cgen_init")
    head, body = c[:cut], c[cut:]
    out, last, n = [], 0, 0
    for s, e, (a, v) in calls(body, "wr"):
        span = [k for o, k in spans if o <= base(a, org)][-1]
        out += [body[last:s], f"{span}[(uint16_t)({a}) - {span}_ORG] = {v}"]
        last, n = e, n + 1
    body = "".join(out) + body[last:]
    body, k = re.subn(r"(?<![\w])codechk\([^()]*(?:\([^()]*\)[^()]*)*\)", "0", body)
    return head + body, n, k


def tune_path(tune):
    """The file of a FAMILIES tune."""
    fix = ROOT / "fixtures" / tune
    return fix if fix.exists() else HVSC / tune


def run(family, out, frames=None):
    """Lift, condition, build and trace ``family``'s tune into ``out``/family: graph.* the
    whole program's value flow, machines.* play's state machines, row.json the results.
    """
    tune, steps = FAMILIES[family]
    psid = tune_path(tune).read_bytes()
    d = pathlib.Path(out) / family
    d.mkdir(parents=True, exist_ok=True)
    c, h, _ = transliterate.transliterate(psid, "gen.h")
    row = {"family": family, "tune": tune}
    if "direct" in steps:
        c, row["wr"], row["codechk"] = direct(c, h)
    (d / "gen.c").write_text(c, encoding="utf-8")
    (d / "gen.h").write_text(h, encoding="utf-8")
    prg = transliterate.compile_prg(d)
    song = sidtrace.default_song(psid)
    frames = frames or sidtrace.song_calls(psid, song, str(HVSC))
    try:
        _, _, bad = sidtrace.compare(psid, str(prg), frames, song)
        row["trace"] = f"exact {frames}" if not bad else f"differs {str(bad)[:60]}"
    except AssertionError as e:
        row["trace"] = str(e)[:80]
    g, summary = flowgraph.graph(d / "gen.c")
    row["residual"] = summary["residual_calls"]
    _write(d / "graph", g)
    m = flowsegment.segment(d / "gen.c")
    _write(d / "machines", m)
    row["machines"] = flowsegment.summary(m)
    row["roles"] = {n: list(r) for n, r in flowsegment.roles(m).items()}
    (d / "row.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
    return row


def _write(stem, g):
    stem.with_suffix(".dot").write_text(flowgraph.dot(g), encoding="utf-8")
    png = stem.with_suffix(".png")
    subprocess.run(["dot", "-Tpng", "-o", png, stem.with_suffix(".dot")], check=True)


def main(argv=None):
    """CLI: run the named families (default all), one JSON row each."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("family", nargs="*", help=f"of {', '.join(FAMILIES)} (default all)")
    ap.add_argument("--out", default=str(ROOT / "build" / "flow"))
    ap.add_argument(
        "--frames", type=int, help="play calls traced (default: song length)"
    )
    args = ap.parse_args(argv)
    unknown = set(args.family) - set(FAMILIES)
    if unknown:
        ap.error(f"unknown family {sorted(unknown)}")
    for fam in tqdm(args.family or list(FAMILIES), desc="families", unit="tune"):
        tqdm.write(json.dumps(run(fam, args.out, args.frames)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
