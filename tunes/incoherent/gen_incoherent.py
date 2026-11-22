"""Generate incoherent_data.h from the original PSID: the data image and the player
state the original keeps in its image."""

from header import c_bytes, cli, defines
from sidtrace import psid_parts

DATA_ORG = 0x0CCF
VOICE_STRIDE = 7
BYTE_FIELDS = {
    "song_pos": 0x0C66,
    "transpose": 0x0C67,
    "patt_pos": 0x0C69,
    "repeat": 0x0C6A,
    "cmd": 0x0C6B,
    "cmd_data": 0x0C6C,
    "effect": 0x0C7B,
    "param": 0x0C7C,
    "new_note": 0x0C7D,
    "wave_ptr": 0x0C7E,
    "wave": 0x0C7F,
    "pulse_ptr": 0x0C80,
    "pulse_time": 0x0C81,
    "order": 0x0C90,
    "pattern": 0x0C91,
    "tempo": 0x0C92,
    "tick": 0x0C93,
    "note": 0x0C94,
    "instr": 0x0C95,
    "gate": 0x0C96,
    "vib_phase": 0x0CA5,
    "vib_delay": 0x0CA6,
    "wave_time": 0x0CA7,
    "ad": 0x0CBA,
    "sr": 0x0CBB,
    "gate_timer": 0x0CBF,
    "last_note": 0x0CC0,
}
WORD_FIELDS = {"freq": 0x0CA8, "pulse": 0x0CAA}
# filter state in code operands: table pointer, step time, cutoff, control, type
FILTER = {
    "ptr": 0x0933,
    "time": 0x0937,
    "cutoff": 0x097C,
    "ctrl": 0x0981,
    "type": 0x0986,
}
FUNKTEMPO = 0x0C64
SCALARS = {"INIT_SONG": 0x08FF, "INIT_TEMPO": 0x0923, "HR_LIMIT": 0x0BDB}


def held(psid):
    """{array: original addresses}: the image from DATA_ORG to its end."""
    load, _, _, image = psid_parts(psid)
    return {"data": range(DATA_ORG, load + len(image))}


def named(_psid):
    """{state: original addresses} the C keeps as variables or in its code."""
    voice = {
        a + VOICE_STRIDE * v + k
        for v in range(3)
        for a, n in [(a, 1) for a in BYTE_FIELDS.values()]
        + [(a, 2) for a in WORD_FIELDS.values()]
        for k in range(n)
    }
    return {
        "command switch": range(0x0C4F, FUNKTEMPO),
        "funktempo": range(FUNKTEMPO, FUNKTEMPO + 2),
        "voices": tuple(sorted(voice)),
    }


def _init(fields):
    return "{" + ",".join(f".{k}=0x{v:x}" for k, v in fields.items()) + "}"


def generate(psid):
    """Return header text for the data image and initial player state."""
    load, _, _, image = psid_parts(psid)

    def byte(addr):
        return image[addr - load]

    def voice(v):
        off = v * VOICE_STRIDE
        out = {k: byte(a + off) for k, a in BYTE_FIELDS.items()}
        out.update(
            {k: byte(a + off) | byte(a + off + 1) << 8 for k, a in WORD_FIELDS.items()}
        )
        return _init(out)

    defs = {
        "DATA_ORG": f"0x{DATA_ORG:04x}",
        "DATA": c_bytes(image[DATA_ORG - load :]),
        "VOICE_INIT": "{" + ",".join(voice(v) for v in range(3)) + "}",
        "FILTER_INIT": _init({k: byte(a) for k, a in FILTER.items()}),
        "INIT_FUNKTEMPO": f"{{{c_bytes(image[FUNKTEMPO - load : FUNKTEMPO - load + 2])}}}",
    }
    defs.update({k: f"0x{byte(a):02x}" for k, a in SCALARS.items()})
    return defines("gen_incoherent.py", defs)


def main(argv=None):
    """CLI entry point."""
    cli(generate, __doc__, argv)


if __name__ == "__main__":
    main()
