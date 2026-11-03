"""Tests for tools/disasm.py and tools/survey.py on a real PSID."""

import hashlib
import re

import disasm
import sidtrace
import survey


def _listing_bytes(text):
    """Address -> byte from the hex columns of a jennings listing."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"([0-9A-F]{4})  ((?:[0-9A-F]{2} )*)", line)
        if m:
            for k, b in enumerate(m[2].split()):
                out[int(m[1], 16) + k] = int(b, 16)
        m = re.match(r"([0-9A-F]{4}) +\.byte (.*)", line)
        if m:
            for k, b in enumerate(m[2].split(",")):
                out[int(m[1], 16) + k] = int(b.strip("$"), 16)
    return out


def test_disasm_covers_image(sample_psid, tmp_path):
    """The listing decodes every executed PC and shows every payload byte as loaded."""
    load, _, play, image = sidtrace.psid_parts(sample_psid)
    sid, out = tmp_path / "t.sid", tmp_path / "t.s"
    sid.write_bytes(sample_psid)
    disasm.main([str(sid), str(out), "--frames", "200"])
    shown = _listing_bytes(out.read_text(encoding="ascii"))
    assert all(shown[load + k] == b for k, b in enumerate(image))
    assert (tmp_path / "t.s.jcov").exists()
    lst = disasm.listing(sample_psid, 200)
    assert lst.line(play) is not None
    assert all(lst.line(pc) is not None for pc in survey.executed(sample_psid, 200))


def test_survey(sample_psid, tmp_path, capsys, monkeypatch):
    """Tunes are ranked by unique PCs; non-PSID files are skipped."""
    good, bad = tmp_path / "a.sid", tmp_path / "b.sid"
    good.write_bytes(sample_psid)
    bad.write_bytes(b"RSID" + bytes(0x80))
    pcs, _, frames = survey.coverage(sample_psid, 20)
    assert pcs > 100 and frames == 20
    monkeypatch.setattr("sys.stdin", [f"{good}\n", f"{bad}\n"])
    survey.main(["--frames", "20", "--jobs", "2"])
    rows = capsys.readouterr().out.splitlines()
    assert rows[0] == "path,pcs,cycles,frames" and rows[1].startswith(str(good))
    assert len(rows) == 2


def test_survey_songlengths(sample_psid, tmp_path):
    """HVSC song lengths select each tune's frame count."""
    path = tmp_path / "Songlengths.md5"
    path.write_text(
        f"{hashlib.md5(sample_psid).hexdigest()}=0:01\n", encoding="latin-1"
    )
    lengths = survey.songlengths(path)
    assert list(lengths.values()) == [[50]]
    sid = tmp_path / "a.sid"
    sid.write_bytes(sample_psid)
    assert survey._job(str(sid), lengths)[1][2] == 50  # pylint: disable=W0212
