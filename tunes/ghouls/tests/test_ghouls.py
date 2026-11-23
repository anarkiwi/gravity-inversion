"""The hand and generated C translations against the original, per subtune and on patched
songs."""

import pytest

import disasm
import gen_ghouls
import sidtrace
import tunetest
from ghouls_support import (
    HVSC,
    BUILD,
    build_prg,
    gen_prg,
    pattern_block,
    with_block_src,
    with_pattern,
)

SONGS = range(32)
PRG = str(BUILD / "ghouls.prg")
PRGS = {"hand": PRG, "generated": str(BUILD / "gen.prg")}
VARIANT_SONG, VARIANT_FRAMES = 4, 150
CMD_GOTO, CMD_GOSUB = 0x87, 0x8A

# One pattern on all three voices, for the paths no subtune runs: a register
# list naming a SID mirror, a note without hard gate, hard restart over
# several frames, vibrato carrying out of $ffff, vibrato width 0 holding the
# direction, a filter cutoff above $0fff, pulse sweeps past both bounds and
# onto PULSE_MAX, extended notes, untransposed notes, portamento overshooting
# both ways, CMD_DEFAULT_PULSE feeding a zero pulse, and a note read past the
# frequency table.
PATTERN = bytes(
    [0x85, 0x18, 0x0F, 0x58, 0x0F, 0xFF]
    + [0x8D, 0x41, 0x80, 0xF0, 0x80, 0x0F, 0x30, 0x04]
    + [0x83, 0x02, 0x8F, 0x03, 0x81, 0x00, 0x10]
    + [0x8E, 0x01, 0x40, 0x01, 0x01, 0x60, 0x10, 0x00, 0x08]
    + [0x8E, 0x01, 0x20, 0x01, 0x00, 0x30, 0x08]
    + [0x8E, 0x01, 0x20, 0x00, 0x00, 0x00, 0x08]
    + [0x88, 0x01, 0x00, 0x00, 0x20, 0x00, 0x00, 0xFF, 0x30, 0x89]
    + [0x80, 0x01, 0x9A, 0x0F, 0x30, 0x04]
    + [0x93, 0x93, 0x8C, 0x05, 0x30, 0x04, 0x30, 0x04, 0x30, 0x04]
    + [0x92, 0x30, 0x40, 0x04, 0x20, 0x08, 0x40, 0x08, 0x92, 0x00]
    + [0x94, 0x11, 0x22, 0x80, 0x10, 0x00, 0x00, 0x7F, 0x04, 0x86]
)

# Branch outcomes no input reaches: the gate test after hard restart (the
# hard gate flag is set whenever a hard restart runs), and the second of
# each BCS/BCC pair, per voice.
UNREACHABLE = {
    (0x624F, 0x6258),
    (0x643C, 0x6445),
    (0x662B, 0x6634),
    (0x62D0, 0x62D2),
    (0x64BD, 0x64BF),
    (0x66AC, 0x66AE),
    (0x6320, 0x6322),
    (0x650D, 0x650F),
    (0x66FC, 0x66FE),
}


@pytest.mark.parametrize(
    "kind,song",
    [(kind, s) for kind in PRGS for s in SONGS],
)
def test_subtune_identical(kind, song, psid):
    """Every SID write of each subtune matches the original to its HVSC length."""
    frames = sidtrace.song_calls(psid, song, HVSC)
    _, writes, bad = sidtrace.compare(psid, PRGS[kind], frames, song)
    assert bad is None and writes


@pytest.fixture(scope="module", name="variant")
def fixture_variant(psid, tmp_path_factory):
    """PATTERN on every voice of VARIANT_SONG, and its C build."""
    data = with_pattern(psid, VARIANT_SONG, PATTERN)
    return data, build_prg(data, tmp_path_factory.mktemp("variant"))


def test_variant_identical(variant):
    """The patched song matches the original."""
    data, prg = variant
    _, writes, bad = sidtrace.compare(data, str(prg), VARIANT_FRAMES, VARIANT_SONG)
    assert bad is None and writes


def test_variant_c_identical(variant, psid):
    """The patched song generates the original's .c: song data is only in the .h."""
    tunetest.assert_same_c(psid, variant[0])


def test_variant_generated(variant, tmp_path):
    """The C generated from the patched song matches it."""
    prg = gen_prg(variant[0], tmp_path)
    _, writes, bad = sidtrace.compare(
        variant[0], str(prg), VARIANT_FRAMES, VARIANT_SONG
    )
    assert bad is None and writes


def test_variant_closes_branch_gaps(psid, variant):
    """Subtunes plus the variant take every reachable branch outcome."""
    cov = disasm.coverage(variant[0], VARIANT_FRAMES, VARIANT_SONG)
    cov |= disasm.coverage(
        psid, [sidtrace.song_calls(psid, s, HVSC) for s in SONGS], list(SONGS), 8
    )
    assert disasm.one_way(variant[0], cov) == UNREACHABLE


def _trap_variants(psid):
    """{trap code: patched PSID} accessing outside the data the hand C holds: a goto past
    the gosub return bytes, a gosub to itself nesting until its return address is stored
    there, a copy block sourced below the block tables."""
    past = gen_ghouls.GAPS["stack"].stop
    dst = pattern_block(psid, VARIANT_SONG)[1]
    return {
        1: with_pattern(psid, VARIANT_SONG, bytes([CMD_GOTO, past & 0xFF, past >> 8])),
        2: with_pattern(psid, VARIANT_SONG, bytes([CMD_GOSUB, dst & 0xFF, dst >> 8])),
        3: with_block_src(psid, VARIANT_SONG, gen_ghouls.BLOCK_SRC - 1),
    }


@pytest.mark.parametrize("code", [1, 2, 3])
def test_outside_data_traps(code, psid, tmp_path):
    """An access outside the data the hand C holds traps, with the code of its site."""
    prg = build_prg(_trap_variants(psid)[code], tmp_path)
    tunetest.assert_traps(code, str(prg), VARIANT_FRAMES, VARIANT_SONG)


def test_data_footprint(psid, variant):
    """The hand C holds only the original's data, and every byte the original reads as data
    but its operand state and the table it names, over every subtune and the variant."""
    runs = [
        (psid, [sidtrace.song_calls(psid, s, HVSC) for s in SONGS], list(SONGS)),
        (variant[0], [VARIANT_FRAMES], [VARIANT_SONG]),
    ]
    tunetest.assert_clean(gen_ghouls, runs, jobs=8)


def test_mismatch_reported(psid, variant, tmp_path, capsys):
    """A build of other data is reported as a mismatch."""
    src = tmp_path / "ref.sid"
    src.write_bytes(psid)
    args = [str(src), str(variant[1]), "--songs", str(VARIANT_SONG + 1)]
    assert sidtrace.main(args + ["--frames", "50"]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_data_within_player_limits(psid):
    """Effects name voices 0-2 and music blocks fit below RAM_END ($a000)."""
    load, _, _, image = sidtrace.psid_parts(psid)

    def peek(a):
        return image[a - load]

    def word(a):
        return peek(a) | peek(a + 1) << 8

    for effect in range(1, 22):
        p = peek(0x6DF7 + effect) | peek(0x6E0D + effect) << 8
        while peek(p) < 0x80:
            assert peek(p) in (0, 2, 4)
            p += 3
    for block in range(10):
        assert word(0x29FB + 2 * block) + word(0x2A30 + 2 * block) <= 0xA000
