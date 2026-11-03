"""Tests for tools/disasm.py and tools/survey.py on a real PSID."""

import hashlib
import json
import re

import pytest

import disasm
import mutate
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


def test_survey_hvsc(sample_psid, tmp_path):
    """With an HVSC root, each tune plays its song length in play calls."""
    (tmp_path / "DOCUMENTS").mkdir()
    (tmp_path / "DOCUMENTS" / "Songlengths.md5").write_text(
        f"{hashlib.md5(sample_psid).hexdigest()}=0:01\n", encoding="latin-1"
    )
    sid = tmp_path / "a.sid"
    sid.write_bytes(sample_psid)
    calls = survey._job(str(sid), str(tmp_path))[1][2]  # pylint: disable=W0212
    assert calls == sidtrace.song_calls(sample_psid, 0, tmp_path) == 50


def test_disasm_songs(sample_psid, tmp_path):
    """Coverage of several subtunes merges; lengths may come from Songlengths."""
    sid, out = tmp_path / "t.sid", tmp_path / "t.s"
    sid.write_bytes(sample_psid)
    (tmp_path / "DOCUMENTS").mkdir()
    (tmp_path / "DOCUMENTS" / "Songlengths.md5").write_text(
        f"{hashlib.md5(sample_psid).hexdigest()}=0:02\n", encoding="latin-1"
    )
    disasm.main([str(sid), str(out), "--song", "1", "--hvsc", str(tmp_path)])

    def code(text):
        return set(re.findall(r"^([0-9A-F]{4})  (?!.*\.byte)", text, re.M))

    merged = code(out.read_text(encoding="ascii"))
    assert code(disasm.listing(sample_psid, 50, 0).source) <= merged
    assert merged == code(disasm.listing(sample_psid, [50, 100], [0, 0]).source)


def test_one_way(sample_psid):
    """One-way branches name their untaken target, and shrink with longer runs."""
    short = disasm.coverage(sample_psid, 5)
    gaps = disasm.one_way(sample_psid, short)
    assert gaps and all(short.flags[pc] for pc, _ in gaps)
    longer = disasm.coverage(sample_psid, 400)
    longer |= short
    assert disasm.one_way(sample_psid, longer) < gaps


def test_mutate(tmp_path, capsys):
    """Mutants the command passes survive; the source is restored."""
    src = tmp_path / "s.c"
    src.write_text("a = 1;\n", encoding="utf-8")
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([["1", "2"], ["a", "b"]]), encoding="utf-8")
    assert mutate.main([str(src), str(edits), "grep", "-q", "= 1", str(src)]) == 1
    assert "SURVIVED 1: 'a' -> 'b'" in capsys.readouterr().out
    assert src.read_text(encoding="utf-8") == "a = 1;\n"
    with pytest.raises(AssertionError):
        list(mutate.mutants("aa", [["a", "b"]]))
