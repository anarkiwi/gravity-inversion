"""Judge a lift of a tune against its hand rewrite (docs/lifter-brief.md, *Method per tune*).

liftcheck.py PSID OUT --hand DIR (--frames N | --hvsc ROOT [--times T]): unbounded accesses,
gotos, -Werror, sidtrace under each one's overrun sites, fcomplex and census of the play units,
and play's orbit from each song's state against the lift's bounds and deleted arms.
"""

import argparse
import functools
import json
import os
import pathlib
import subprocess
import sys
import tomllib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
from deity_informant import orbit as orbit_mod
from deity_informant import postinit
from pysidtracker.header import parse_sid_header
from tqdm import tqdm

import fcomplex
import sidtrace
import transliterate
from constructs import ROOT

#: The census measures virtuous-cycles.toml's readability metric combines:
#: {measure: {class: weight}}.
COMBINE = next(
    m["options"]["combine"]
    for m in tomllib.loads((ROOT / "virtuous-cycles.toml").read_text("utf-8"))["metric"]
    if m["name"] == "readability"
)
#: The measures in table order: (key, label).
MEASURES = (
    ("unbounded", "unbounded/checked"),
    ("noset", "unbounded/no set"),
    ("effects", "overruns on original"),
    ("orbit", "orbit"),
    ("goto", "gotos"),
    ("werror", "-Werror"),
    ("sidtrace", "sidtrace"),
    ("cognitive", "fcomplex cognitive"),
    *((k, f"census {k}") for k in COMBINE),
)
#: The suffixes of the units holding play: its entry's and the functions entries share.
PLAY = ("_play.c", "_shared.c")
#: The play calls the orbit check runs from each song's state at most.
ORBIT_CALLS = 20_000


def units(d):
    """The C units of the translation in `d`: its `units` list (resolved in `d` by name), or
    every `.c` there but the runtime's trap."""
    listed = d / "units"
    if listed.exists():
        return [d / pathlib.Path(n).name for n in listed.read_text("utf-8").split()]
    return sorted(p for p in d.glob("*.c") if p.name != "cgen_trap.c")


def play_units(paths):
    """The units of `paths` that hold play."""
    return [p for p in paths if p.name.endswith(PLAY)]


def census(paths, includes):
    """{class: count} summed over the census of `paths` (wayward-eagle)."""
    args = ["wayward-eagle", "census", "--json"]
    args += [x for d in includes for x in ("-I", str(d))]
    r = subprocess.run(
        [*args, *map(str, paths)], capture_output=True, text=True, check=True
    )
    out = {}
    for v in json.loads(r.stdout).values():
        for k, n in v["counts"].items():
            out[k] = out.get(k, 0) + n
    return out


def replay(psid, prg, frames, sites):
    """`OK` or the first mismatch of `prg`'s SID writes against `psid` under `sites`."""
    song = sidtrace.default_song(psid)
    _, writes, bad = sidtrace.compare(psid, str(prg), frames, song, sites)
    if bad is None:
        return f"OK {frames} frames, {writes} writes"
    return f"MISMATCH frame {bad[0]} write {bad[1]}"


def overrun_effects(d, psid, frames, allowed=()):
    """`OK` or the overrun sites of the translation in `d` that take effect when `psid` plays
    with no overrun rule, other than those of `allowed` objects (sidtrace.read_allowed).
    """
    ov = pathlib.Path(d) / "overruns.json"
    sites = json.loads(ov.read_text("utf-8")) if ov.exists() else []
    song = sidtrace.default_song(psid)
    bad = sidtrace.unallowed(
        sites, sidtrace.effects(psid, frames, sites, song), allowed
    )
    if not bad:
        return "OK"
    first = min(f for _, f, _ in bad)
    return f"{len(bad)} sites from frame {first}: {_pcs(s['pc'] for s, _, _ in bad)}"


def measure(d, psid, frames):
    """{measure: value} of the translation in directory `d` replayed against `psid`."""
    d = pathlib.Path(d)
    paths = units(d)
    out = {}
    try:
        prg = transliterate.compile_prg(paths, flags=("-Wall", "-Wextra", "-Werror"))
        out["werror"] = "clean"
    except subprocess.CalledProcessError:
        out["werror"] = "FAILS"
        prg = transliterate.compile_prg(paths)
    ov = d / "overruns.json"
    sites = json.loads(ov.read_text("utf-8")) if ov.exists() else ()
    out["sidtrace"] = replay(psid, prg, frames, sites)
    play = play_units(paths)
    out["cognitive"] = fcomplex.score(play)["total"]["cognitive"]
    counts = census(play, [d, transliterate.DRIVER])
    out["goto"] = counts.get("goto", 0)
    for k, w in COMBINE.items():
        out.update({c: counts.get(c, 0) for c in w})
        out[k] = sum(n * out[c] for c, n in w.items())
    return out


def song_states(psid):
    """(play's PC, the distinct states init leaves for the songs of `psid`) on the machine
    tools/transliterate.py lifts from."""
    _, init, play, _ = sidtrace.psid_parts(psid)
    songs = range(parse_sid_header(psid).songs)
    machine = functools.partial(
        transliterate.c64_init, sid_model=sidtrace.header_model(psid)
    )
    got = postinit.run(
        bytes(sidtrace.boot_ram(psid)), init, songs, transliterate.VOLATILE, machine
    )
    return play, sorted({bytes(s) for s in got.states})


def violations(accesses, succ, sites, tables, starts):
    """(PCs of `accesses` {(pc, store): addresses} outside every overrun site's hull and
    table extent their instruction has, PCs of `succ` {pc: successors} followed by a PC
    the lift has no instruction at)."""
    spans = {}
    for s in sites:
        spans.setdefault(s["pc"], []).append(s["addr"])
    for pc, lo, hi, *_ in tables:
        spans.setdefault(pc, []).append((lo, hi))
    out = set()
    for (pc, _), addrs in accesses.items():
        if pc in spans:
            a = np.asarray(addrs)[:, None]
            lo, hi = np.asarray(spans[pc]).T
            if not ((a >= lo) & (a <= hi)).any(axis=1).all():
                out.add(pc)
    lifted = set(starts)
    arms = {pc for pc, ss in succ.items() if not lifted.issuperset(ss)}
    return sorted(out), sorted(arms)


def walks(states, play, sites, calls):
    """deity-informant's orbit walk of play at `play` from each of `states` under the overrun
    `sites`, at most `calls` calls each: one thread per state (the native core runs without
    the GIL)."""
    walk = functools.partial(
        orbit_mod.walk,
        pc=play,
        volatile=transliterate.VOLATILE,
        limit=calls,
        sites=sites,
    )
    with ThreadPoolExecutor(min(len(states), os.cpu_count() or 1)) as pool:
        return list(
            tqdm(
                pool.map(walk, states),
                total=len(states),
                desc="orbit",
                unit="song",
                leave=False,
                disable=None,
            )
        )


def check_orbit(d, psid, calls=ORBIT_CALLS):
    """`OK` and the calls play's orbits from each song's state ran under the lift in `d`'s
    overrun sites, or the PCs whose accesses leave its bounds and the branches that take an
    arm it deleted."""
    d = pathlib.Path(d)
    sites = json.loads((d / "overruns.json").read_text("utf-8"))
    bounds = json.loads((d / "bounds.json").read_text("utf-8"))
    play, states = song_states(psid)
    bad, arms, ran, ends = set(), set(), 0, {}
    for how, n, got, succ in walks(states, play, sites, calls):
        b, a = violations(got, succ, sites, bounds["tables"], bounds["starts"])
        bad.update(b)
        arms.update(a)
        ran += n - (how == "open")
        ends[how] = ends.get(how, 0) + 1
    if bad or arms:
        return f"VIOLATED accesses {_pcs(bad)} arms {_pcs(arms)}"
    rest = ", ".join(f"{n} {how}" for how, n in sorted(ends.items()) if how != "closed")
    return f"OK {ran} calls" + (f" ({rest})" if rest else "")


def _pcs(pcs):
    """`pcs` as `$XXXX` joined by commas, `-` for none."""
    return ",".join(f"${x:04X}" for x in sorted(pcs)) or "-"


def lift(psid, out):
    """Translate `psid` into directory `out` as tools/transliterate.py does; the counts of
    unbounded accesses, all and those of no address set (the rest a memory-map check into
    a block), each None when the lifter does not report them."""
    out.mkdir(parents=True, exist_ok=True)
    header = "tune.h"
    c, h, info = transliterate.transliterate(psid, header, split=True)
    paths = transliterate.write(out, c, h, header)
    (out / "units").write_text("".join(f"{p}\n" for p in paths), "utf-8")
    (out / "overruns.json").write_text(json.dumps(info["overruns"]), "utf-8")
    bounds = {"tables": info.get("tables", []), "starts": info["starts"]}
    (out / "bounds.json").write_text(json.dumps(bounds), "utf-8")
    found = info.get("unbounded")
    if found is not None:
        (out / "unbounded.json").write_text(json.dumps(found), "utf-8")
    if found is None:
        return None, None
    return len(found), sum(why == "no set" for _, why, *_ in found)


def cell(got, k):
    """Measure `k` of `got` as text, a combined measure's weighted components beside it."""
    v = str(got.get(k, "-"))
    if k not in COMBINE or k not in got:
        return v
    return f"{v} = " + " + ".join(f"{n}*{got[c]} {c}" for c, n in COMBINE[k].items())


def table(got, hand):
    """The rows of measures `got` beside `hand` as text."""
    w = max(len(label) for _, label in MEASURES)
    rows = [f"{'':{w}}  {'lift':<34}  hand"]
    rows += [
        f"{label:{w}}  {cell(got, k):<34}  {cell(hand, k)}" for k, label in MEASURES
    ]
    return "\n".join(rows)


def main(argv=None):
    """CLI: lift, measure the lift and the hand rewrite in parallel, print the table."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("psid", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path, help="directory the lift is written to")
    ap.add_argument("--hand", type=pathlib.Path, help="the hand rewrite's directory")
    end = ap.add_mutually_exclusive_group(required=True)
    end.add_argument("--frames", type=int)
    end.add_argument("--hvsc", help="HVSC root: the song's length")
    ap.add_argument("--times", type=int, default=1, help="multiple of the length")
    ap.add_argument("--json", type=pathlib.Path, help="write both measures here")
    ap.add_argument(
        "--allowed",
        help="objects whose overrun sites may take effect on the original (JSON, as make "
        "check's overruns_allowed.json)",
    )
    ap.add_argument(
        "--orbit-calls", type=int, default=ORBIT_CALLS, help="play calls per song state"
    )
    args = ap.parse_args(argv)
    if args.json:
        args.json.unlink(missing_ok=True)
    psid = args.psid.read_bytes()
    song = sidtrace.default_song(psid)
    frames = args.frames or sidtrace.song_calls(psid, song, args.hvsc)
    frames *= args.times
    progress = tqdm(total=4 if args.hand else 3, desc="liftcheck", unit="step")
    with ProcessPoolExecutor(3) as pool:
        hand = pool.submit(measure, args.hand, psid, frames) if args.hand else None
        got = dict(zip(("unbounded", "noset"), lift(psid, args.out)))
        progress.update()
        orbit = pool.submit(check_orbit, args.out, psid, args.orbit_calls)
        allowed = sidtrace.read_allowed(args.allowed)
        effects = pool.submit(overrun_effects, args.out, psid, frames, allowed)
        got.update(measure(args.out, psid, frames))
        progress.update()
        got["orbit"] = orbit.result()
        got["effects"] = effects.result()
        progress.update()
        ref = {"unbounded": 0, "noset": 0, **hand.result()} if hand else {}
        progress.update(bool(hand))
    progress.close()
    print(table(got, ref))
    if args.json:
        args.json.write_text(json.dumps({"lift": got, "hand": ref}), "utf-8")
    ok = all(got[k].startswith("OK") for k in ("sidtrace", "orbit", "effects"))
    return 0 if ok and got["werror"] == "clean" and not got["noset"] else 1


if __name__ == "__main__":
    sys.exit(main())
