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
NO_PROVENANCE = pytest.mark.xfail(
    raises=KeyError, strict=True, reason="cgen reports no info['provenance'] yet"
)


@functools.cache
def provenance(tune):
    """cgen's provenance of the tune's generated C."""
    m = constructs.load(tune)
    _, _, info = transliterate((constructs.ROOT / m["psid"]).read_bytes(), "t.h")
    return info["provenance"]


def _entries():
    return [
        pytest.param(t, c["id"], marks=NO_PROVENANCE, id=f"{t}:{c['id']}")
        for t in TUNES
        for c in constructs.load(t)["constructs"]
        if c["kind"] not in UNCHECKED
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
        return c["dispatch"] in prov["switches"]
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
