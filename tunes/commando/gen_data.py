"""Generate commando_data.h from the assembled original PSID and its ACME labels."""

from jennings.acme import read_symbol_list

from header import c_bytes, cli, defines
from sidtrace import psid_parts


def held(psid, labels):
    """{array: original addresses}: the song data to the image's end, the frequency table
    and the voice offsets."""
    load, _, _, image = psid_parts(psid)
    return {
        "song": range(labels["_songdata"], load + len(image)),
        "v": range(labels["freqsl"], labels["posoff"]),
        "sid": range(labels["sid"], labels["sid"] + 3),
    }


def named(_psid, labels):
    """{state: original addresses} the C keeps as variables."""
    return {
        "voice state, status, tempo, temporaries": range(
            labels["posoff"], labels["sid"]
        )
    }


def generate(psid, labels):
    """Return header text for the song data image."""
    load, _, _, image = psid_parts(psid)
    end = load + len(image)

    def span(lo, hi):
        return image[lo - load : hi - load]

    org, freq = labels["_songdata"], labels["freqsl"]
    freq_end = labels["posoff"]
    defs = {
        "SONG_ORG": f"0x{org:04x}",
        "SONG_DATA": c_bytes(span(org, end)),
        "FREQ_LEN": str(freq_end - freq),
        "FREQ_TABLE": c_bytes(span(freq, freq_end)),
        "SID_OFFSETS": c_bytes(span(labels["sid"], labels["sid"] + 3)),
    }
    for name in ("songptrs", "songtempos", "patpl", "patph"):
        defs[name.upper()] = str(labels[name] - org)
    defs["INS"] = str(labels["ins_pwl"] - org)
    return defines("tools/gen_data.py", defs)


def main(argv=None):
    """CLI entry point."""
    cli(generate, __doc__, argv, labels=read_symbol_list)


if __name__ == "__main__":
    main()
