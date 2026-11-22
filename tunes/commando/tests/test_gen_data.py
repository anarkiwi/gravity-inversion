"""Tests for tools/gen_data.py."""

import re

from pysidtracker.header import write_psid

import gen_data
import sidtrace


def _psid(load, payload):
    return write_psid(load=load, init=load, play=load, image=payload, embed_load=True)


def _array(header, name):
    text = re.search(rf"#define {name} (.*)", header)[1]
    return bytes(int(b, 16) for b in text.split(","))


def test_generate_is_byte_identical(ref_psid, labels):
    """Generated tables are byte-identical to the assembled original."""
    header = gen_data.generate(ref_psid, labels)
    load, _, _, image = sidtrace.psid_parts(ref_psid)
    org, freq = labels["_songdata"], labels["freqsl"]
    assert _array(header, "SONG_DATA") == image[org - load :]
    assert _array(header, "FREQ_TABLE") == image[freq - load : labels["posoff"] - load]
    assert _array(header, "SID_OFFSETS") == b"\x00\x07\x0e"
    assert f"#define SONG_ORG 0x{org:04x}" in header
    assert f"#define INS {labels['ins_pwl'] - org}" in header


def test_main(tmp_path):
    """CLI writes a header from a synthetic PSID."""
    psid = tmp_path / "t.sid"
    psid.write_bytes(_psid(0x1000, bytes(range(16))))
    labels = tmp_path / "t.labels"
    labels.write_text(
        "".join(
            f"\t{n}\t= ${a:04x}\n"
            for n, a in {
                "_songdata": 0x1008,
                "freqsl": 0x1000,
                "posoff": 0x1004,
                "sid": 0x1005,
                "songptrs": 0x1008,
                "songtempos": 0x100E,
                "patpl": 0x100F,
                "patph": 0x100F,
                "ins_pwl": 0x100F,
            }.items()
        ),
        encoding="ascii",
    )
    out = tmp_path / "t.h"
    gen_data.main([str(psid), str(labels), str(out)])
    text = out.read_text(encoding="ascii")
    assert "#define FREQ_LEN 4" in text
    assert "#define SONG_DATA 0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f" in text
