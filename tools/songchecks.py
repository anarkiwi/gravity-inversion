"""The checks of generated songs: certificates against brute force, coverage against songs.

Check A certifies play from each song state and runs the plain orbit to closure; check B
compares the code the real songs of one playroutine run with what forcing and walking back
reach from one plain song; `certify` times certificates by size class (docs/gensongs.md).
"""

import contextlib
from multiprocessing import get_context
import json
import os
import signal
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from jennings import memo
from jennings.coverage import certificate as V
from jennings.coverage import components as C
from tqdm import tqdm

import forced
import liftcheck
import sidtrace
import transliterate
import walkback

#: The calls certify may take, by size class.
CALLS = {"tiny": 20_000, "small": 100_000, "medium": 500_000, "large": 2_000_000}
#: The CPU seconds certify may take, by size class.
SECONDS = {"tiny": 120, "small": 180, "medium": 300, "large": 300}
#: The calls the plain orbit may run, by size class.
ORBIT = {"tiny": 200_000, "small": 1_000_000, "medium": 2_000_000, "large": 2_000_000}
#: The access and edge keys a finding lists of each difference at most.
SHOWN = 64


@contextlib.contextmanager
def cpu_limit(seconds):
    """Raise TimeoutError in the block once this process has used `seconds` of CPU."""

    def expire(*_):
        raise TimeoutError(f"{seconds} CPU seconds")

    old = signal.signal(signal.SIGPROF, expire)
    signal.setitimer(signal.ITIMER_PROF, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, old)


def certify(state, play, calls, seconds):
    """(record, certificate or None) of certifying and verifying play from `state` within
    `calls` calls and `seconds` CPU seconds, as `forced.proven` certifies it."""
    exclude = [(C.STACK.start, C.STACK.stop), *transliterate.VOLATILE]
    start, cpu = time.time(), time.process_time()
    try:
        with cpu_limit(seconds):
            cert = V.certify(
                state, play, calls, max_cycles=sidtrace.MAX_CYCLES, exclude=exclude
            )
    except TimeoutError:
        cert = None
    out = {
        "form": "timeout" if cert is None else type(cert).__name__,
        "seconds": round(time.time() - start, 3),
        "cpu": round(time.process_time() - cpu, 3),
        "limit": [calls, seconds],
    }
    if cert is None or isinstance(cert, V.Open):
        out["calls"] = None if cert is None else cert.calls
        return out, None
    verdict = V.verify(state, cert)
    out |= {
        "calls": cert.calls,
        "verified": verdict.ok and not verdict.assumed,
        "failed": verdict.failed,
    }
    return out, cert


def orbit(state, play, limit):
    """(end, calls, access keys, edge keys, seconds) of play's plain orbit from `state`:
    the registers, the stack page and every address a call stores to are the state, the
    stores found by running it until no call stores to a new one."""
    start = time.time()
    stores = np.arange(C.STACK.start, C.STACK.stop)
    while True:
        end, states, acc, edges, _ = memo.orbit(
            C.machine(state),
            play,
            0,
            sidtrace.MAX_CYCLES,
            limit,
            cells=[*stores.tolist(), *V.REGISTERS],
        )
        acc = np.asarray(acc, np.int64)
        new = np.setdiff1d(acc[acc >> 16 & 1 == 1] & 0xFFFF, stores)
        if not new.size:
            break
        stores = np.union1d(stores, new)
    calls = states - (end == "open")
    return end, calls, acc, np.asarray(edges, np.int64), time.time() - start


def _keys(keys, access):
    """Readable `keys`: ``[pc, "load"|"store", addr]`` or ``[pc, next]``."""
    keys = np.asarray(sorted(keys), np.int64)[:SHOWN]
    if access:
        kinds = np.where(keys >> 16 & 1, "store", "load")
        return [[int(k >> 17), str(s), int(k & 0xFFFF)] for k, s in zip(keys, kinds)]
    return [[int(k >> 16), int(k & 0xFFFF)] for k in keys]


def compare(cert, acc, edges):
    """{name: keys} of the accesses and edges the certificate and the orbit do not share."""
    mine = (
        {
            pc << 17 | (kind == C.STORE) << 16 | a
            for (pc, kind), addrs in cert.accesses.items()
            for a in addrs
        },
        {a << 16 | b for a, b in cert.edges},
    )
    theirs = (set(acc.tolist()), set(edges.tolist()))
    return {
        "orbit_accesses": _keys(theirs[0] - mine[0], True),
        "orbit_edges": _keys(theirs[1] - mine[1], False),
        "certificate_accesses": _keys(mine[0] - theirs[0], True),
        "certificate_edges": _keys(mine[1] - theirs[1], False),
    }


def check_a(path, size):
    """Check A of the PSID at `path`: per song state, its certificate and verdict, the
    plain orbit to closure, and whether the two claim the same accesses and edges."""
    play, states = liftcheck.song_states(path.read_bytes())
    out = []
    for k, state in enumerate(states):
        rec, cert = certify(state, play, CALLS[size], SECONDS[size])
        end, calls, acc, edges, secs = orbit(state, play, ORBIT[size])
        rec |= {"state": k, "orbit": end, "orbit_calls": int(calls)}
        rec["orbit_seconds"] = round(secs, 3)
        if cert is not None:
            diff = compare(cert, acc, edges)
            rec["equal"] = not any(diff.values())
            if not rec["equal"]:
                rec["diff"] = diff
        out.append(rec)
    return out


def certify_only(path, size):
    """The certify records of every song state of the PSID at `path`."""
    play, states = liftcheck.song_states(path.read_bytes())
    return [
        certify(s, play, CALLS[size], SECONDS[size])[0] | {"state": k}
        for k, s in enumerate(states)
    ]


#: The thread count each numeric library gets in a pool worker.
THREADS = walkback.THREADS


def _worker():
    """Pool worker setup: the numeric libraries' threads."""
    os.environ.update(THREADS)


def run(job, songs, directory, jobs):
    """{name: job(path, size)} over the manifest `songs` in `directory`, in a pool of
    `jobs` processes."""
    out = {}
    with ProcessPoolExecutor(jobs, get_context("spawn"), _worker) as pool:
        futures = {
            pool.submit(job, directory / f"{s['name']}.sid", s["size"]): s["name"]
            for s in songs
        }
        for f in tqdm(as_completed(futures), total=len(futures), desc=job.__name__):
            out[futures[f]] = f.result()
    return dict(sorted(out.items()))


def load():
    """The host's load averages."""
    return [round(x, 2) for x in os.getloadavg()]


def summary_a(results, songs):
    """The JSON-ready summary of check A `results` ({name: records})."""
    seeds = {s["name"]: [s["seed"], s["index"]] for s in songs}
    states = [(n, r) for n, rs in results.items() for r in rs]
    unequal = [n for n, r in states if r.get("equal") is False]
    return {
        "songs": len(results),
        "states": len(states),
        "certified": sum("equal" in r for _, r in states),
        "equal": sum(r.get("equal") is True for _, r in states),
        "unequal": [{"song": n, "seed": seeds[n]} for n in sorted(set(unequal))],
        "unverified": sorted({n for n, r in states if r.get("verified") is False}),
        "forms": _count(r["form"] for _, r in states),
        "orbits": _count(r["orbit"] for _, r in states),
        "not_certified": _uncertified(states, seeds),
    }


def _count(items):
    out = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _uncertified(states, seeds):
    """The song states with no certificate within their limits, with their seeds."""
    return [
        {"song": n, "state": r["state"], "seed": seeds[n], "form": r["form"]}
        | {"limit": r["limit"]}
        for n, r in states
        if r["form"] not in ("Certificate", "Segmented")
    ]


def timing(results, songs):
    """Per size class: states, forms, and calls and seconds (median, max) of the
    certificates over `results` ({name: certify records})."""
    size = {s["name"]: s["size"] for s in songs}
    seeds = {s["name"]: [s["seed"], s["index"]] for s in songs}
    out = {}
    for cls, most in CALLS.items():
        states = [(n, r) for n, rs in results.items() if size[n] == cls for r in rs]
        if not states:
            continue
        done = [r for _, r in states if r["form"] in ("Certificate", "Segmented")]
        calls = [r["calls"] for r in done]
        secs = [r["seconds"] for r in done]
        out[cls] = {
            "states": len(states),
            "forms": _count(r["form"] for _, r in states),
            "calls": _spread(calls),
            "seconds": _spread(secs),
            "cpu": _spread([r["cpu"] for r in done]),
            "limit": [most, SECONDS[cls]],
            "not_certified": _uncertified(states, seeds),
        }
    return out


def _spread(values):
    """[median, max] of `values`, None for none."""
    return [statistics.median(values), max(values)] if values else None


def song_code(path, size):
    """(instruction addresses ascending, closed) the plain orbits of every state of the
    PSID at `path` run."""
    play, states = liftcheck.song_states(path.read_bytes())
    pcs, closed = np.zeros(0, np.int64), True
    for state in states:
        end, _, _, edges, _ = orbit(state, play, ORBIT[size])
        pcs = np.union1d(pcs, forced.instructions(edges))
        closed &= end == "closed"
    return pcs.tolist(), bool(closed)


def tools_code(psid, jobs):
    """{stage: instruction addresses} play of `psid` runs: certified, with forcing, and
    with walking back; walkback's report."""
    play, states = liftcheck.song_states(psid)
    base = np.unique(
        np.concatenate(
            [forced.proven(s, play, transliterate.VOLATILE)[0] for s in states]
        )
    )
    rep = forced.report(psid)
    extra = np.asarray([a << 16 | b for a, b in rep["forced_edges"]], np.int64)
    walked = walkback.report(psid, jobs)
    return {
        "certified": forced.instructions(base).tolist(),
        "forced": forced.instructions(np.union1d(base, extra)).tolist(),
        "walked": sorted(
            set(walked["executed"]) | set(forced.instructions(base).tolist())
        ),
    }, walked


def labelled(addrs, symbols, code):
    """`addrs` as ``[address, "label+offset"]`` by the nearest playroutine label below."""
    lo, hi = code
    marks = sorted(
        (v, k)
        for k, v in symbols.items()
        if isinstance(v, int) and lo <= v < hi and k.islower()
    )
    at = np.asarray([v for v, _ in marks], np.int64)
    out = []
    for a in addrs:
        i = int(np.searchsorted(at, a, "right")) - 1
        name = f"{marks[i][1]}+{a - marks[i][0]}" if i >= 0 else "?"
        out.append([int(a), name])
    return out


def check_b(directory, songs, jobs, packed):
    """Check B over the full-build sweep `songs` in `directory`: the instructions all of
    them run against those certifying, forcing and walking back the first (plain) one
    reach; `packed` the plain song's gtpack.Packed, for labels."""
    runs = run(song_code, songs, directory, jobs)
    union = sorted({pc for pcs, _ in runs.values() for pc in pcs})
    plain = (directory / f"{songs[0]['name']}.sid").read_bytes()
    stages, walked = tools_code(plain, jobs)
    found, ran = set(stages["walked"]), set(union)
    return {
        "plain": songs[0]["name"],
        "songs": len(songs),
        "closed": sum(c for _, c in runs.values()),
        "instructions": {k: len(v) for k, v in stages.items()} | {"union": len(union)},
        "tools_only": labelled(sorted(found - ran), packed.symbols, packed.code),
        "songs_only": labelled(sorted(ran - found), packed.symbols, packed.code),
        "unresolved": walked["unresolved"],
        "seconds": walked["seconds"],
    }


def write_json(path, data):
    """Write `data` as JSON to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), "utf-8")
