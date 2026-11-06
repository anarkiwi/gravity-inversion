"""The hand and generated C translations against the original, per SID write and play call."""

import pytest

from incoherent_support import BUILD, FIX, SID, VARIANTS, build_prg, gen_prg, patched
import sidtrace

PRG = BUILD / "incoherent.prg"
PRGS = {"hand": PRG, "generated": BUILD / "gen.prg"}
BUILDERS = {"hand": build_prg, "generated": gen_prg}
SONG_POS = range(0x0C66, 0x0C7B, 7)
VARIANT_FRAMES = 6000


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


def test_mismatch_reported(psid, capsys):
    """A differing song is reported as a mismatch."""
    path = BUILD / "variant.sid"
    path.write_bytes(patched(psid, VARIANTS["speed_entry_zero"][0]))
    assert sidtrace.main([str(path), str(PRG), "--frames", str(VARIANT_FRAMES)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
