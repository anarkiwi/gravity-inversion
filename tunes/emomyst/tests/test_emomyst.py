"""The C translation against the original: the tune, and patched variants."""

import pytest

import disasm
import sidtrace
import survey
import transliterate

# Branch arms the original can never take from init/play, by branch address.
DEAD = {
    0x11D9: "sound effects: only the host API starts one",
    0x11DB: "sound effects",
    0x12F2: "sound effects",
    0x1222: "slowdown: only the host API sets it",
    0x1294: "track-tempo orderlist fx: not in this build",
    0x1811: "frequency modifier index is clamped below $80",
    0x12C1: "BPL used as a jump",
    0x12D0: "BPL used as a jump",
    0x14CF: "BNE used as a jump",
    0x1621: "BCS used as a jump",
    0x1688: "BCS used as a jump",
    0x1690: "BCS used as a jump",
    0x16F8: "BCC used as a jump",
    0x1727: "BCS used as a jump",
    0x181F: "BEQ used as a jump",
    0x1951: "self-modified BCC used as a jump",
    0x1968: "BNE used as a jump",
    0x197C: "BNE used as a jump",
    0x1A13: "self-modified BCC used as a jump",
    0x1AB0: "BNE used as a jump",
    0x1ABF: "BNE used as a jump",
}


def test_whole_tune_identical(psid_path, prg, frames, capsys):
    """Every SID write matches the original over the HVSC length and twice it."""
    args = [str(psid_path), str(prg), "--hvsc", str(psid_path.parent)]
    assert sidtrace.main(args) == 0
    assert sidtrace.main(args + ["--times", "2"]) == 0
    out = capsys.readouterr().out
    assert f"OK: {frames} frames" in out and f"OK: {2 * frames} frames" in out


def _rows(m, size):
    return [r.addrs for r in m.rows() if len(r.addrs) == size]


def _instruments(m):
    return m.pointers(10, 12)[1:]


def _table_rows(m, start):
    """Row addresses of an instrument table, up to its $ff end row."""
    while m[start] != 0xFF:
        yield start
        start += 3
    yield start


def _seq_loop(m, voice):
    loop = m.orderlist(voice)
    while m[loop] != 0xFF:
        loop += 1
    return loop


def _orderlists(m):
    m[m.orderlist(0)] = 0x80  # skipped by init, replays the pattern after the loop
    m[_seq_loop(m, 0) + 1] = 0x80  # loop to position 0
    m[m.orderlist(1)] = 0xFE  # init leaves pattern 0
    m[m.table(8)] = 0  # pattern 0 at the SWP header


def _orderlist_end(m):
    m[_seq_loop(m, 0)] = 0xFE


def _vibrato_types(m):
    """Vibrato types 0/$10/$20/$30 with fast increase, some with bit 0 set."""
    values = [0x08, 0x38, 0x11, 0x28, 0x39, 0x01]
    for i, ins in enumerate(_instruments(m)):
        m[ins] = m[ins] & 0xC6 | values[i % len(values)]
        m[ins + 5] |= 0x03
        m[ins + 6] = 0x60


def _ctrl(mask, values):
    def patch(m):
        for i, ins in enumerate(_instruments(m)):
            m[ins] = m[ins] & ~mask | values[i % len(values)]

    return patch


def _tables(m):
    """Jumps into the last rows; arpeggio NOP/absolute; waveform $10; filter
    switch and odd band; chords return; volume 6 shows the band's low bit."""
    for i, ins in enumerate(_instruments(m)):
        wf = list(_table_rows(m, ins + 0x10))
        if len(wf) > 1:
            m[wf[-2]], m[wf[-2] + 1] = 0xFE, 0x10
        arps = [row + 1 for row in wf[:-1] if m[row + 1] != 0x7F]  # keep chords
        for arp, value in zip(arps, (0xE0, 0x80)):
            m[arp] = value
        if len(wf) > 3:
            m[wf[2]] = 0x10
        pos = m[ins + 10]
        pw = list(_table_rows(m, ins + pos))
        if len(pw) > 1:  # to the first row, or for one row to itself
            m[pw[-2]], m[pw[-2] + 1] = 0xFE, pos
        pos = m[ins + 11]
        flt = list(_table_rows(m, ins + pos))
        if len(flt) > 2:
            m[flt[0]] |= 1
            m[flt[0] + 2], m[flt[1] + 2] = 0x81, 0x90
            m[flt[-2]], m[flt[-2] + 1] = 0xFE, pos
        m[ins + 8] = 1 + i % 4
    chords = m.table(14)
    for addr in range(chords, m.table(16)):
        if m[addr] == 0x7F:
            m[addr] = 0x7E
    for row in _rows(m, 3)[::4]:
        m[row[2]] = 0xA6


def _keytrack_1952(m):
    """Octave $4e puts the commonest note at pitch $81; pulse keyboard track $73
    then reads the exponent table at $1952, the note fx branch operand."""
    _octave(0x4E)(m)
    for ins in _instruments(m):
        pw = list(_table_rows(m, ins + m[ins + 10]))
        if len(pw) > 1 and m[pw[0]] & 0x80:
            m[pw[0] + 2] = 0x73


def _filter_switch(m):
    """Instruments alternately filtered without control, and unfiltered."""
    for i, ins in enumerate(_instruments(m)):
        wf_end = list(_table_rows(m, ins + 0x10))[-1]
        m[ins + 11] = 2 if i % 2 else wf_end - ins  # HR SR byte 0, or $ff


def _octave(shift):
    def patch(m):
        for ins in _instruments(m):
            m[ins + 9] = shift

    return patch


def _gate_off_pointers(m):
    for ins in _instruments(m):
        m[ins + 12], m[ins + 13], m[ins + 14] = 0x13, m[ins + 10], m[ins + 11]


def _notes(values):
    def patch(m):
        rows = [a for a, *_ in _rows(m, 2) + _rows(m, 3) if m[a] != 0xFE]
        for k, row in enumerate(sorted(rows)[::7]):
            m[row] = 0x80 | values[k % len(values)]
        for (row,) in _rows(m, 1)[::9]:
            if m[row]:
                m[row] = 0x78  # a note, not a packed rest

    return patch


def _fx_column(size, values, ifx=False, pw_end=False):
    """fx column of the rows of `size`, cycling `values`; optionally instrument
    column $40 on some rows, and pulse tables that never change the width."""

    def patch(m):
        if pw_end:
            for ins in _instruments(m):
                m[ins + 10] = list(_table_rows(m, ins + 0x10))[-1] - ins
        if ifx:
            for row in _rows(m, 3)[1::5]:
                m[row[1]] = 0xC0
        for k, row in enumerate(_rows(m, size)):
            fx = values[k % len(values)]
            if size == 4:
                m[row[2]], m[row[3]] = fx
            else:
                m[row[2]] = fx

    return patch


# name: (patch, entry points first run, one-way branches it takes the other way)
VARIANTS = {
    "orderlists": (_orderlists, {0x1153}, {0x13CB, 0x13D1}),
    "orderlist_end": (_orderlist_end, {0x13C3}, {0x13C1}),
    "hr_gate_off": (_ctrl(0x04, [0x04]), {0x136F}, {0x136D}),
    "vibrato_types": (  # also without the first-frame waveform
        _vibrato_types,
        {0x1581},
        {0x153A, 0x1471, 0x17F2, 0x17F7},
    ),
    "keep_tables": (_ctrl(0xC0, [0xC0]), set(), {0x14A8, 0x14B4}),
    "tables": (
        _tables,
        set(),
        {
            0x16EA,
            0x161D,
            0x164C,
            0x1736,
            0x1721,
            0x1698,
            0x169D,
        },
    ),
    "filter_switch": (_filter_switch, set(), set()),
    "high_pitch": (_octave(0x30), set(), {0x1789, 0x180A}),
    "high_pitch_note_fx": (  # pitch $81 reads the note fx branch operand
        lambda m: (_keytrack_1952(m), _notes([0x78, 0x7F, 0x7D])(m)),
        set(),
        set(),
    ),
    "note_fx": (
        _notes([0x78, 0x79, 0x7A, 0x7C, 0x7F, 0x7B, 0x60]),
        {0x1953, 0x195D, 0x1961},
        {0x1327, 0x1446},
    ),
    "gate_off_pointers": (
        _gate_off_pointers,
        set(),
        {0x1982, 0x1999, 0x19A6},
    ),
    "small_fx": (
        _fx_column(
            3,
            [0x31, 0x95, 0xA8, 0xD3, 0xE2, 0xF5, 0xB4, 0x2A, 0x32, 0x64, 0x20],
            ifx=True,
        ),
        {0x1A1E, 0x1A5F, 0x1A60, 0x1A6E, 0x1A6F},
        set(),
    ),
    "big_fx": (
        _fx_column(
            4,
            [(9, 1), (10, 1), (11, 0), (13, 4), (14, 7), (15, 0x30)],
            pw_end=True,
        ),
        {0x1AC9, 0x1AD2, 0x1ADD, 0x1AE8, 0x1AEC, 0x1AF2},
        set(),
    ),
    "fast_slides": (
        _fx_column(4, [(1 + k % 2, v) for k, v in enumerate(range(0x80, 0x100, 5))]),
        set(),
        {0x1815},
    ),
    "tempo_fx": (
        _fx_column(4, [(0x10, 6), (0x11, 0x52), (0x11, 0x53), (0x12, 1), (0x10, 5)]),
        {0x1AF6, 0x1B10, 0x1B1F},
        {0x1258},
    ),
    "track_tempo_fx": (
        _fx_column(
            4, [(0x13, 6), (0x14, 0x65), (0x14, 0x64), (0x15, 1), (0x17, 0), (0x1F, 0)]
        ),
        {0x1B28, 0x1B38, 0x1B49, 0x1B52, 0x1B54},
        set(),
    ),
}


@pytest.fixture(scope="session", name="tune_cover")
def fixture_tune_cover(psid, frames):
    """(executed PCs, one-way branches with their untaken target) of the unpatched
    tune over twice its length."""
    cov = disasm.coverage(psid, 2 * frames)
    return survey.executed(psid, 2 * frames), disasm.one_way(psid, cov)


def test_one_way_branches_accounted(tune_cover):
    """Every branch the tune runs one way only is dead or taken the other way by a
    variant."""
    pcs, gaps = tune_cover
    one_way = {pc for pc, _ in gaps}
    covered = set().union(*(v[2] for v in VARIANTS.values()))
    assert covered <= one_way and one_way - covered == set(DEAD)
    assert not pcs & set().union(*(v[1] for v in VARIANTS.values()))


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    name, psid, gen, swp, frames, tune_cover, tmp_path
):
    """Patched data runs paths the tune does not; the C matches on them."""
    patch, pcs, branches = VARIANTS[name]
    m = swp.Music(psid, gen)
    patch(m)
    data = m.bytes()
    assert pcs | branches <= survey.executed(data, frames)
    untaken = {gap for gap in tune_cover[1] if gap[0] in branches}
    assert not untaken & disasm.one_way(data, disasm.coverage(data, frames))
    prg = swp.build_prg(data, gen, tmp_path)
    ref = sidtrace.trace_psid(data, frames)
    assert sidtrace.diff(ref, sidtrace.trace_prg(prg, frames)) is None


GEN_MISSED = {}


@pytest.mark.slow
@pytest.mark.parametrize(
    "name",
    [
        (
            pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=GEN_MISSED[n]))
            if n in GEN_MISSED
            else n
        )
        for n in sorted(VARIANTS)
    ],
)
def test_variant_generated(  # pylint: disable=too-many-positional-arguments
    name, psid, gen, swp, frames, tmp_path
):
    """The C generated from each patched variant matches it."""
    m = swp.Music(psid, gen)
    VARIANTS[name][0](m)
    data = m.bytes()
    prg = transliterate.build(data, tmp_path)[0]
    ref = sidtrace.trace_psid(data, frames)
    assert sidtrace.diff(ref, sidtrace.trace_prg(prg, frames)) is None


def test_mismatch_reported(psid_path, gen, swp, tmp_path, capsys):
    """C built from other data is reported as a mismatch."""
    psid = psid_path.read_bytes()
    m = swp.Music(psid, gen)
    _orderlist_end(m)
    prg = swp.build_prg(m.bytes(), gen, tmp_path)
    args = [str(psid_path), str(prg), "--hvsc", str(psid_path.parent)]
    assert sidtrace.main(args) == 1
    assert "MISMATCH" in capsys.readouterr().out
