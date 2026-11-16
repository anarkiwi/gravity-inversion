"""Tests for tools/sidtrace.py: the C translation against the original."""

import pytest

from support import BUILD, STOP_FRAMES, VARIANTS, build_prg, patched
import footprint
import gen_data
import sidtrace

LOOP_LIMIT = 20000


def test_psid_parts(ref_psid, labels):
    """PSID header fields come from the reference."""
    load, init, play, image = sidtrace.psid_parts(ref_psid)
    assert (load, init, play) == (0x1000, labels["init"], labels["play"])
    assert len(image) == len(ref_psid) - 0x7E


def test_loop_frame(ref_psid, labels):
    """A limit shorter than the tune loop is an error."""
    with pytest.raises(AssertionError):
        sidtrace.loop_frame(ref_psid, labels["posoff"], 100)


def test_whole_tune_identical(capsys):
    """Every SID write matches the original up to the tune loop."""
    args = [str(BUILD / n) for n in ("ref.sid", "commando.prg")]
    args += ["--wrap", str(BUILD / "ref.labels"), "posoff"]
    assert sidtrace.main(args) == 0
    assert "OK" in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(name, ref_psid, labels, tmp_path):
    """Patched songs exercise stop, skydive and vibrato carry paths."""
    psid = patched(ref_psid, labels, VARIANTS[name])
    prg = build_prg(psid, labels, tmp_path)
    ref = sidtrace.trace_psid(psid, STOP_FRAMES)
    assert sidtrace.diff(ref, sidtrace.trace_prg(prg, STOP_FRAMES)) is None
    if name == "stop":
        assert not ref[-1] and (0x18, 0x0F) in sum(ref[2:], [])


def _trap_patches(psid, labels):
    """{trap code: patches} the hand C reads outside its data under: a note past the voice
    state after the frequency table, a track below the song data, a pattern at the image's
    end, an instrument record there."""
    load, _, _, image = sidtrace.psid_parts(psid)
    end, below = load + len(image), labels["_songdata"] - 1
    note = [("p02", 0, 0x86)]
    return {
        1: note + [("p02", 2, 0x76)],
        2: [("_songdata", 0, below & 0xFF), ("_songdata", 3, below >> 8)],
        3: [("song1t1", 0, 0), ("patpl", 0, end & 0xFF), ("patph", 0, end >> 8)],
        4: note + [("p02", 1, 13)],
    }


@pytest.mark.parametrize("code", range(1, 5))
def test_outside_data_traps(code, ref_psid, labels, tmp_path):
    """A read outside the data the hand C holds traps, with the code of its site."""
    psid = patched(ref_psid, labels, _trap_patches(ref_psid, labels)[code])
    prg = build_prg(psid, labels, tmp_path)
    with pytest.raises(AssertionError, match=rf"jam at \$[0-9a-f]+ \(trap {code}\)"):
        sidtrace.trace_prg(prg, STOP_FRAMES)


def test_data_footprint(ref_psid, labels):
    """The hand C holds only the original's data, and every byte the original reads as data
    but its operand state and the state it names, up to the tune loop and over the patched
    variants."""
    frames = sidtrace.loop_frame(ref_psid, labels["posoff"], LOOP_LIMIT)
    runs = [(ref_psid, [frames], [0])]
    runs += [
        (patched(ref_psid, labels, p), [STOP_FRAMES], [0]) for p in VARIANTS.values()
    ]
    orig = footprint.Original(runs, 1)
    held = gen_data.held(ref_psid, labels)
    assert (
        footprint.check(orig, held, gen_data.named(ref_psid, labels)) == footprint.CLEAN
    )


def test_mismatch_reported(ref_psid, labels, tmp_path, capsys):
    """A differing song is reported as a mismatch."""
    psid = patched(ref_psid, labels, VARIANTS["stop"])
    prg = build_prg(psid, labels, tmp_path)
    args = [str(BUILD / "ref.sid"), str(prg)]
    assert sidtrace.main(args + ["--frames", str(STOP_FRAMES)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
