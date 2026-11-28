"""Generated GoatTracker 2 songs: their features, playroutines, layout and checks."""

import json

import numpy as np
import pytest
from pygoattracker import Player
from pygoattracker import typed as T
from pygoattracker.model import Repeat, Transpose, entry_from_byte
from pygoattracker.packed import pattern_rows
from pygoattracker.songimage import BASE
from pysidtracker.header import parse_sid_header
from pysidtracker.oracle import grid_from_calls

import gensongs
import gtpack
import liftcheck
import songchecks
import sidtrace

FRAMES = 400


@pytest.fixture(name="smoke", scope="module")
def fixture_smoke(tmp_path_factory):
    """The smoke run's directory and results."""
    out = tmp_path_factory.mktemp("smoke")
    return out, gensongs.smoke(out, 2)


def _steps(size, build, seed=0):
    return gensongs.specs(seed, 1, size, "sweep", build=build)


@pytest.mark.parametrize("size", ["tiny", "small", "large"])
@pytest.mark.parametrize("build", ["tailored", "full"])
def test_songs_use_their_features(size, build):
    """Each sweep song uses exactly the features it was made with, read off its data."""
    for spec in _steps(size, build, seed=3):
        assert gensongs.uses(spec.song()) == spec.features


def test_sweep_adds_one_feature_a_step():
    """The sweep starts plain and adds one feature (with what it needs) per step."""
    steps = gensongs.sweep()
    assert steps[0] == () and set(steps[-1]) == set(gensongs.FEATURES)
    for a, b in zip(steps, steps[1:]):
        assert set(a) <= set(b) and len(set(b) - set(a)) <= 1


def test_closure_rejects_unknown():
    """A feature the generator does not know is an error."""
    with pytest.raises(ValueError):
        gensongs.closure(["nosuch"])
    assert gensongs.closure(["setpulseptr"]) == ("pulse", "setpulseptr")


def test_fixed_features_share_one_playroutine():
    """Songs of one fixed feature set have byte-identical playroutines; another set's
    playroutine differs."""
    one = {
        s.pack()[1].playroutine()
        for s in gensongs.specs(
            5, 3, "small", "fixed", features=("vibrato", "pulsemod")
        )
    }
    other = gensongs.specs(5, 1, "small", "fixed", features=("vibrato",))[0].pack()[1]
    assert len(one) == 1 and other.playroutine() not in one


def test_full_build_is_one_playroutine_over_sizes():
    """The full build holds the playroutine constant whatever the song uses."""
    codes = {
        s.pack()[1].playroutine()
        for size in ("tiny", "medium")
        for s in _steps(size, "full")[::4]
    }
    assert len(codes) == 1


def test_tailored_removes_code():
    """A plain song's tailored playroutine is smaller than the full build's, and a
    feature's define comes back when a song uses it."""
    plain = _steps("tiny", "tailored")[0]
    song, packed = plain.pack()
    full = gtpack.pack(song, gensongs.FULL)
    assert len(packed.playroutine()) < len(full.playroutine())
    assert packed.defines["NOPULSE"] == 1 and full.defines["NOPULSE"] == 0
    pulse = gensongs.specs(0, 1, "tiny", "fixed", features=("pulse",))[0].pack()[1]
    assert pulse.defines["NOPULSE"] == 0 and pulse.defines["NOVIB"] == 1


@pytest.mark.parametrize("build", ["tailored", "full"])
def test_6502_plays_as_the_spec(build):
    """The packed 6502 image's SID writes equal pygoattracker's playspec of the song after
    the first frame, for every subtune, sweep step by step."""
    for spec in _steps("small", build, seed=7)[::3]:
        song, packed = spec.pack()
        psid = packed.psid()
        for sub in range(len(song.subtunes)):
            want = np.asarray(Player(song, subtune=sub).render_grid(FRAMES))
            got = np.asarray(
                grid_from_calls(sidtrace.trace_psid(psid, FRAMES, song=sub))
            )
            assert (want[1:] == got[1:FRAMES]).all(), (spec.name, sub)


def _song_and_layout(spec):
    song, packed = spec.pack()
    return song, packed, gtpack.layout(packed)


def test_layout_holds_the_song():
    """The bytes at each layout address read back as the song's data."""
    spec = gensongs.specs(2, 1, "medium", "fixed")[0]
    song, packed, lay = _song_and_layout(spec)
    mem = np.zeros(0x10000, np.uint8)
    mem[BASE : BASE + len(packed.image)] = np.frombuffer(packed.image, np.uint8)
    tables = {t["name"]: t for t in lay["tables"]}

    def column(name):
        t = tables[name]
        return mem[t["address"] : t["address"] + t["length"]].tolist()

    ins = packed.song.instruments
    assert column("mt_insad") == [i.attack_decay for i in ins]
    assert column("mt_inssr") == [i.sustain_release for i in ins]
    assert column("mt_inswaveptr") == [i.wave_ptr for i in ins]
    speed = [
        T.speed_row(a, b)
        for a, b in zip(column("mt_speedlefttbl"), column("mt_speedrighttbl"))
    ]
    assert speed == list(song.speedtable)
    pulse = [
        T.pulse_row(a, b)
        for a, b in zip(column("mt_pulsetimetbl"), column("mt_pulsespdtbl"))
    ]
    assert pulse == list(song.pulsetable)
    for o in lay["orderlists"]:
        raw = mem[o["address"] : o["address"] + o["length"]].tolist()
        assert raw[-2] == 0xFF
        want = song.subtunes[o["subtune"]].channels[o["channel"]]
        assert raw[-1] == want.restart
        got = _entries(raw[:-2])
        assert got == list(want.entries)
    for k, p in enumerate(lay["patterns"]):
        rows, end = pattern_rows(mem, p["address"])
        assert end == p["address"] + p["length"]
        assert tuple(rows) == gtpack.packed_pattern(song.patterns[k])
    lo, hi = lay["code"]
    assert packed.image[lo - BASE : hi - BASE] == packed.playroutine()
    assert lo <= lay["init"] < hi and lo <= lay["play"] < hi


def _entries(raw):
    """Orderlist entries of packed bytes: a repeat trails the pattern it repeats."""
    out = []
    for b in raw:
        e = entry_from_byte(b)
        if isinstance(e, Repeat) and out:
            out.insert(len(out) - 1, e)
        else:
            out.append(e)
    return out


def test_play_reads_only_the_layout():
    """Every load play makes outside zero page, the stack, I/O and its own code is of a
    layout table, orderlist or pattern; every orderlist and pattern is read."""
    spec = gensongs.specs(
        4, 1, "small", "fixed", features=("transpose", "repeat", "pulse")
    )[0]
    _, packed, lay = _song_and_layout(spec)
    play, states = liftcheck.song_states(packed.psid())
    keys = songchecks.orbit(states[0], play, 50_000)[2]
    loads = np.unique(keys[keys >> 16 & 1 == 0] & 0xFFFF)
    ok = np.zeros(0x10000, bool)
    ok[:0x200] = ok[0xD000:0xE000] = True
    ok[lay["code"][0] : lay["code"][1]] = True
    spans = [*lay["tables"], *lay["orderlists"], *lay["patterns"]]
    for t in spans:
        ok[t["address"] : t["address"] + t["length"]] = True
    assert ok[loads].all(), [hex(a) for a in loads[~ok[loads]]]
    read = np.zeros(0x10000, bool)
    read[loads] = True
    for t in [*lay["orderlists"], *lay["patterns"]]:
        assert read[t["address"]], t["name"]


def test_psid_header():
    """The PSID declares one song per subtune and the playroutine's entries."""
    spec = gensongs.specs(0, 1, "tiny", "fixed", features=("multisong",))[0]
    song, packed = spec.pack()
    head = parse_sid_header(packed.psid("x"))
    assert head.songs == len(song.subtunes) > 1
    assert (head.init_address, head.play_address) == (packed.init, packed.play)


def test_noeffects_packs_no_commands():
    """A NOEFFECTS build's patterns hold no commands, and its layout says so."""
    spec = gensongs.specs(0, 1, "tiny", "fixed", features=("vibrato",))[0]
    song = spec.song()
    full = gtpack.layout(gtpack.pack(song, gensongs.FULL))
    bare = gtpack.layout(gtpack.pack(song, gensongs.FULL | {"NOEFFECTS": 1}))
    assert bare["patterns"][0]["length"] < full["patterns"][0]["length"]


def test_nowavedelay_packs_waveforms():
    """A build without wave delays packs waveforms below $10 as greloc does."""
    song = gensongs.specs(0, 1, "tiny", "fixed", features=())[0].song()
    one = gtpack.pack(song, gensongs.tailored(song))
    two = gtpack.pack(song, gensongs.tailored(song) | {"NOWAVEDELAY": 0})
    at = one.symbols["mt_wavetbl"] - BASE
    n = len(song.wavetable)
    assert one.image[at : at + n] != two.image[at : at + n]


def test_assembly_errors():
    """A playroutine missing its data labels does not assemble."""
    with pytest.raises(gtpack.AssemblyError):
        gtpack.assemble({"base": gtpack.CODE, "zpbase": gtpack.ZP})


def test_certify_limits():
    """A call limit below the repeat is Open; a CPU limit cut short is a timeout."""
    spec = gensongs.specs(0, 1, "tiny", "fixed", features=("pulsemod",))[0]
    play, states = liftcheck.song_states(spec.pack()[1].psid())
    rec, cert = songchecks.certify(states[0], play, 3, 60)
    assert rec["form"] == "Open" and cert is None
    with songchecks.cpu_limit(60):
        pass
    with pytest.raises(TimeoutError):
        with songchecks.cpu_limit(0.01):
            while True:
                np.random.default_rng(0).random(1000).sort()


def test_compare_finds_a_missing_edge():
    """An edge the orbit takes and the certificate lacks is a difference."""
    spec = gensongs.specs(0, 1, "tiny", "fixed", features=("vibrato",))[0]
    play, states = liftcheck.song_states(spec.pack()[1].psid())
    _, cert = songchecks.certify(states[0], play, 20_000, 60)
    _, _, acc, edges, _ = songchecks.orbit(states[0], play, 20_000)
    assert not any(songchecks.compare(cert, acc, edges).values())
    extra = np.append(edges, 0x1234 << 16 | 0x1235)
    diff = songchecks.compare(cert, acc[1:], extra)
    assert diff["orbit_edges"] == [[0x1234, 0x1235]]
    assert len(diff["certificate_accesses"]) == 1


def test_smoke_check_a(smoke):
    """Check A on the smoke songs: every state certified, verified and equal to its orbit."""
    out, res = smoke
    s = res["check_a"]["summary"]
    assert s["states"] == s["certified"] == s["equal"] > 0
    assert not s["unequal"] and not s["unverified"]
    assert json.loads((out / "fixed" / "check_a.json").read_text())["summary"] == s


def test_smoke_check_b(smoke):
    """Check B on the smoke sweep: one playroutine, the stages growing, and the tools
    reaching the code every song of it runs."""
    _, res = smoke
    (v,) = res["check_b"]["variations"]
    n = v["instructions"]
    assert n["certified"] <= n["forced"] <= n["walked"]
    assert n["certified"] < n["union"]
    assert v["closed"] == v["songs"] == len(gensongs.sweep())
    assert all(isinstance(name, str) for _, name in v["tools_only"] + v["songs_only"])


def test_cli(tmp_path, capsys):
    """generate, check-a, certify and summary from the command line, with --json."""
    d = tmp_path / "songs"
    assert (
        gensongs.main(["generate", str(d), "--features", "gate", "--count", "2"]) == 0
    )
    manifest = gensongs.manifest(d)
    assert len(manifest["playroutines"]) == 1 and len(manifest["songs"]) == 2
    assert (d / f"{manifest['songs'][0]['name']}.json").exists()
    assert (
        gensongs.main(
            ["check-a", str(d), "--jobs", "1", "--json", str(tmp_path / "a.json")]
        )
        == 0
    )
    assert gensongs.main(["certify", str(d), "--jobs", "1"]) == 0
    assert gensongs.main(["summary", str(d)]) == 0
    out = capsys.readouterr().out
    assert "check A: 2 songs" in out and "tiny" in out
    timing = json.loads((d / "certify.json").read_text())["timing"]
    assert timing["tiny"]["states"] == 2
    assert json.loads((tmp_path / "a.json").read_text())["summary"]["songs"] == 2


def test_summaries_report_seeds():
    """Unequal and uncertified states are reported with their seeds."""
    songs = [{"name": "a", "seed": 1, "index": 2, "size": "tiny"}]
    recs = {
        "a": [
            {
                "state": 0,
                "form": "Certificate",
                "equal": False,
                "verified": True,
                "orbit": "closed",
                "calls": 5,
                "seconds": 1.0,
                "cpu": 1.0,
                "limit": [9, 9],
            },
            {
                "state": 1,
                "form": "timeout",
                "orbit": "open",
                "calls": None,
                "seconds": 9.0,
                "cpu": 9.0,
                "limit": [9, 9],
            },
        ]
    }
    s = songchecks.summary_a(recs, songs)
    assert s["unequal"] == [{"song": "a", "seed": [1, 2]}]
    assert s["not_certified"][0]["state"] == 1
    t = songchecks.timing(recs, songs)["tiny"]
    assert t["calls"] == [5, 5] and len(t["not_certified"]) == 1


def test_labelled():
    """Addresses are named by the nearest playroutine label below them."""
    got = songchecks.labelled(
        [0x8005, 0x7000], {"mt_a": 0x8000, "X": 0x8004}, (0x8000, 0x9000)
    )
    assert got == [[0x8005, "mt_a+5"], [0x7000, "?"]]


def test_layout_shape():
    """The layout names every table play reads, its orderlists and its patterns."""
    spec = gensongs.specs(1, 1, "small", "fixed", features=("multisong", "filter"))[0]
    song, _, lay = _song_and_layout(spec)
    names = {t["name"] for t in lay["tables"]}
    assert set(gtpack.DATA) <= names
    assert len(lay["orderlists"]) == 3 * len(song.subtunes)
    assert len(lay["patterns"]) == len(song.patterns)
    assert set(lay["defines"]) >= set(gensongs.SWITCHES)


def test_orderlist_shapes():
    """Tiny songs play one pattern per voice; larger ones transpose and repeat."""
    tiny = gensongs.specs(0, 1, "tiny", "fixed", features=("transpose", "repeat"))[
        0
    ].song()
    for o in tiny.subtunes[0].channels:
        kinds = [type(e) for e in o.entries]
        assert kinds[:2] == [Transpose, Repeat] and len(kinds) == 3


def test_check_a_and_song_code(smoke):
    """Check A of one smoke song in this process, its plain orbit's code, and a
    certificate cut short by its CPU limit."""
    out, _ = smoke
    name = gensongs.manifest(out / "fixed")["songs"][0]["name"]
    path = out / "fixed" / f"{name}.sid"
    recs = songchecks.check_a(path, "tiny")
    rec = recs[0]
    assert len(recs) == 1
    assert rec["equal"] and rec["orbit"] == "closed"
    assert rec["calls"] <= rec["orbit_calls"]
    pcs, closed = songchecks.song_code(path, "tiny")
    assert closed and len(pcs) > 100
    play, states = liftcheck.song_states(path.read_bytes())
    late, cert = songchecks.certify(states[0], play, 20_000, 0.001)
    assert late["form"] == "timeout" and cert is None
