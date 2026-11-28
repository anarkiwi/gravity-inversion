"""The code of play behind branches a song takes one way, run by forcing each branch.

forced.py PSID... [--json PATH]: from play's certified facts, each one-way conditional branch is
stopped at in a call that runs it and resumed on its other side until the call returns; the
branches that run exposes are forced in turn (docs/forced.md).
"""

import argparse
import json
import pathlib
import time

import numpy as np
from jennings.coverage import certificate as V
from jennings.coverage import components as C
from jennings.coverage.cfg import instruction_bytes
from jennings.opcodes import OPCODES
from tqdm import tqdm

import liftcheck
import sidtrace
import transliterate

#: The calls a certificate may cover.
LIMIT = 2_000_000
#: The forced branches one run may chain.
DEPTH = 8
#: The conditional branch opcodes.
BRANCH = [op for op in range(256) if OPCODES[op][1] == "rel"]


def gaps(keys, mem):
    """{branch: its only successor} of the conditional branches of `mem` that the edge
    `keys` (``pc << 16 | next``) show going one way."""
    src = keys >> 16
    cond = np.isin(mem[src], BRANCH)
    pcs, n = np.unique(src[cond], return_counts=True)
    sel = cond & np.isin(src, pcs[n == 1])
    return dict(zip(src[sel].tolist(), (keys[sel] & 0xFFFF).tolist()))


def other_side(mem, pc, nxt):
    """The successor of the conditional branch at `pc` that is not `nxt`."""
    off = int(mem[pc + 1])
    target = (pc + 2 + (off - 256 if off > 127 else off)) & 0xFFFF
    return target if nxt == (pc + 2) & 0xFFFF else (pc + 2) & 0xFFFF


def proven(image, play, volatile):
    """(edge keys, {pc: first call running it}, song data bool [65536]) play's verified
    certificate proves from `image`: song data the bytes loaded, never stored, no
    instruction's. Raises RuntimeError where play has no verified certificate."""
    exclude = [(C.STACK.start, C.STACK.stop), *volatile]
    cert = V.certify(
        image, play, LIMIT, max_cycles=sidtrace.MAX_CYCLES, exclude=exclude
    )
    if isinstance(cert, V.Open):
        raise RuntimeError(
            f"play at ${play:04X} has no cover point within {LIMIT} calls"
        )
    verdict = V.verify(image, cert)
    if not verdict.ok or verdict.assumed:
        raise RuntimeError(f"play at ${play:04X}: {verdict.failed or verdict.assumed}")
    e = np.asarray(sorted(map(tuple, cert.edges)), np.int64).reshape(-1, 2)
    calls = C.Calls(image, play, max_cycles=sidtrace.MAX_CYCLES).run(cert.calls)
    seen, at = calls.seen("edges", cert.calls)
    pcs, where = np.unique(seen.astype(np.int64) >> 16, return_index=True)
    loaded, stored, ran = (np.zeros(0x10000, bool) for _ in range(3))
    for (_, kind), addrs in cert.accesses.items():
        (stored if kind == C.STORE else loaded)[list(addrs)] = True
    ran[np.unique(e)] = True
    code = instruction_bytes(np.frombuffer(image, np.uint8), ran)
    return (
        np.unique(e[:, 0] << 16 | e[:, 1]),
        dict(zip(pcs.tolist(), at[where].tolist())),
        loaded & ~stored & ~code,
    )


def snapshots(image, play, calls):
    """{call: (memory, registers)} of play's machine before each of the ascending `calls`."""
    m, at, out = C.machine(image), 0, {}
    for call in tqdm(sorted(set(calls)), desc="advance", unit="call", mininterval=1):
        if call > at:
            m.trajectory(play, 0, sidtrace.MAX_CYCLES, call - at, [play])
            at = call
        out[call] = (bytes(m.memory), (m.a, m.x, m.y, m.sp, m.p))
    return out


def run(snapshot, play, chain, code):
    """Edge keys ``pc << 16 | next`` of one call of play from `snapshot`, each branch of
    `chain` (``(branch, other)``) resumed at its other side when reached. None where a branch
    of the chain is not reached; False where the call does not return or leaves `code`.
    """
    memory, regs = snapshot
    m = C.machine(memory)
    m.a, m.x, m.y, m.sp, m.p = regs
    floor = m.sp - 1
    (first, other), rest = chain[0], chain[1:]
    if not m.run_to_rts_until(play, 0, [first], sidtrace.MAX_CYCLES) or m.pc != first:
        return None
    parts = []
    for branch, nxt in [*rest, (None, None)]:
        m.pc, parts = other, [*parts, np.asarray([first], np.int64)]
        stop = None if branch is None else [branch]
        reason, trace = m.run_until(sidtrace.MAX_CYCLES, stop, floor, trace=True)
        parts.append(np.asarray(trace.pc, np.int64))
        if branch is not None and reason != "pc":
            return None
        first, other = branch, nxt
    pcs = np.concatenate(parts)
    if reason != "sp" or not code[pcs].all():
        return False
    return np.unique(pcs[:-1] << 16 | pcs[1:])


def force(image, play, code, known):  # pylint: disable=too-many-locals
    """(forced edge keys, runs kept, strays) of play from `image`, whose certificate proves
    ``(edges, first)`` `known`: each one-way branch not among `known[2]` (steps already
    forced) forced, to the fixed point or :data:`DEPTH` deep; `code` as :func:`run` takes it.
    """
    base, first, done = known
    mem = np.frombuffer(image, np.uint8)
    todo = [
        (first[pc], (step,))
        for pc, nxt in sorted(gaps(base, mem).items())
        if (step := (pc, other_side(mem, pc, nxt))) not in done
    ]
    done.update(chain[-1] for _, chain in todo)
    shots = snapshots(image, play, [call for call, _ in todo])
    seen, runs, strays = base, 0, set()
    while todo:
        work, todo = todo, []
        for call, chain in work:
            got = run(shots[call], play, chain, code)
            if got is None or got is False:
                strays |= {chain[-1]} if got is False else set()
                continue
            runs, seen = runs + 1, np.union1d(seen, got)
            ran = set((got >> 16).tolist())
            for pc, nxt in gaps(seen, mem).items() if len(chain) < DEPTH else ():
                step = (pc, other_side(mem, pc, nxt))
                if pc in ran and step not in done:
                    done.add(step)
                    todo.append((call, (*chain, step)))
    return np.setdiff1d(seen, base), runs, strays


def report(psid):  # pylint: disable=too-many-locals
    """The JSON-ready report of forcing play of `psid` over its song states: a branch is
    forced from the first state whose certificate has it one-way."""
    play, states = liftcheck.song_states(psid)
    load, _, _, body = sidtrace.psid_parts(psid)
    mem = np.frombuffer(states[0], np.uint8)
    proofs = [proven(s, play, transliterate.VOLATILE) for s in states]
    code = np.zeros(0x10000, bool)
    code[load : load + len(body)] = True
    for _, _, data in proofs:
        code &= ~data
    base = np.unique(np.concatenate([edges for edges, _, _ in proofs]))
    extra, runs, strays, done, start = (
        np.zeros(0, np.int64),
        0,
        set(),
        set(),
        time.time(),
    )
    for image, (edges, first, _) in zip(states, proofs):
        e, n, lost = force(image, play, code, (edges, first, done))
        extra, runs, strays = np.union1d(extra, e), runs + n, strays | lost
    both = np.union1d(base, np.setdiff1d(extra, base))

    def count(keys):
        return len(np.unique(np.concatenate([keys >> 16, keys & 0xFFFF])))

    return {
        "play": play,
        "states": len(states),
        "runs": runs,
        "instructions": [count(base), count(both)],
        "one_way": [len(gaps(base, mem)), len(gaps(both, mem))],
        "forced_edges": [
            [int(k >> 16), int(k & 0xFFFF)] for k in np.setdiff1d(both, base)
        ],
        "strays": [list(step) for step in sorted(strays)],
        "left": sorted(gaps(both, mem)),
        "seconds": round(time.time() - start, 2),
    }


def main(argv=None):
    """Command line entry."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("psid", type=pathlib.Path, nargs="+")
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args(argv)
    out = {str(p): report(p.read_bytes()) for p in args.psid}
    if args.json:
        args.json.write_text(json.dumps(out, indent=1), "utf-8")
    for name, r in out.items():
        left = " ".join(f"${pc:04X}" for pc in r["left"])
        print(
            f"{pathlib.Path(name).stem}: instructions {r['instructions'][0]} -> "
            f"{r['instructions'][1]}, one-way {r['one_way'][0]} -> {r['one_way'][1]}, "
            f"{r['runs']} forced runs in {r['seconds']} s, {len(r['strays'])} strayed; "
            f"left {left}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
