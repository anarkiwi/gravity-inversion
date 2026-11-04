"""The construct maps (tunes/*/constructs.json) against the originals and the generated C.

Each map entry is valid in jennings' listing and covered by the original's run as the map says;
the generated C translates it as the hand C does, read from cgen's info["provenance"].
"""

import functools

import pytest

from transliterate import transliterate
import constructs

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")
UNCHECKED = {"region", "inline_repeat", "unrolled_stores", "per_voice_paths"}
MISSED = {
    "automatas:row-copies": "the copies fold as two classes, entered at $112C and $115E",
    "emomyst:wf_arp.end": "$16D9 → $16EE is threaded into the JMP's goto $16EE → $1776, and "
    "$1721 → $1776 prints structured with the fall-through $1773 → $1776 as a goto",
    "emomyst:vib_slide.vibrato": "$1579/$158B/$158D → $1590 print structured",
}


@functools.cache
def provenance(tune):
    """cgen's provenance of the tune's generated C."""
    m = constructs.load(tune)
    _, _, info = transliterate((constructs.ROOT / m["psid"]).read_bytes(), "t.h")
    return info["provenance"]


def _entries():
    keys = [
        f"{t}:{c['id']}"
        for t in TUNES
        for c in constructs.load(t)["constructs"]
        if c["kind"] not in UNCHECKED
    ]
    return [
        pytest.param(
            *k.split(":"),
            marks=(
                [pytest.mark.xfail(strict=True, reason=MISSED[k])]
                if k in MISSED
                else []
            ),
            id=k,
        )
        for k in keys
    ]


def _calls(prov, target):
    return {
        tuple(call)
        for f in prov["functions"].values()
        if f["entry"] == target
        for call in f["calls"]
    }


def lifted(c, prov):  # pylint: disable=too-many-return-statements
    """True if the generated C has the construct in the form the hand C gives it."""
    k, gotos = c["kind"], {tuple(e) for e in prov["gotos"]}
    if k == "loop":
        return c["head"] in prov["loops"]
    if k == "multi_entry_loop":
        return c["head"] in prov["loops"] and not {tuple(e) for e in c["edges"]} & gotos
    if k in ("call", "fall_call", "tail_call"):
        sites = c.get("sites", []) + c.get("falls", []) + c.get("jumps", [])
        calls = {s for s, t in _calls(prov, c["target"]) if t == c["target"]}
        return set(sites) <= calls
    if k == "unrolled":
        return any(
            f["index"] and set(c["copies"]) <= set(f["copies"])
            for f in prov["functions"].values()
        )
    if k == "smc":
        return c["cell"] in prov["cells"]
    if k == "goto":
        return {tuple(e) for e in c["edges"]} <= gotos
    if k == "join":
        return not {tuple(e) for e in c["edges"]} & gotos
    if k == "short_circuit":
        return any(set(c["branches"]) <= set(ch) for ch in prov["conditions"])
    if k == "switch":
        edges = {tuple(e) for e in c.get("edges", [])}
        return c["dispatch"] in prov["switches"] and not edges & gotos
    return all(tuple(e) in gotos or _calls(prov, e[1]) for e in c["edges"])


@pytest.mark.slow
@pytest.mark.parametrize("tune", TUNES)
def test_map(tune):
    """Every entry is valid in the original's listing and covered as the map says."""
    m = {c["id"]: c for c in constructs.load(tune)["constructs"]}
    for cid, (errors, covered) in constructs.check(tune, 4).items():
        assert not errors, (cid, errors)
        assert covered == m[cid].get("covered", True), cid


@pytest.mark.slow
@pytest.mark.parametrize("tune,cid", _entries())
def test_lifted(tune, cid):
    """The generated C has the construct in the hand C's form."""
    c = next(c for c in constructs.load(tune)["constructs"] if c["id"] == cid)
    assert lifted(c, provenance(tune)), c["hand"]


def test_main(capsys):
    """The CLI validates a map and prints each entry."""
    assert constructs.main(["incoherent", "--jobs", "2"]) == 0
    assert "incoherent:read_row.next: ok (covered)" in capsys.readouterr().out
