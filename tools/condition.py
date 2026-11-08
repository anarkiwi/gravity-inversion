"""Lift one tune per player family, check its SID writes against the original and write its
value-flow graphs (docs/flowgraph.md).
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

from tqdm import tqdm

import flowgraph
import flowsegment
import sidtrace
import transliterate

ROOT = pathlib.Path(__file__).resolve().parent.parent
HVSC = pathlib.Path(os.environ.get("HVSC", "/scratch/datasets/hvsc/C64Music"))
#: ``{family: tune}``: a tune is HVSC-relative or under fixtures/.
FAMILIES = {
    "goattracker": "DEMOS/0-9/10_Orbyte.sid",
    "defmon": "DEMOS/A-F/Fading_Lights.sid",
    "dmc": "DEMOS/0-9/2Pac_Intro.sid",
    "jch": "DEMOS/0-9/1st_Chaff.sid",
    "futurecomposer": "MUSICIANS/A/A-Man/Future_Composer_4-Demo_07.sid",
    "sidwizard": "Emomyst.sid",
    "musicassembler": "DEMOS/0-9/7_Years.sid",
    "soundmonitor": "DEMOS/0-9/1988_Carat_tune_1.sid",
}


def tune_path(tune):
    """The file of a FAMILIES tune."""
    fix = ROOT / "fixtures" / tune
    return fix if fix.exists() else HVSC / tune


def run(family, out, frames=None):
    """Lift, build and trace ``family``'s tune into ``out``/family: graph.* the whole
    program's value flow, machines.* play's state machines, row.json the results."""
    tune = FAMILIES[family]
    psid = tune_path(tune).read_bytes()
    d = pathlib.Path(out) / family
    d.mkdir(parents=True, exist_ok=True)
    c, h, _ = transliterate.transliterate(psid, "gen.h")
    row = {"family": family, "tune": tune}
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
