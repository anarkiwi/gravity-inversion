"""Tests for tools/sidplot.py."""

import numpy as np
import pytest

import sidplot


def grid():
    """Two frames of a register grid with known 16-bit, 12-bit and 11-bit values."""
    g = np.zeros((2, 25), np.int64)
    g[1, 0:2] = (0x34, 0x12)
    g[1, 7 + 2 : 7 + 4] = (0xCD, 0xFB)
    g[1, 14 + 4] = 0x41
    g[1, 21:23] = (0xFF, 0x80)
    g[1, 24] = 0x1F
    return g


def test_series_combines_register_pairs():
    """Frequency, pulse and cutoff combine their register pairs and masks."""
    got = {name: values for name, values, _, _ in sidplot.series(grid())}
    assert got["freq 1"].tolist() == [0, 0x1234]
    assert got["pulse 2"].tolist() == [0, 0xBCD]
    assert got["control 3"].tolist() == [0, 0x41]
    assert got["cutoff"].tolist() == [0, 0x400 | 0x07]
    assert got["mode/volume"].tolist() == [0, 0x1F]
    assert len(got) == 3 * 5 + 3


def test_render_writes_an_image(tmp_path):
    """The plot is written as a PNG."""
    out = tmp_path / "regs.png"
    sidplot.render(grid(), out, "t", start=10)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_main_reads_the_capture(tmp_path, monkeypatch):
    """The CLI reads the song's capture over start + frames frames."""
    psid = tmp_path / "t.sid"
    psid.write_bytes(b"PSID")
    calls = {}

    def capture(data, song, hvsc, root):
        calls["capture"] = (data, song, hvsc, root)
        return "cap.csv.zst"

    def frames(path, nframes):
        calls["frames"] = (path, nframes)
        return nframes, np.tile(grid()[1], (nframes, 1))

    monkeypatch.setattr(sidplot, "hvsc_capture", capture)
    monkeypatch.setattr(sidplot, "oracle_frames", frames)
    out = tmp_path / "o.png"
    sidplot.main(
        [
            str(psid),
            str(out),
            "--hvsc",
            "H",
            "--song",
            "2",
            "--start",
            "3",
            "--frames",
            "4",
        ]
    )
    assert calls["capture"] == (b"PSID", 1, "H", "H")
    assert calls["frames"] == ("cap.csv.zst", 7)
    assert out.exists()


def test_main_without_a_capture_fails(tmp_path, monkeypatch):
    """A song with no capture is a usage error."""
    psid = tmp_path / "t.sid"
    psid.write_bytes(b"PSID")
    monkeypatch.setattr(sidplot, "hvsc_capture", lambda *_: None)
    with pytest.raises(SystemExit):
        sidplot.main([str(psid), str(tmp_path / "o.png"), "--hvsc", "H"])
