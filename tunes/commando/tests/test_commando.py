"""Tests for tools/sidtrace.py: the C translation against the original."""

import pytest

from support import BUILD, build_prg
import sidtrace

STOP_FRAMES = 2500


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


def _patched(psid, labels, patches):
    data = bytearray(psid)
    for label, offset, value in patches:
        data[labels[label] - 0x1000 + 0x7E + offset] = value
    return bytes(data)


INS = 8
VARIANTS = {
    "stop": [("song1t1", 4, 0xFE)],
    "skydive_vibrato_pwl": [
        ("ins_pwl", 2 * INS + 5, 1),
        ("ins_pwl", 2 * INS + 7, 0x0A),
        ("ins_pwl", 3 * INS + 7, 0x07),
        ("p02", 0, 0x86),
        ("p02", 2, 0x5F),
    ],
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(name, ref_psid, labels, tmp_path):
    """Patched songs exercise stop, skydive and vibrato carry paths."""
    psid = _patched(ref_psid, labels, VARIANTS[name])
    prg = build_prg(psid, labels, tmp_path)
    ref = sidtrace.trace_psid(psid, STOP_FRAMES)
    assert sidtrace.diff(ref, sidtrace.trace_prg(prg, STOP_FRAMES)) is None
    if name == "stop":
        assert not ref[-1] and (0xD418, 0x0F) in sum(ref[2:], [])


def test_mismatch_reported(ref_psid, labels, tmp_path, capsys):
    """A differing song is reported as a mismatch."""
    psid = _patched(ref_psid, labels, VARIANTS["stop"])
    prg = build_prg(psid, labels, tmp_path)
    args = [str(BUILD / "ref.sid"), str(prg)]
    assert sidtrace.main(args + ["--frames", str(STOP_FRAMES)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
