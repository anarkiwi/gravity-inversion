"""Generate automatas_data.h from the original PSID: its data and the state it keeps in code."""

from header import c_bytes, cli, defines
from sidtrace import psid_parts

WORDS_ORG, CODE_ORG, TABLES_ORG = 0x1554, 0x168C, 0x177F
# play's code bytes the word table's high bytes reach past its end
CODE_READ = (
    *range(0x168C, 0x168F),
    *(0x1697, 0x1698, 0x169D, *range(0x16A2, 0x16A6), 0x16A7, 0x16A8),
    *(0x16B3, 0x16B4, 0x16BC, 0x16BF, 0x16C0),
)
VOICE_STRIDE = 0x31
CUT_OPS = {0x69: 0, 0xE9: 1}
CUT_OPCODES = (0x10B8, 0x10BF)
# per-voice state the player keeps in its code image, initialised from it
VOICE_STATE = {
    "slide": (0x1019, 0x101A),
    "detune": (0x101F,),
    "route_or": (0x1020,),
    "route_and": (0x1021,),
    "pw_lo": (0x1023,),
    "pw_hi": (0x1025,),
    "freq_lo": (0x102D,),
    "freq_hi": (0x102F,),
    "note_base": (0x12CC,),
}


def held(psid):
    """{array: original addresses}: the word table, the code bytes read past it, and the
    tables and streams from TABLES_ORG to the image's end."""
    load, _, _, image = psid_parts(psid)
    return {
        "words": range(WORDS_ORG, CODE_ORG),
        "code_read": CODE_READ,
        "tables": range(TABLES_ORG, load + len(image)),
    }


def named(_psid):
    """{state: original addresses} the player keeps in data gaps of its code, per voice."""
    fields = {
        "voice slide, pitch, sweep, detune, routing bits": (
            0x1019,
            0x101A,
            0x101B,
            *range(0x101E, 0x1022),
        ),
        "voice note base, note": (0x12CC, 0x135E),
    }
    return {
        k: tuple(a + VOICE_STRIDE * v for v in range(3) for a in addrs)
        for k, addrs in fields.items()
    }


def generate(psid):
    """Return header text for the data image and the initial player state."""
    load, _, _, image = psid_parts(psid)

    def byte(addr):
        return image[addr - load]

    voices = []
    for v in range(3):
        fields = []
        for name, addrs in VOICE_STATE.items():
            value = sum(
                byte(a + v * VOICE_STRIDE) << 8 * k for k, a in enumerate(addrs)
            )
            fields.append(f".{name}=0x{value:x}")
        voices.append("{" + ",".join(fields) + "}")
    subs = {CUT_OPS[byte(a)] for a in CUT_OPCODES}
    assert len(subs) == 1, "cutoff add and subtract opcodes disagree"
    arrays = held(psid)
    defs = {
        "WORDS_ORG": f"0x{WORDS_ORG:04x}",
        "WORDS": c_bytes(byte(a) for a in arrays["words"]),
        "CODE_READ": c_bytes(byte(a) for a in CODE_READ),
        "CODE_READ_ADDRS": ",".join(f"0x{a:04x}" for a in CODE_READ),
        "TABLES_ORG": f"0x{TABLES_ORG:04x}",
        "TABLES": c_bytes(byte(a) for a in arrays["tables"]),
        "VOICE_INIT": ",".join(voices),
        "CUT_SUB": str(subs.pop()),
    }
    return defines("gen_automatas.py", defs)


def main(argv=None):
    """CLI entry point."""
    cli(generate, __doc__, argv)


if __name__ == "__main__":
    main()
