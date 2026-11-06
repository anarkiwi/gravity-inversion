"""Ablation of generated C (tools/ablate.py): each rewrite removes its construct and compiles;
exact steps keep a player's SID writes; the data accounting matches the jennings listing.
"""

import json
import re
import subprocess

import pytest

import ablate
import cflow
import fuzzplayer
from copies import instructions
from costs import refs
from disasm import coverage, listing
from transliterate import transliterate

HEADER = """#define R0_ORG 0x0002
#define R0_LEN 2
#define R0_INIT 0x00, 0x20
#define C0_ORG 0x1000
#define C0_LEN 16
#define C0_INIT 0xA9, 0, 0x8D, 0, 0x20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x60
#define C0_DATA_INIT 0x30, 0x00
#define CODE_ORG 0x1000
#define R1_ORG 0x1004
#define R1_LEN 2
#define R2_ORG 0x2000
#define R2_LEN 512
#define R2_INIT 0
"""

SOURCE = """#include "tiny.h"
#include <stdint.h>

uint8_t io_read(uint16_t a);
void io_write(uint16_t a, uint8_t v);
void trap(uint16_t k);

uint8_t C0[C0_LEN] = {C0_INIT};
uint8_t R0[R0_LEN] = {R0_INIT};
#define R1 (C0 + (R1_ORG - C0_ORG))
uint8_t R2[R2_LEN] = {R2_INIT};
static const uint8_t C0_DATA[] = {C0_DATA_INIT};
static __attribute__((noinline)) uint8_t rd(uint16_t a) {
  if ((uint16_t)(a - R0_ORG) < R0_LEN)
    return R0[a - R0_ORG];
  if ((uint16_t)(a - R2_ORG) < R2_LEN)
    return R2[a - R2_ORG];
  if ((a >= 0xD000 && a < 0xE000))
    return io_read(a);
  if ((uint16_t)(a - C0_ORG) < C0_LEN)
    return C0[a - C0_ORG];
  return 0;
}
static __attribute__((noinline)) void wr(uint16_t a, uint8_t v) {
  if ((uint16_t)(a - R2_ORG) < R2_LEN) {
    R2[a - R2_ORG] = v;
    return;
  }
  if ((uint16_t)(a - C0_ORG) < C0_LEN &&
      (C0_DATA[(a - C0_ORG) >> 3] >> ((a - C0_ORG) & 7) & 1)) {
    C0[a - C0_ORG] = v;
    return;
  }
}
static __attribute__((noinline)) uint8_t codechk(uint16_t a, uint8_t v) {
  if ((uint16_t)(a - C0_ORG) < C0_LEN)
    return C0[a - C0_ORG] != v &&
           !(C0_DATA[(a - C0_ORG) >> 3] >> ((a - C0_ORG) & 7) & 1);
  return 0;
}
static uint8_t rv1;
static void g(uint8_t x) {
  io_write(0xD400, x);
  rv1 = x;
}
static uint8_t h(uint8_t a, uint8_t b) {
  io_write(0xD401, a);
  return b;
}
void cgen_init(uint8_t a) {
  R1[0] = 0;
  R1[1] = 0x20;
  R2[0x1FF] = a;
}
void cgen_play(uint8_t a) {
  g(a);
  uint8_t v2 = rv1;
  io_write(0xD402, v2 + h(1, a));
  uint16_t p = R0[0] | ((uint16_t)R0[1] << 8);
  io_write(0xD403, rd(p + a));
  io_write(0xD404, rd((R0[0] | ((uint16_t)R0[1] << 8)) + 2));
  R0[0] = a;
  io_write(0xD405, rd(p + 1));
  io_write(0xD406, rd((R1[0] | ((uint16_t)R1[1] << 8)) + a));
  uint16_t q = (R1[0] | ((uint16_t)R1[1] << 8)) + 0x40;
  uint16_t hi = q >> 8;
  if ((hi == 1) || ((uint8_t)(hi - 0xD0) < 0x10)) {
    trap(1);
    return;
  }
  wr(q, a);
  if (codechk(q, a)) {
    trap(2);
    return;
  }
  uint8_t s = C0[a & 3];
  switch ((uint16_t)((s | ((uint16_t)(0 - (s > 0x7F)) & 0xFF00)) + (CODE_ORG + 0x10))) {
  case CODE_ORG + 0x12:
    io_write(0xD407, 1);
    break;
  case CODE_ORG + 0xE:
    io_write(0xD407, 2);
    break;
  case CODE_ORG + 0x200:
    io_write(0xD407, 3);
    break;
  default:
    break;
  }
  if (a == 7) {
    uint8_t v3 = rd(0x2010 + a);
    io_write(0xD408, v3);
  } else if (a < 7) {
    uint8_t v4 = rd(0x2020 + a);
    io_write(0xD408, v4);
  } else {
    uint8_t v5 = rd(0x2030 + a);
    io_write(0xD408, v5);
  }
}

#include "driver.h"

uint8_t io_read(uint16_t a) { return *(volatile uint8_t *)a; }

void io_write(uint16_t a, uint8_t v) { *(volatile uint8_t *)a = v; }

__attribute__((noinline)) void trap(uint16_t k) {
  (void)k;
  for (;;)
    __asm__ volatile(".byte 0x02");
}

void tune_init(uint8_t song) { cgen_init(song); }

void tune_play(void) { cgen_play(0); }
"""

PLAYER = """init:
    LDA $D41B
    STA sel
    LDA #<song
    STA $02
    LDA #>song
    STA $03
    LDA #<tab
    STA op+1
    LDA #>tab
    STA op+2
    LDX #0
-   LDA tab2,X
    BEQ +
    BMI skip
    STA buf,X
    LDA tab,X
    STA buf+4,X
skip:
    INX
    CPX #4
    BNE -
+
    LDA #0
    STA pos
    STA pos+1
    STA pos+2
    RTS
play:
    LDX #2
-   JSR voice
    DEX
    BPL -
    RTS
voice:
    LDA pos,X
    AND #7
    TAY
    LDA ($02),Y
    STA $D400
op: LDA $FFFF,X
    STA $D401
    LDA pos,X
    AND #3
    TAY
    LDA offs,Y
    STA br+1
    CLV
br: BVC t0
t0: LDA #1
    BNE done
t1: LDA #2
    BNE done
t2: LDA #3
    BNE done
t3: LDA #4
done:
    STA $D402
    LDA br
    STA $D403
    INC pos,X
    RTS
offs: !byte t0-br-2, t1-br-2, t2-br-2, t3-br-2
pos: !byte 0, 0, 0
sel: !byte 0
buf: !fill 8, $55
tab: !byte 1, 2, 3
tab2: !byte 5, 6, 7, 0
song: !byte 9, 8, 7, 6, 5, 4, 3, 2, 1, 0
}
"""
FRAMES = 200


@pytest.fixture(name="tiny")
def fixture_tiny(tmp_path):
    """An Ablation over a small C file in the printer's runtime interface."""
    (tmp_path / "tiny.h").write_text(HEADER, encoding="ascii")
    (tmp_path / "tiny.c").write_text(SOURCE, encoding="ascii")
    return ablate.Ablation(tmp_path / "tiny.c", work=tmp_path / "work")


def _pass(ctx, fn, text=SOURCE):
    """C text after one pass, checked to compile."""
    out = cflow.rewrite(text, fn(ctx.unit(text), ctx))
    ctx.build(out, fn.__name__)
    return out


def _psid(asm, out):
    src = out / "player.asm"
    src.write_text(asm, encoding="ascii")
    sid = out / "player.sid"
    subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
    return sid.read_bytes()


def _generated(psid, out):
    c, h, _ = transliterate(psid, "gen.h")
    (out / "gen.c").write_text(c, encoding="utf-8")
    (out / "gen.h").write_text(h, encoding="utf-8")
    return out / "gen.c"


def test_evaluate(tiny):
    """The evaluator gives the printer's sign-extended branch offset C's 16-bit value."""
    unit = tiny.unit(SOURCE)
    sw = next(
        n for n, _ in cflow.walk(unit.fns["cgen_play"]) if n.get("kind") == "SwitchStmt"
    )
    cond = sw["inner"][0]
    key = next(iter(refs(cond)))
    assert [cflow.evaluate(cond, {key: x}) for x in (0, 2, 0x7F, 0x80, 0xFE)] == [
        0x1010,
        0x1012,
        0x108F,
        0x0F90,
        0x100E,
    ]
    assert cflow.constant(cond) is None


def test_rewrite():
    """Edits apply right to left; one overlapping a later edit is dropped."""
    assert cflow.rewrite("abcdef", [(1, 3, "X"), (3, 3, "+"), (2, 5, "Y")]) == "aX+def"


def test_identity_returns(tiny):
    """A return global holding the caller's value and a function returning its argument use
    the argument."""
    out = _pass(tiny, ablate.identity_returns)
    assert "uint8_t v2 = a;" in out and "(h(1, a), a)" in out


def test_direct(tiny):
    """rd()/wr() index the largest data region; codechk() passes; the guard is false."""
    out = _pass(tiny, ablate.direct_reads)
    assert "return R2[(uint16_t)(a - R2_ORG)];" in out
    out = _pass(tiny, ablate.direct_writes)
    assert "R2[(uint16_t)(a - R2_ORG)] = v;" in out and "return 0;\n}" in out
    assert "(hi == 1)" not in out and "if (0)" in out


def test_zero_page_reads(tiny):
    """Reads through the zero-page pointer, or a variable holding it unchanged, take the byte;
    one after the pointer changed does not."""
    out = _pass(tiny, ablate.zero_page_reads)
    assert "rdz_R0_0(a)" in out and "rdz_R0_0(2)" in out and "rd(p + 1)" in out
    assert out.count("static __attribute__((noinline)) uint8_t rdz_R0_0") == 1


def test_fold(tiny):
    """Cells stored once with a constant fold in play, and rd() of the constant pointer plus a
    byte indexes the region holding all it can reach."""
    tiny.facts = ablate.Facts(
        ablate.Counter({0x1004: 1, 0x1005: 1}), {0x2000}, {0x1004: 0, 0x1005: 0x20}
    )
    out = _pass(tiny, ablate.fold_constant_stores)
    assert "R1[0] | " not in out and "(0x0 | ((uint16_t)0x20 << 8))" in out
    out = _pass(tiny, ablate.constant_addresses, out)
    assert (
        "R2[0x0 + (a)]" in out and "R2[0x10 + (a)]" in out and "(R2[0x40] = (a))" in out
    )


def test_store_past_the_bitmap_is_data(tmp_path):
    """wr() of a constant address past a span's bitmap (every byte past it is data) indexes the
    span; one at a constant byte the bitmap marks does not."""
    (tmp_path / "tiny.h").write_text(
        HEADER.replace("#define C0_DATA_INIT 0x30, 0x00", "#define C0_DATA_INIT 0x30"),
        encoding="ascii",
    )
    text = SOURCE.replace("  wr(q, a);\n", "  wr(0x100C, a);\n  wr(0x1001, a);\n")
    (tmp_path / "tiny.c").write_text(text, encoding="ascii")
    ctx = ablate.Ablation(tmp_path / "tiny.c", work=tmp_path / "work")
    out = _pass(ctx, ablate.constant_addresses, text)
    assert "(C0[0xC] = (a))" in out and "wr(0x1001, a)" in out


def test_fold_play(tiny):
    """Reads in play of cells play never writes fold to their value after init; a cell play
    writes does not."""
    tiny.facts = ablate.Facts(
        ablate.Counter(), {0x1005}, {0x1004: 0, 0x1005: 0x20, 0x1000: 0xA9}
    )
    out = _pass(tiny, ablate.fold_play_constants)
    assert "(0x0 | ((uint16_t)R1[1] << 8))" in out


def test_byte_switch(tiny):
    """A switch on a sign-extended byte plus a constant switches on the byte."""
    out = _pass(tiny, ablate.byte_switch)
    assert "switch (s)" in out and "case 0x2:" in out and "case 0xFE:" in out
    assert "0x200" not in out


def test_alike_branches(tiny):
    """Arms alike up to literals become one body selecting them by the conditions."""
    out = _pass(tiny, ablate.alike_branches)
    assert out.count("io_write(0xD408") == 1
    assert "((a == 7) ? 8208 : (a < 7) ? 8224 : 8240)" in out


def test_absent(tiny):
    """Without the runtime's rd() the runtime rewrites have nothing to do."""
    unit = tiny.unit(re.sub(r"\brd\(", "rx(", SOURCE))
    assert not ablate.direct_reads(unit, tiny) and not ablate.zero_page_reads(
        unit, tiny
    )


@pytest.fixture(scope="module", name="player")
def fixture_player(tmp_path_factory):
    """(PSID, report) of the test player: zero-page and self-modified pointers, a branch
    offset dispatch, a dead init loop (after a store of a byte read from the SID, which the
    lift does not fold past), a code byte read as data."""
    out = tmp_path_factory.mktemp("player")
    psid = _psid(fuzzplayer.HEADER + PLAYER, out)
    return psid, ablate.ablate(
        psid, _generated(psid, out), frames=FRAMES, work=out / "w", verify=True
    )


@pytest.mark.slow
def test_player_exact(player):
    """Every exact step keeps the player's SID writes; the dispatch and dead loop shrink."""
    rows = {r["step"]: r for r in player[1]["steps"]}
    assert all(r["verified"] for r in rows.values() if r["kind"] == "exact")
    assert rows["byte switch"]["delta"] < 0 and rows["dead init loops"]["delta"] < 0


@pytest.mark.slow
def test_accounting(player):
    """Each code-kept region's instruction bytes are those of the listing's instructions in
    it; its bytes split into instruction, accessed and untouched."""
    psid, report = player
    cov = coverage(psid, [FRAMES], [0], init=True)
    insns = instructions(listing(psid, 0, cov=cov).source)
    for d in report["data"]:
        lo, hi = d["origin"], d["origin"] + d["bytes"]
        assert d["instruction"] == sum(
            min(a + n, hi) - max(a, lo) for a, n, *_ in insns if a < hi and a + n > lo
        )
        assert d["instruction"] + d["accessed"] + d["untouched"] == d["bytes"]
    assert any(d["instruction"] for d in report["data"])


@pytest.mark.slow
@pytest.mark.parametrize("style", ["smc", "jsr3"])
def test_fuzz_exact(style, tmp_path):
    """Exact steps keep a fuzz player's SID writes."""
    psid = _psid(fuzzplayer.player(style, 0), tmp_path)
    report = ablate.ablate(
        psid,
        _generated(psid, tmp_path),
        frames=FRAMES,
        work=tmp_path / "w",
        verify=True,
    )
    assert all(r["verified"] for r in report["steps"] if r["kind"] == "exact")


@pytest.mark.slow
def test_main(capsys, tmp_path):
    """The CLI reports a tune's steps against its hand translation, as tables or JSON."""
    assert ablate.main(["commando", "--frames", "50", "--work", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| baseline | exact |" in out and "residual" in out
    assert (
        ablate.main(["commando", "--frames", "50", "--json", "--work", str(tmp_path)])
        == 0
    )
    report = json.loads(capsys.readouterr().out)["commando"]
    assert report["steps"][0]["step"] == "baseline" and "hand" in report
