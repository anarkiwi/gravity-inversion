"""Tests for tools/relocate.py and tools/playtables.py on synthetic 6502 players."""

import dataclasses
import json
import types

import numpy as np
import pytest
from deity_informant.postinit import X
from pysidtracker.header import parse_sid_header, write_psid

import playtables
import pointers
import relocate
import sidtrace

FRAMES = 8
TBL_A, PTR_LO, PTR_HI, TBL_B, TBL_C = 0x101E, 0x1060, 0x1064, 0x1070, 0x1078


def _psid(code, init=0x1000, play=0x1003, songs=1):
    """PSID with `code` at $1000, `songs` subtunes."""
    return write_psid(load=0x1000, init=init, play=play, image=bytes(code), songs=songs)


def _player():
    """Init stores $FB and the volume. Play counts in its own `LDA #` operand, reads a table
    by abs,X (overrunning into its code), a table by ($FB),Y through a split pointer table,
    compares the pointer's high byte with `#>tbl`, and reads a table by ($FD),Y set up by
    `LDA #>tbl`; each value goes to the SID."""
    mem = bytearray(0x7C)
    code = {
        0x00: (0x4C, 0x06, 0x10),  # jmp init
        0x03: (0x4C, 0x10, 0x10),  # jmp play
        0x06: (0xA9, 0x0F, 0x8D, 0x18, 0xD4),  # lda #$0f: sta $d418
        0x0B: (0xA9, 0x00, 0x85, 0xFB, 0x60),  # lda #0: sta $fb: rts
        0x10: (0xA9, 0x00, 0x18, 0x69, 0x01, 0x29, 0x03),  # lda #n: clc: adc #1: and #3
        0x17: (0x8D, 0x11, 0x10, 0xAA, 0x4C, 0x21, 0x10),  # sta n: tax: jmp $1021
        0x1E: (0x11, 0x22, 0x33),
        0x21: (0xBD, 0x1E, 0x10, 0x8D, 0x00, 0xD4),  # lda tbl_a,x: sta $d400
        0x27: (0xBD, 0x60, 0x10, 0x85, 0xFB),  # lda ptr_lo,x: sta $fb
        0x2C: (0xBD, 0x64, 0x10, 0x85, 0xFC),  # lda ptr_hi,x: sta $fc
        0x31: (0xA0, 0x01, 0xB1, 0xFB, 0x8D, 0x01, 0xD4),  # ldy #1: lda ($fb),y: sta
        0x38: (0xA5, 0xFC, 0xC9, 0x10, 0xD0, 0x05),  # lda $fc: cmp #>tbl_b: bne
        0x3E: (0xA9, 0x55, 0x8D, 0x03, 0xD4),  # lda #$55: sta $d403
        0x43: (0xA9, 0x78, 0x85, 0xFD, 0xA9, 0x10, 0x85, 0xFE),  # tbl_c -> $fd
        0x4B: (0x8A, 0xA8, 0xB1, 0xFD, 0x8D, 0x02, 0xD4, 0x60),  # lda ($fd),y: sta: rts
        0x60: (0x70, 0x72, 0x74, 0x76, 0x10, 0x10, 0x10, 0x10),
        0x70: tuple(range(1, 9)),
        0x78: (0x44, 0x55, 0x66, 0x77),
    }
    for at, b in code.items():
        mem[at : at + len(b)] = b
    return _psid(mem)


@pytest.fixture(name="post", scope="module")
def _post():
    return relocate.post_init(_player())


@pytest.fixture(name="plan", scope="module")
def _plan(post):
    return relocate.plan([post], [FRAMES])


def _check(post, p, frames=FRAMES):
    """(hits, rule mismatch, plain mismatch) of the one song's :func:`relocate.check`."""
    _, hits, rule, plain = relocate.check([post], p, [frames])[0]
    return hits, rule, plain


def test_post_init(post):
    """Init's state: its stores and its volatile writes in order."""
    assert post.io == [(0xD418, 0x0F)]
    assert 0xFB in post.stored and post.end == 0x107C


def test_tables():
    """Indexed bases start tables: fields of one record (stride 7) join its table though a
    loop over every byte clears them, and that loop's reach ends it; an unindexed access lies
    in the table holding it or is a table of its own byte; a read past a table's end into the
    next indexed base overruns it; a table's pad is the bytes past its end any index can
    reach."""
    mem = np.zeros(0x10000, np.uint8)
    mem[[0x10, 0x13, 0x16, 0x19, 0x1C, 0x1F, 0x22]] = [
        0xBD,
        0xBD,
        0x9D,
        0xAD,
        0xAD,
        0xB9,
        0xB9,
    ]
    rows = [(0x10, 0x2000, a) for a in (0x2000, 0x2007, 0x200E)]
    rows += [(0x13, 0x2001, a) for a in (0x2001, 0x2008, 0x200F)]
    rows += [(0x16, 0x2000, a) for a in range(0x2000, 0x2015)]
    rows += [(0x19, 0x2009, 0x2009), (0x1C, 0x2030, 0x2030)]
    rows += [(0x1F, 0x2040, a) for a in (0x2040, 0x2041, 0x2043)]
    rows += [(0x22, 0x2042, a) for a in (0x2042, 0x2043)]
    rows += [(0x25, 0x1FF0, a) for a in (0x2042, 0x2043)]
    pc, base, addr = (np.array(c, np.int64) for c in zip(*rows))
    acc = (pc, np.zeros_like(pc), base, addr)
    code = np.zeros(0x10000, bool)
    code[0x1FF0] = True
    mem[0x25] = 0xB9
    extent = playtables.tables(acc, code, mem)
    starts, ends, cell, inside, last = extent
    assert list(zip(starts.tolist(), ends.tolist())) == [
        (0x2000, 0x2014),
        (0x2030, 0x2030),
        (0x2040, 0x2041),
        (0x2042, 0x2043),
    ]
    assert last.tolist() == [[0x200E, 7], [0x2030, 1], [0x2041, 1], [0x2043, 1]]
    assert cell[(pc == 0x19)].tolist() == [0] and inside[pc == 0x19].all()
    assert cell[pc == 0x25].tolist() == [3, 3] and inside[pc == 0x25].all()
    assert (~inside).sum() == 1 and addr[~inside].tolist() == [0x2043]
    assert playtables.overreach(extent, acc, mem).tolist() == [0xEC, 0, 0xFE, 0xFE]
    assert playtables.overruns(acc, extent) == [
        {
            "pc": 0x1F,
            "addr": [0x2040, 0x2043],
            "ok": [[0x2040, 0x2041]],
            "last": [[0x2041, 1]],
        }
    ]


def test_groups():
    """Tables whose addresses' high bytes sum a common byte move as one block, as do those
    its hull overlaps; a block's pad is the reach of its tables' pads past its end, its last
    record its last table's."""
    extent = (
        np.array([0x2000, 0x2010, 0x2020, 0x2030]),
        np.array([0x2005, 0x2015, 0x2025, 0x2038]),
        None,
        None,
        np.array([[0x2005, 1], [0x2015, 1], [0x2024, 2], [0x2038, 1]]),
    )
    run = types.SimpleNamespace(
        events={
            (1, ((0x50, 1),), 0x2000),
            (2, ((0x50, 1), (0x51, 1)), 0x2020),
            (3, ((0x52, 1),), 0x2030),
            (4, X, 0x2010),
        }
    )
    table = {(1, 0x2000): 0, (2, 0x2020): 2, (3, 0x2030): 3, (4, 0x2010): 1}
    blocks, blk = playtables.groups(run, table, extent, np.array([0, 0x13, 0, 0x10]))
    assert blocks == [
        (0x2000, 0x2025, 3, (0x2024, 2)),
        (0x2030, 0x2038, 0x10, (0x2038, 1)),
    ]
    assert blk.tolist() == [0, 0, 0, 1]


def test_place():
    """A span whose bytes and the pad it needs there are free stays (bytes of the reasons it
    owns aside); else Δ
    is the least page multiple keeping it and its pad clear of every reason, past the last;
    spans without pads that touch keep one Δ; a span that fits only below `start` goes there;
    one that fits nowhere raises, naming what holds its closest window."""
    code = relocate.mask([(0x1000, 0x1100), (0x2200, 0x2300)])
    why = {"code pages": code}
    pad = relocate.PAGE
    deltas, cursor, held = relocate.place(
        [
            (0x1010, 0x1020, pad, pad),
            (0x1030, 0x1040, pad, pad),
            (0x4010, 0x4020, pad, pad),
        ],
        why,
        0x2000,
    )
    assert deltas == [0x1000, 0x1300, 0]
    assert cursor == 0x1040 + 0x1300 + pad + 1
    assert np.flatnonzero(held).tolist() == [
        *range(0x2010, 0x2021 + pad),
        *range(0x2330, 0x2341 + pad),
        *range(0x4010, 0x4021 + pad),
    ]
    got = relocate.place([(0x1010, 0x1020, 0, 0), (0x1021, 0x1040, 3, 3)], why, 0x2000)
    assert got[:2] == ([0x1000, 0x1000], 0x2044)
    why["init's data"] = relocate.mask([(0x4010, 0x4021)])
    assert relocate.place([(0x4010, 0x4020, 1, 1)], why, 0x2000, ("init's data",))[
        0
    ] == [0]
    assert relocate.place([(0x4010, 0x4020, 1, 1)], why, 0x2000)[0] == [-0x2000]
    tight = {"data that stays": relocate.mask([(0x4021, 0x4022)])}
    assert relocate.place([(0x4010, 0x4020, 2, 0)], tight, 0x2000)[0] == [0]
    assert relocate.place([(0x4010, 0x4020, 2, 1)], tight, 0x2000)[0] == [-0x2000]
    why = {"I/O": ~relocate.mask([(0x1000, 0x1300)])}
    got = relocate.place(
        [(0x3010, 0x3020, pad, pad), (0x3030, 0x3040, pad, pad)], why, 0x2000
    )
    assert got[:2] == ([-0x2000, -0x1F00], 0x2000)
    with pytest.raises(RuntimeError, match=r"\$0010-\$02F0: at \$0F10 I/O 240$"):
        relocate.place([(0x10, 0x2F0, pad, pad)], why, 0x2000)
    summary = relocate.report(
        relocate.Plan(
            [(0x3010, 0x3020, -0x2000), (0x4000, 0x4001, 0)],
            {},
            [],
            {},
            {},
            [],
            why["I/O"],
            0,
            {},
        )
    )
    assert summary["moved"] == ["$3010-$3020 -$2000"]
    assert summary["in place"] == ["$4000-$4001"]


def test_solve():
    """A byte every constraint holding it asks the same shift of shifts; a shared byte
    first; constraints no byte satisfies are left."""
    base, t1, t2 = ((9, 1),), ((1, 1),), ((2, 1),)
    s, left = relocate.solve([(base + t1, 3), (base + t2, 3)])
    assert s == {9: 3} and not left
    s, left = relocate.solve([(base + t1, 3), (base + t2, 4)])
    assert s == {1: 3, 2: 4} and not left
    s, left = relocate.solve([(((5, 2),), 4), (((6, 1),), 1), (((6, 1),), 0)])
    assert not s and left == [0, 1]
    s, left = relocate.solve([(((1, 1), (7, -1)), 0)], {1: 3})
    assert s == {1: 3, 7: 3} and not left


def test_room():
    """Init goes in the first run of free bytes long enough at or after the cursor, else the
    first after the stack."""
    free = np.zeros(0x10000, bool)
    free[[0x2000, 0x2002, 0x2003, 0x2004, 0x3000]] = True
    assert relocate.room(free, 0x1000, 3) == 0x2002
    assert relocate.room(free, 0x2003, 1) == 0x2003
    assert relocate.room(free, 0x2005, 3) == 0x2002
    with pytest.raises(RuntimeError):
        relocate.room(free, 0x2001, 4)


def test_plan(plan):
    """Each table moves by whole pages off its code's page; the read past the abs,X table into
    code overruns it, judged as its last entry; each kind of reference is rewritten by its
    block's Δ."""
    moved = {lo: (hi, d) for lo, hi, d in plan.moved}
    assert set(moved) == {TBL_A, PTR_LO, PTR_HI, TBL_B, TBL_C}
    assert all(
        d % relocate.PAGE == 0 and lo + d >= 0x1100 for lo, (_, d) in moved.items()
    )
    assert not plan.kept and not plan.unsolved
    assert not plan.overrun and moved[TBL_A][0] == TBL_A + 2
    assert plan.sites == [
        {
            "pc": 0x1021,
            "addr": [TBL_A, TBL_A + 3],
            "ok": [[TBL_A, TBL_A + 2]],
            "last": [[TBL_A + 2, 1]],
        }
    ]
    page = {lo: d // relocate.PAGE for lo, (_, d) in moved.items()}
    want = {
        0x1023: page[TBL_A],
        0x1029: page[PTR_LO],
        0x102E: page[PTR_HI],
        0x103B: page[TBL_B],
        0x1048: page[TBL_C],
        **{PTR_HI + i: page[TBL_B] for i in range(4)},
    }
    assert plan.shift == want
    assert plan.kinds[0x1023] == "absx operand of LDA"
    assert plan.kinds[0x103B] == "imm operand of CMP"
    assert plan.kinds[0x1048] == "imm operand of LDA"
    assert plan.kinds[PTR_HI] == "data"


def _repeats(plan, mem, want, held):
    """Each moved block's pad in `mem` repeats its last record of `want`, but bytes `held`."""
    for (_, hi, d), (n, first, k) in zip(plan.moved, plan.fill):
        for a in range(hi + 1, hi + n + 1):
            assert held[a + d] or mem[a + d] == want[first + (a - first) % k]


def test_apply(post, plan):
    """The PSID loads the relocated image with a play-only header; its stub restores init's
    state; the protected run reads no vacated byte and plays as the original."""
    load, image, init = relocate.apply([post], plan)
    data = relocate.psid([post], load, image, init)
    head = parse_sid_header(data)
    assert (head.songs, head.start_song, head.play_address) == (1, 1, 0x1003)
    assert head.init_address == init == plan.cursor
    mem = np.zeros(0x10000, np.uint8)
    mem[load : load + len(image)] = np.frombuffer(image, np.uint8)
    held, want = np.zeros(0x10000, bool), post.mem.copy()
    held[[a + d for a, d in plan.overrun]] = True
    for a, k in plan.shift.items():
        want[a] += k
    for lo, hi, d in plan.moved:
        assert (mem[lo + d : hi + d + 1] == want[lo : hi + 1]).all()
        held[lo + d : hi + d + 1] = True
    old = relocate.mask([(lo, hi + 1) for lo, hi, _ in plan.moved]) & ~plan.held
    assert old.any() and not mem[old].any()
    _repeats(plan, mem, want, held)
    d = {lo: d for lo, _, d in plan.moved}[TBL_A]
    assert plan.held[TBL_A + 3 + d] and mem[TBL_A + 3 + d] == 0x33
    assert mem[TBL_C + 0x102] == 0x44 or any(
        mem[TBL_C + d] == 0x44 for *_, d in plan.moved
    )
    hits, rule, plain = _check(post, plan)
    assert not hits and rule is None and plain[2:] == ((0, 0xBD), (0, 0x33))


def test_unrewritten(post, plan):
    """A reference left at its old address reads a vacated byte: the protection names it."""
    p = dataclasses.replace(
        plan, shift={a: s for a, s in plan.shift.items() if a != 0x1023}
    )
    hits, _, _ = _check(post, p)
    assert hits and {pc for pc, _, _ in hits} == {0x1021}
    assert all(TBL_A <= a < TBL_A + 3 and f >= 1 for _, a, f in hits)


def test_unexplained():
    """A pointer high byte play computes by ORA explains no shift: its table stays."""
    code = bytearray(0x80)
    code[0:3] = (0x4C, 0x06, 0x10)
    code[3:6] = (0x4C, 0x07, 0x10)
    code[6] = 0x60
    play = (0xA9, 0x70, 0x85, 0xFD, 0xA9, 0x10, 0x09, 0x00, 0x85, 0xFE)
    code[7 : 7 + len(play)] = play
    tail = (0xA0, 0x00, 0xB1, 0xFD, 0x8D, 0x00, 0xD4, 0x60)
    code[0x11 : 0x11 + len(tail)] = tail
    code[0x70] = 0x42
    post = relocate.post_init(_psid(code, play=0x1003))
    p = relocate.plan([post], [2])
    assert p.kept == {(0x1070, 0x1070): "address cannot move"}
    assert not p.moved and not p.unsolved and not p.shift


def test_control():
    """A table play patches a branch offset from stays where it is: the lifter reads its
    targets there; the counter play steps it by stays with its code."""
    code = bytearray(0x80)
    code[0:3] = (0x4C, 0x06, 0x10)
    code[3:6] = (0x4C, 0x07, 0x10)
    code[6] = 0x60
    play = (
        0xEE,
        0x30,
        0x10,
        0xAD,
        0x30,
        0x10,
        0x29,
        0x01,
        0xAA,
        0xE8,
    )  # inc n: and: inx
    play += (0xBD, 0x3F, 0x10, 0x8D, 0x18, 0x10, 0x90, 0x00)  # lda br,x: sta: bcc
    play += (0x8D, 0x00, 0xD4, 0x60)
    code[7 : 7 + len(play)] = play
    code[0x40:0x42] = (0x00, 0x00)
    post = relocate.post_init(_psid(code))
    p = relocate.plan([post], [4])
    assert p.kept == {(0x103F, 0x1041): "feeds control"}
    assert [(lo, hi) for lo, hi, _ in p.moved] == [(0x1030, 0x1030)]
    assert p.shift == {
        0x1009: p.moved[0][2] // relocate.PAGE,
        0x100C: p.moved[0][2] // relocate.PAGE,
    }


def test_main(tmp_path, capsys):
    """The CLI writes the play-only PSID and its summary, and checks it."""
    src, out, rep = tmp_path / "t.sid", tmp_path / "r.sid", tmp_path / "r.json"
    src.write_bytes(_player())
    argv = [str(src), str(out), "--frames", str(FRAMES), "--report", str(rep)]
    assert relocate.main([*argv, "--check"]) == 0
    printed = capsys.readouterr().out
    assert "0 vacated accesses" in printed and "overrun rule: identical" in printed
    summary = json.loads(rep.read_text("utf-8"))
    assert summary["kept"] == {} and len(summary["moved"]) == 5
    assert summary["overruns"] == {"$1021": ["$101E-$1020"]}
    assert [r[:5] for r in summary["rewritten"]["imm operand of CMP"]] == ["$103B"]
    site = [
        {
            "pc": 0x1021,
            "addr": [TBL_A, TBL_A + 3],
            "ok": [[TBL_A, TBL_A + 2]],
            "last": [[TBL_A + 2, 1]],
        }
    ]
    ref = sidtrace.trace_psid(
        src.read_bytes(),
        FRAMES,
        remap=sidtrace.overrun_remap(site),
    )
    assert sidtrace.trace_psid(out.read_bytes(), FRAMES) == ref


TBL_P = 0x1040


def _pointer_player(word):
    """Init leaves `word` in the pointer $FB; play points it at a table by `LDA #` before
    reading it by ($FB),Y at Y 2 and 1, and counts in its own `LDA #` operand ($1022).
    """
    code = bytearray(0x50)
    code[0:6] = (0x4C, 0x06, 0x10, 0x4C, 0x0F, 0x10)
    code[6:15] = (0xA9, word & 0xFF, 0x85, 0xFB, 0xA9, word >> 8, 0x85, 0xFC, 0x60)
    play = (0xA9, TBL_P & 0xFF, 0x85, 0xFB, 0xA9, TBL_P >> 8, 0x85, 0xFC, 0xA0, 0x02)
    play += (
        0xB1,
        0xFB,
        0x99,
        0x00,
        0xD4,
        0x88,
        0xD0,
        0xF8,
    )  # lda ($fb),y: sta: dey: bne
    play += (0xA9, 0x00, 0x8D, 0x02, 0xD4, 0xEE, 0x22, 0x10, 0x60)
    code[0x0F : 0x0F + len(play)] = play
    code[0x40:0x44] = (0x11, 0x22, 0x33, 0x44)
    return _psid(code)


def _relocated(word):
    """(original post-init, plan, relocated post-init) of :func:`_pointer_player`."""
    post = relocate.post_init(_pointer_player(word))
    p = relocate.plan([post], [FRAMES])
    out = relocate.post_init(relocate.psid([post], *relocate.apply([post], p)))
    return post, p, out


def _word(post):
    return int(post.mem[0xFB]) | int(post.mem[0xFC]) << 8


@pytest.mark.parametrize("word", [0x1017, TBL_P])
def test_entry_pointer(word):
    """Init's word in a pointer play reads through after storing it: the stub leaves it a
    whole-page move away, at the bytes it reached; code bytes are copied into the table's pad,
    a moved table's bytes are followed by its Δ. The table starts at the base its pointer
    is set to."""
    post, p, out = _relocated(word)
    ((lo, hi, d),) = p.moved
    got = _word(out)
    assert (got - word) % relocate.PAGE == 0 and got != word and not p.left
    assert [out.mem[got + y] for y in (1, 2)] == [post.mem[word + y] for y in (1, 2)]
    if word == TBL_P:
        assert got == word + d and not p.overrun
    else:
        assert hi + d < got + 1 and got + 2 <= hi + d + relocate.PAGE
        assert p.overrun == [(word + 1, got - word), (word + 2, got - word)]
    hits, rule, plain = _check(post, p)
    assert not hits and rule is None and plain is None
    assert lo == TBL_P


def test_entry_changing():
    """Init's word reaching a byte play writes stays: a copy would not follow it."""
    post, p, out = _relocated(0x1021)
    assert p.left == {0xFB: "reaches bytes a copy cannot hold"}
    assert _word(out) == _word(post) == 0x1021 and not p.overrun


def test_lands():
    """A pointer's bytes land in the first pad holding them a whole-page move away, over
    free bytes or copies of the same source."""
    n = relocate.PAGE
    assert pointers.lands(0x1A46, [0, 3], [(0x2000, n)], {}) == 0x600
    assert pointers.lands(0x1A46, [0, 0xC0], [(0x2000, n), (0x3040, n)], {}) == 0x1600
    two = [(0x2000, n), (0x3000, n)]
    assert pointers.lands(0x1A46, [0, 3], two, {0x2049: 0x1234}) == 0x1600
    assert pointers.lands(0x1A46, [0, 3], [(0x2000, n)], {0x2049: 0x1A49}) == 0x600
    assert pointers.lands(0x1A46, [0, 0xC0], [(0x2000, n)], {}) is None
    assert pointers.lands(0x1A46, [0, 3], [(0x2000, 0x49)], {}) is None


def _image(code):
    """The bytes of `code` ({offset: bytes}) from $1000."""
    mem = bytearray(max(at + len(b) for at, b in code.items()))
    for at, b in code.items():
        mem[at : at + len(b)] = b
    return mem


def _output_player(indexed):
    """Play reads table T ($1050) through ($FB),Y, its high byte from `ptrhi` ($1041) (by
    `LDA ptrhi,X` at X 0 when `indexed`, else `LDA ptrhi`), and reads F,Y ($1040) at Y 0 and
    1 by turns, so the second reaches `ptrhi`; each value goes to the SID."""
    load = (0xA2, 0x00, 0xBD) if indexed else (0xEA, 0xEA, 0xAD)
    return _psid(
        _image(
            {
                0x00: (0x4C, 0x06, 0x10, 0x4C, 0x07, 0x10, 0x60),
                0x07: (
                    0xA9,
                    0x00,
                    0x18,
                    0x69,
                    0x01,
                    0x29,
                    0x01,
                ),  # lda #n: adc #1: and #1
                0x0E: (0x8D, 0x08, 0x10, 0xA8),  # sta n: tay
                0x12: (0xB9, 0x40, 0x10, 0x8D, 0x01, 0xD4),  # lda f,y: sta $d401
                0x18: (*load, 0x41, 0x10, 0x85, 0xFC, 0xA9, 0x50, 0x85, 0xFB),
                0x23: (0xA0, 0x00, 0xB1, 0xFB, 0x8D, 0x00, 0xD4, 0x60),
                0x40: (0x33, 0x10),
                0x50: (0x77,),
            }
        )
    )


def test_output_indexed():
    """A read past a table's end overruns it: F,Y at Y 1 reaches `ptrhi`, which an index
    reads, a table of its own, so the reference reads F's last entry there and so does the
    relocation, F padded with it; `ptrhi` and the table T its pointer reaches move. As it
    stands the SID hears $10 for `ptrhi`."""
    post = relocate.post_init(_output_player(True))
    p = relocate.plan([post], [FRAMES])
    assert not p.kept and not p.unsolved
    assert {(lo, hi) for lo, hi, _ in p.moved} == {
        (0x1040, 0x1040),
        (0x1041, 0x1041),
        (0x1050, 0x1050),
    }
    assert p.sites == [
        {
            "pc": 0x1012,
            "addr": [0x1040, 0x1041],
            "ok": [[0x1040, 0x1040]],
            "last": [[0x1040, 1]],
        }
    ]
    hits, rule, plain = _check(post, p)
    assert not hits and rule is None
    assert plain[2:] == ((1, 0x10), (1, 0x33))


def test_output_unindexed():
    """An address only an unindexed access names starts no table: F,Y reaching `ptrhi` reads
    inside F, which moves with it, so no rule covers that read and the SID hears `ptrhi`
    shifted by T's move."""
    post = relocate.post_init(_output_player(False))
    p = relocate.plan([post], [FRAMES])
    moved = {(lo, hi): d for lo, hi, d in p.moved}
    assert set(moved) == {(0x1040, 0x1041), (0x1050, 0x1050)}
    assert not p.sites and not p.kept
    hits, rule, plain = _check(post, p)
    shifted = 0x10 + moved[(0x1050, 0x1050)] // relocate.PAGE
    assert not hits and rule == plain and plain[2:] == ((1, 0x10), (1, shifted))


def test_fixup():
    """The copies of a plan run in at most 255 bytes, one fix-up copy loop's most, a run
    continuing while source and destination do."""
    p = types.SimpleNamespace(
        moved=[(0x1000, 0x1100, 0x1000)],
        fill=[(0, 0x1100, 1)],
        overrun=[(0x1101, 0x1000), (0x1102, 0x1000), (0x1300, 0x2000)],
    )
    assert relocate.runs(*relocate.pairs(p)) == [
        (0x1000, 0x2000, 0xFF),
        (0x10FF, 0x20FF, 4),
        (0x1300, 0x3300, 1),
    ]


def _songs_player():
    """Two songs: init points $FB at T+2A ($1050) by the song in A and keeps the song in
    `sel` ($1030); play reads T through ($FB),Y without storing it, and U ($1048) by `sel`.
    """
    return _psid(
        _image(
            {
                0x00: (0x4C, 0x06, 0x10, 0x4C, 0x14, 0x10),
                0x06: (0xAA, 0xBD, 0x40, 0x10, 0x85, 0xFB, 0xA9, 0x10, 0x85, 0xFC),
                0x10: (0x8E, 0x30, 0x10, 0x60),  # stx sel: rts
                0x14: (0xA0, 0x00, 0xB1, 0xFB, 0x8D, 0x00, 0xD4),  # lda ($fb),y: sta
                0x1B: (0xAE, 0x30, 0x10, 0xBD, 0x48, 0x10, 0x8D, 0x01, 0xD4, 0x60),
                0x30: (0x00,),
                0x40: (0x50, 0x52),
                0x48: (0x0A, 0x0B),
                0x50: (0x61, 0x62, 0x63),
            }
        ),
        songs=2,
    )


def test_songs():
    """One relocation serves every song: blocks span what any song reaches; the image holds
    the moved constants; init calls the original's, then fixes what it leaves (the pointer
    word shifted, the moved state copied) for each song; each plays as the original."""
    data = _songs_player()
    posts = [relocate.post_init(data, s) for s in (0, 1)]
    p = relocate.plan(posts, [FRAMES, FRAMES])
    moved = {(lo, hi): d for lo, hi, d in p.moved}
    assert set(moved) == {(0x1030, 0x1030), (0x1048, 0x1049), (0x1050, 0x1052)}
    assert p.shift[0xFC] == moved[(0x1050, 0x1052)] // relocate.PAGE
    load, image, init = relocate.apply(posts, p)
    t = 0x1050 + moved[(0x1050, 0x1052)] - load
    assert image[t : t + 3] == bytes([0x61, 0x62, 0x63])
    assert image[init - load : init - load + 3] == bytes([0x20, 0x00, 0x10])
    out = relocate.psid(posts, load, image, init)
    head = parse_sid_header(out)
    assert (head.songs, head.start_song) == (2, 1)
    for song, post in enumerate(posts):
        got = relocate.post_init(out, song)
        assert int(got.mem[0x1030 + moved[(0x1030, 0x1030)]]) == song
        assert _word(got) == _word(post) + moved[(0x1050, 0x1052)]
        assert relocate.post_init(data, song).mem[0x1030] == song
    for _, hits, rule, plain in relocate.check(posts, p, [FRAMES, FRAMES]):
        assert not hits and rule is None and plain is None


def test_trace_io():
    """An access to I/O reads no data: the read of its own opcode the emulator reports for
    it is no access, an instruction reading its own opcode as data is."""
    code = _image(
        {
            0x00: (0x4C, 0x06, 0x10, 0x4C, 0x07, 0x10, 0x60),
            0x07: (0xA0, 0x00, 0xB9, 0x1B, 0xD4, 0xEA, 0xEA),  # ldy #0: lda $d41b,y
            0x0E: (0xA2, 0x03, 0xBD, 0x0D, 0x10, 0x60),  # ldx #3: lda $100d,x: rts
        }
    )
    post = relocate.post_init(_psid(code))
    (pc, w, base, addr), _ = playtables.trace_play(post, 2)
    assert list(zip(pc.tolist(), w.tolist(), base.tolist(), addr.tolist())) == [
        (0x1010, 0, 0x100D, 0x1010)
    ]


def test_in_place():
    """A table off its code's pages whose bytes and the pad its reads need lie on free bytes
    stays in place, its bytes kept; the one on the code's page moves, its pad repeating its
    last entry."""
    code = bytearray(0x182)
    code[0:7] = (0x4C, 0x06, 0x10, 0x4C, 0x07, 0x10, 0x60)
    code[7:14] = (0xA9, 0x00, 0x18, 0x69, 0x01, 0x29, 0x01)  # lda #n: adc #1: and #1
    code[14:18] = (0x8D, 0x08, 0x10, 0xAA)  # sta n: tax
    code[18:24] = (0xBD, 0x80, 0x11, 0x8D, 0x00, 0xD4)  # lda far,x: sta $d400
    code[24:31] = (0xBD, 0x40, 0x10, 0x8D, 0x01, 0xD4, 0x60)  # lda near,x: sta: rts
    code[0x40:0x42] = (0x21, 0x22)
    code[0x180:0x182] = (0x31, 0x32)
    post = relocate.post_init(_psid(code))
    p = relocate.plan([post], [FRAMES])
    moved = {(lo, hi): d for lo, hi, d in p.moved}
    assert moved[(0x1180, 0x1181)] == 0 and moved[(0x1040, 0x1041)] != 0
    load, image, init = relocate.apply([post], p)
    mem = np.zeros(0x10000, np.uint8)
    mem[load : load + len(image)] = np.frombuffer(image, np.uint8)
    assert mem[0x1180:0x1182].tolist() == [0x31, 0x32]
    d = moved[(0x1040, 0x1041)]
    assert mem[0x1042 + d : 0x1045 + d].tolist() == [0x22] * 3
    assert relocate.report(p)["in place"] == ["$1180-$1181"]
    hits, rule, plain = _check(post, p)
    assert not hits and rule is None and plain is None
    assert relocate.psid([post], load, image, init)


def test_room_rom():
    """Init never goes under a ROM the CPU port maps at init, though free memory there is
    the first past the cursor."""
    post = relocate.post_init(_player())
    p = relocate.plan([post], [FRAMES])
    free = np.zeros(0x10000, bool)
    free[0xA000:0xC000] = free[0x9000:0x9100] = True
    _, _, init = relocate.apply(
        [post], dataclasses.replace(p, free=free, cursor=0xA000)
    )
    assert 0x9000 <= init < 0x9100


def test_widen():
    """A pointer no pad holds asks each block its instructions access for the pad its reach
    from every post-init word needs, a whole-page move away; that pad holds it, one byte less
    does not."""
    mem = np.zeros(0x10000, np.uint8)
    mem[0xFB:0xFD] = (0x46, 0x1A)
    posts = [types.SimpleNamespace(mem=mem)]
    run = types.SimpleNamespace(pairs={0xFB: {(0x30, 0), (0x30, 3)}})
    left = {0xFB: "no pad holds its reach", 0x02: "reaches bytes a copy cannot hold"}
    need = pointers.widen(posts, run, {0x30: {0, 1}}, left, {0: (0x2000, 0)})
    assert need == {0: 0x4A}
    assert pointers.lands(0x1A46, [0, 3], [(0x2000, 0x4A)], {}) == 0x600
    assert pointers.lands(0x1A46, [0, 3], [(0x2000, 0x49)], {}) is None


def test_stores():
    """A store past its table's end into another table joins them; a read there, or a
    store into no table, does not."""
    extent = (
        np.array([0x2000, 0x2010]),
        np.array([0x2003, 0x2012]),
        np.array([0, 0, 0, 0]),
        np.array([True, False, False, False]),
        None,
    )
    acc = (
        np.array([1, 1, 2, 3]),
        np.array([1, 1, 0, 1]),
        np.array([0x2000] * 4),
        np.array([0x2001, 0x2011, 0x2012, 0x2020]),
    )
    u, v = relocate._stores(acc, extent)  # pylint: disable=protected-access
    assert u.tolist() == [0] and v.tolist() == [1]


def test_rom_read():
    """Play reading a ROM byte it never runs: the stub banks RAM in and stores the ROM's
    value there, so the relocation plays as the original."""
    code = _image(
        {
            0x00: (0x4C, 0x06, 0x10, 0x4C, 0x07, 0x10, 0x60),
            0x07: (0xAD, 0x00, 0xE0, 0x8D, 0x00, 0xD4),  # lda $e000: sta $d400
            0x0D: (0xBD, 0x40, 0x10, 0x8D, 0x01, 0xD4, 0x60),  # lda tbl,x: sta: rts
            0x40: (0x12,),
        }
    )
    post = relocate.post_init(_psid(code))
    p = relocate.plan([post], [FRAMES])
    assert p.port is not None and p.port & 7 == relocate.RAM_IO
    assert [a for a, _ in p.rom] == [0xE000] and p.rom[0][1] != post.mem[0xE000]
    hits, rule, plain = _check(post, p)
    assert not hits and rule is None and plain is None


def test_static_reach():
    """A table ends where its instructions' static reach does, short of the next base: the
    lift bounds `LDA tbl,X` of the player's `AND #3` index to tbl+3, which the trace of a
    frame leaves at tbl+1."""
    reach = playtables.static_reach(_player())
    assert reach[0x1021] == TBL_A + 3
    post = relocate.post_init(_player())
    acc, _, code, _ = playtables.observe([post], [1])
    plain = playtables.tables(acc, code, post.mem)
    full = playtables.tables(acc, code, post.mem, reach)
    i = list(plain[0]).index(TBL_A)
    assert plain[1][i] == TBL_A + 1 and full[1][i] == TBL_A + 2
