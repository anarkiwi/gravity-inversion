"""The hand and generated C translations against the original, per SID write and play call."""

import functools

import pytest

from incoherent_support import BUILD, FIX, SID, VARIANTS, build_prg, gen_prg, patched
import footprint
import gen_incoherent
import sidtrace
import transliterate

PRG = BUILD / "incoherent.prg"
PRGS = {"hand": PRG, "generated": BUILD / "gen.prg"}
BUILDERS = {"hand": build_prg, "generated": gen_prg}
SONG_POS = range(0x0C66, 0x0C7B, 7)
PATT_LO, PATT_HI = 0x0D95, 0x0E0F
VARIANT_FRAMES = 6000


@functools.cache
def _base_c(psid):
    return transliterate.transliterate(psid, "gen.h")[0]


def test_song_identical(psid, capsys):
    """Every SID write matches the original over the HVSC song length."""
    assert sidtrace.main([str(SID), str(PRG), "--hvsc", str(FIX)]) == 0
    calls = sidtrace.song_calls(psid, 0, FIX)
    assert f"OK: {calls} frames" in capsys.readouterr().out


def _jumps(psid, frames):
    """Play calls at which a voice's order list position moves back."""
    last, out = {}, []

    def on_watch(frame, addr, value):
        if frame and value < last.get(addr, 0):
            out.append(frame)
        last[addr] = value

    sidtrace.trace_psid(psid, frames, SONG_POS, on_watch)
    return out


@pytest.mark.parametrize("kind", PRGS)
def test_loop_identical(kind, psid):
    """Every SID write matches over twice the song length, through order list jumps."""
    frames = 2 * sidtrace.song_calls(psid, 0, FIX)
    jumps = _jumps(psid, frames)
    assert jumps and jumps[0] > frames // 2
    ref = sidtrace.trace_psid(psid, frames)
    assert sidtrace.diff(ref, sidtrace.trace_prg(PRGS[kind], frames)) is None


def test_without_init_identical(psid):
    """A song number with bit 7 set skips init: play runs on the image's state."""
    ref = sidtrace.trace_psid(psid, VARIANT_FRAMES, song=0x80)
    assert sidtrace.diff(ref, sidtrace.trace_psid(psid, VARIANT_FRAMES)) is not None
    got = sidtrace.trace_prg(PRG, VARIANT_FRAMES, song=0x80)
    assert sidtrace.diff(ref, got) is None


def test_song_outside_range_traps():
    """The generated init is translated for the songs the PSID header counts: a song number
    outside them (bit 7 set) traps at init."""
    with pytest.raises(AssertionError, match=r"jam at \$[0-9a-f]+ \(trap \d+\)"):
        sidtrace.trace_prg(PRGS["generated"], VARIANT_FRAMES, song=0x80)


def test_outside_data_traps(psid, tmp_path):
    """A read outside the data the hand C holds traps: every pattern pointer at the image's
    end."""
    load, _, _, image = sidtrace.psid_parts(psid)
    end, n = load + len(image), PATT_HI - PATT_LO
    patches = {PATT_LO + i: end & 0xFF for i in range(n)}
    variant = patched(psid, patches | {PATT_HI + i: end >> 8 for i in range(n)})
    with pytest.raises(AssertionError, match=r"jam at \$[0-9a-f]+ \(trap 1\)"):
        sidtrace.trace_prg(build_prg(variant, tmp_path), VARIANT_FRAMES)


@pytest.mark.parametrize("kind", BUILDERS)
@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(name, kind, psid, tmp_path):
    """Patched songs exercise paths the tune does not use."""
    patches, unchanged = VARIANTS[name]
    variant = patched(psid, patches)
    ref = sidtrace.trace_psid(variant, VARIANT_FRAMES)
    same = sidtrace.diff(ref, sidtrace.trace_psid(psid, VARIANT_FRAMES)) is None
    assert same == unchanged
    got = sidtrace.trace_prg(BUILDERS[kind](variant, tmp_path), VARIANT_FRAMES)
    assert sidtrace.diff(ref, got) is None


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_c_identical(name, psid):
    """A patched song generates the original's .c: song data is only in the .h."""
    variant = patched(psid, VARIANTS[name][0])
    assert transliterate.transliterate(variant, "gen.h")[0] == _base_c(psid)


def test_data_footprint(psid):
    """The hand C holds only the original's data, and every byte the original reads as data
    but its operand state and the state and switch it names, through order list jumps and
    over the patched variants."""
    runs = [(psid, [2 * sidtrace.song_calls(psid, 0, FIX)], [0])]
    runs += [(patched(psid, p), [VARIANT_FRAMES], [0]) for p, _ in VARIANTS.values()]
    orig = footprint.Original(runs, 1)
    got = footprint.check(orig, gen_incoherent.held(psid), gen_incoherent.named(psid))
    assert got == footprint.CLEAN


def test_mismatch_reported(psid, capsys):
    """A differing song is reported as a mismatch."""
    path = BUILD / "variant.sid"
    path.write_bytes(patched(psid, VARIANTS["speed_entry_zero"][0]))
    assert sidtrace.main([str(path), str(PRG), "--frames", str(VARIANT_FRAMES)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
