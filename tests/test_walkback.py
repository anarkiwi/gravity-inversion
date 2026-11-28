"""Tests for tools/walkback.py on synthetic PSIDs whose guards are each read back to a song byte,
a constant the player stores, or a patch that would run the song table.
"""

import json

import numpy as np
import pytest
from jennings.acme import assemble

import forced
import fuzzplayer
import walkback as W

COUNT = """
    inx
    cpx #4
    bne +
    ldx #0
+   stx pos
    rts
"""

IMMEDIATE = f"""
init:
    rts
play:
    ldx pos
    lda song,x
    cmp #$42
guard beq hit
back:
{COUNT}
hit:
    inc extra
    jmp back
song !byte 1, 2, 3, 4
pos !byte 0
extra !byte 0
"""

MODE = f"""
init:
    rts
play:
    jmp (vec)
normal:
    ldx pos
    lda song,x
    cmp #$80
guard bcc +
    lda #<modal
    sta vec
    lda #>modal
    sta vec+1
+
{COUNT}
modal:
    inc extra
    rts
vec !word normal
song !byte 1, 2, 3, 4
pos !byte 0
extra !byte 0
"""

OFFSET = f"""
init:
    rts
offsets !byte 0, t1 - t0, t2 - t0
play:
    ldx pos
    ldy song,x
    lda offsets,y
    sta guard+1
    clc
guard bcc t0
t0  inc c0
    jmp back
t1  inc c1
    jmp back
t2  inc c2
    jmp back
back:
{COUNT}
song !byte 0, 0, 0, 0
pos !byte 0
c0 !byte 0
c1 !byte 0
c2 !byte 0
"""

CONSTANT = """
init:
    rts
play:
set lda #5
    sta cell
    lda cell
    cmp #5
guard bne +
    inc extra
+   rts
cell !byte 0
extra !byte 0
"""

TABLE = f"""
init:
    rts
play:
    ldx pos
    lda song,x
    cmp #$60
guard beq song
{COUNT}
song !byte 1, 2, 3, 4
pos !byte 0
"""

PUSHED = f"""
init:
    rts
play:
    ldx pos
    ldy song,x
    lda hi,y
    pha
    lda lo,y
    pha
guard rts
t0  inc c0
    jmp back
t1  inc c1
    jmp back
back:
{COUNT}
lo !byte <(t0 - 1), <(t1 - 1)
hi !byte >(t0 - 1), >(t1 - 1)
song !byte 0, 0, 0, 0
pos !byte 0
c0 !byte 0
c1 !byte 0
"""

TWO = f"""
init:
    rts
play:
    ldx pos
    lda song,x
    sta ptr
    lda song+4,x
    sta ptr+1
guard jmp (ptr)
t0  inc c0
    jmp back
back:
{COUNT}
song !byte <t0, <t0, <t0, <t0, >t0, >t0, >t0, >t0
ptr !word 0
pos !byte 0
c0 !byte 0
"""


def _tune(src):
    """(PSID, symbols) of a synthetic player."""
    return assemble(fuzzplayer.HEADER + src + "\n}\n")


@pytest.fixture(scope="module", name="reports")
def fixture_reports():
    """{name: (report, symbols)} of each synthetic player."""
    out = {}
    for name, src in {
        "immediate": IMMEDIATE,
        "mode": MODE,
        "offset": OFFSET,
        "constant": CONSTANT,
        "table": TABLE,
        "pushed": PUSHED,
        "two": TWO,
    }.items():
        psid, syms = _tune(src)
        out[name] = (W.report(psid), syms, psid)
    return out


def _guard(report, pc):
    """The unresolved entry of guard `pc` of `report`."""
    return next(u for u in report["unresolved"] if u["guard"] == pc)


def test_immediate(reports):
    """A song byte compared with an immediate is patched to that immediate, the first one the
    guard's call loads, and the branch's other side runs."""
    r, syms, _ = reports["immediate"]
    assert [p["changes"] for p in r["patches"]] == [[[syms["song"], 1, 0x42]]]
    assert r["patches"][0]["guard"] == syms["guard"]
    assert [syms["guard"], syms["hit"]] in r["patched_edges"]
    assert syms["hit"] in r["executed"] and not r["unresolved"]


def test_mode_in_later_call(reports):
    """A command byte sets a mode in one call whose code runs in the next: forcing stores the
    mode but never runs its code, the patched run (the byte at the compared immediate) does.
    """
    r, syms, psid = reports["mode"]
    edges = {tuple(e) for e in forced.report(psid)["forced_edges"]}
    assert not any(syms["modal"] in e for e in edges)
    assert [p["changes"] for p in r["patches"]][0] == [[syms["song"], 1, 0x80]]
    assert syms["modal"] in r["executed"]
    assert r["instructions"]["patched"] > r["instructions"]["forced"]


def test_offset_table(reports):
    """A branch offset the player rewrites from a table indexed by a song byte reaches each
    target: the byte set to each other index of the table, none past it (code)."""
    r, syms, _ = reports["offset"]
    got = sorted(
        p["changes"][0][2] for p in r["patches"] if p["guard"] == syms["guard"]
    )
    assert got == [1, 2]
    assert all(p["changes"][0][0] == syms["song"] for p in r["patches"])
    assert {syms["t1"], syms["t2"]} <= set(r["executed"])


def test_constant(reports):
    """A branch on a constant the player stores stops at the instruction setting it."""
    r, syms, _ = reports["constant"]
    u = _guard(r, syms["guard"])
    assert u["reason"] == W.CONSTANT and u["pc"] == syms["set"] and not r["patches"]
    assert r["reasons"] == {W.CONSTANT: 1}


def test_song_table(reports):
    """The patch that sends a branch into the song table is rejected with its rule."""
    r, syms, _ = reports["table"]
    u = _guard(r, syms["guard"])
    assert u["reason"] == W.REJECTED and f"${syms['song']:04X}" in u["detail"]
    assert not r["patches"]


def test_pushed_return(reports):
    """A return to an address pushed from tables indexed by a song byte reaches the other
    entry, the byte set to its index."""
    r, syms, _ = reports["pushed"]
    assert [p["changes"] for p in r["patches"]] == [[[syms["song"], 0, 1]]]
    assert syms["t1"] in r["executed"]


def test_two_bytes(reports):
    """A jump through a pointer whose bytes come from two song bytes needs both at once."""
    r, syms, _ = reports["two"]
    u = _guard(r, syms["guard"])
    assert u["reason"] == W.TWO_BYTES and u["kind"] == "indirect"


def test_evaluate_reads_off_instructions():
    """The P-Code evaluator gives a compare's carry, an add's carry and an indexed load's
    value for every value of the varied input, as the 6502 defines them."""
    v = np.arange(256, dtype=np.int64)
    mem = np.zeros(0x10000, np.uint8)
    mem[0x2000:0x2100] = np.arange(256)[::-1]
    span = np.ones(0x10000, bool)
    span[0x2080] = False

    def trace(code, regs, loads=(), vals=(), smc=()):
        rec = (0x1000, bytes(code + [0] * (3 - len(code))), loads, vals, (), 0xFF, smc)
        return W.Trace([rec], [regs], mem)

    regs = (0, 0, 0, 0xFF, 0)
    get, ok = W.evaluate(trace([0xC9, 0x30], regs), 0, "A", v, span)
    assert (get("C") == (v >= 0x30)).all() and (get("Z") == (v == 0x30)).all()
    assert ok.all()
    get, _ = W.evaluate(trace([0x69, 0xF0], (0, 0, 0, 0xFF, 1)), 0, "A", v, span)
    assert (get("A") == (v + 0xF1) & 0xFF).all() and (
        get("C") == (v + 0xF1 > 255)
    ).all()
    get, ok = W.evaluate(
        trace([0xBD, 0, 0x20], regs, (0x2000,), (255,)), 0, "X", v, span
    )
    assert (get("A") == 255 - v).all() and (ok == (v < 0x80)).all()
    get, _ = W.evaluate(trace([0xA9, 7], regs, smc=(1,)), 0, 0x1001, v, span)
    assert (get("A") == v).all()
    indirect = trace([0xB1, 0x10], regs, (0x10, 0x11, 0), (0, 0, 0), (1,))
    with pytest.raises(W.Unsupported):
        W.evaluate(indirect, 0, 0x1001, v, span)


def test_cli(reports, tmp_path, capsys):
    """The command line prints each tune's counts and unresolved guards, writes the report as
    JSON and the kept patched images."""
    psid = reports["immediate"][2]
    path, out = tmp_path / "synthetic.sid", tmp_path / "w.json"
    path.write_bytes(psid)
    args = [
        str(path),
        "--json",
        str(out),
        "--images",
        str(tmp_path / "img"),
        "--jobs",
        "2",
    ]
    assert W.main(args) == 0
    got = json.loads(out.read_text("utf-8"))[str(path)]
    assert got["patches"] == reports["immediate"][0]["patches"]
    images = np.load(tmp_path / "img" / "synthetic.npz")["images"]
    assert images.shape == (1, 0x10000)
    assert images[0][reports["immediate"][1]["song"]] == 0x42
    assert "synthetic: instructions" in capsys.readouterr().out
