"""Generate emomyst_data.h from the original SID-Wizard PSID."""

from jennings.coverage.cfg import JMP

from header import c_bytes, cli, defines
from sidtrace import psid_parts

TABLES_ORG = 0x1830
TABLES_END = 0x1939  # the player's code follows the frequency-low table
# code bytes 8-bit indexed reads of the frequency-low table reach past its end
CODE_READ = (
    *range(TABLES_END, 0x195F),
    *(0x1960, 0x1962, 0x1963, 0x1965, 0x1968, 0x1969, 0x196B, 0x196C, 0x196D, 0x196F),
)
LDX_IMM, LDY_IMM = 0xA2, 0xA0


def music_org(load, init, image):
    """Base address of the music data: init jumps to LDX #lo / LDY #hi."""

    def at(addr, n):
        return image[addr - load : addr - load + n]

    op, lo, hi = at(init, 3)
    assert op == JMP, "init is not a JMP"
    ldx, base_lo, ldy, base_hi = at(lo | hi << 8, 4)
    assert (ldx, ldy) == (LDX_IMM, LDY_IMM), "init does not load the music base"
    return base_lo | base_hi << 8


def named(_psid):
    """{what the C has instead: original addresses} of the data it does not hold."""
    return {
        "ch[] and the player's variables": range(0x1024, 0x10A5),
        "player variables in code gaps": (0x1191, 0x1788),
        "SLOWDOWN 0: the slowdown table's first entry": (0x11AB,),
        "switches over the fx": (*range(0x19F1, 0x1A00), *range(0x1A6E, 0x1AAE)),
        "tune_init's table addresses: the relocator's tables": range(0x1B55, 0x1BD0),
    }


def held(psid):
    """{array: original addresses}: the player's tables, the code bytes reads past them
    reach, and the music data to the image's end."""
    load, init, _, image = psid_parts(psid)
    return {
        "tab": range(TABLES_ORG, TABLES_END),
        "code_read": CODE_READ,
        "music": range(music_org(load, init, image), load + len(image)),
    }


def generate(psid):
    """Return header text: player tables and the music data, as loaded."""
    load, init, _, image = psid_parts(psid)
    org = music_org(load, init, image)
    defs = {
        "TABLES_ORG": f"0x{TABLES_ORG:04x}",
        "TABLES": c_bytes(image[TABLES_ORG - load : TABLES_END - load]),
        "CODE_READ": c_bytes(image[a - load] for a in CODE_READ),
        "CODE_READ_ADDRS": ",".join(f"0x{a:04x}" for a in CODE_READ),
        "MUSIC_ORG": f"0x{org:04x}",
        "MUSIC": c_bytes(image[org - load :]),
    }
    return defines("gen_emomyst.py", defs)


def main(argv=None):
    """CLI entry point."""
    cli(generate, __doc__, argv)


if __name__ == "__main__":
    main()
