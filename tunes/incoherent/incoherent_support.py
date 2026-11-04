"""Paths, patched variants and the C build used by the Incoherent Nightmare tests."""

import os
import pathlib
import subprocess

from gen_incoherent import generate
from transliterate import build

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
FIX = ROOT.parent.parent / "fixtures"
SID = FIX / "Incoherent_Nightmare_tune_2.sid"
DRIVER = ROOT.parent.parent / "c64"
LOAD = 0x0800
PSID_HEADER = 0x7E

FREQ_HI, SPEED_L, SPEED_R = 0x0D2F, 0x12CA, 0x12E2
INS_FIRST_WAVE, WAVE_L = 0x0FE0, 0x100B
PATTERN_52 = 0x1CE9
INSTRUMENTS, NOTES, SPEEDS = range(1, 44), range(96), range(1, 24)

# {name: (patches {address: byte}, same output as the unpatched tune)}
VARIANTS = {
    "first_wave_none_or_gate": (
        {INS_FIRST_WAVE + i: 0 if i & 1 else 0xFE for i in INSTRUMENTS},
        False,
    ),
    "wave_table_effects": (
        {
            WAVE_L + 0x17: 0xE4,
            WAVE_L + 0x1B: 0xE0,
            WAVE_L + 0x1E: 0xE1,
            WAVE_L + 0x44: 0xE2,
            WAVE_L + 0x63: 0xE3,
            WAVE_L + 0x6B: 0xEF,
        },
        False,
    ),
    "relative_speeds_wide_semitones": (
        {SPEED_L: 0x80}
        | {SPEED_L + s: 0x80 for s in SPEEDS}
        | {SPEED_R + s: 0 for s in SPEEDS}
        | {FREQ_HI + n: n * 0x9D & 0xFF for n in NOTES},
        False,
    ),
    "speed_entry_zero": ({SPEED_R: 0x10}, False),
    "wave_delay_wraps": (
        {WAVE_L + 1: 0x02} | {0x0CA7 + 7 * v: 0x20 for v in range(3)},
        False,
    ),
    "vibrato_between_porta_rows": (
        {PATTERN_52 + k: 0x54 for k in (16, 20, 24)}
        | {PATTERN_52 + k: 0x0D for k in (17, 21, 25)},
        False,
    ),
    "command_below_0x40_after_instrument": ({0x16A4: 0x0A}, True),
}


def patched(psid, patches):
    """`psid` with image bytes at original addresses replaced."""
    data = bytearray(psid)
    for addr, value in patches.items():
        data[addr - LOAD + PSID_HEADER] = value
    return bytes(data)


def build_prg(psid, out_dir):
    """Compile the C player against data generated from `psid`; return its path."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "incoherent_data.h").write_text(generate(psid), encoding="ascii")
    prg = out_dir / "incoherent.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{out_dir}", "-o", prg]
        + [f"-I{DRIVER}", str(ROOT / "src" / "incoherent.c")]
        + [str(DRIVER / "driver.c")],
        check=True,
    )
    return prg


def gen_prg(psid, out_dir):
    """Compile the C transliterated from `psid`; return its path."""
    return build(psid, out_dir)[0]
