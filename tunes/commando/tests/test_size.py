"""The C playroutine stays under twice the size of the original source."""

from support import ROOT, player_size


def test_c_under_twice_asm():
    """Compare src/commando.c to the asm playroutine, comments included."""
    assert len((ROOT / "src" / "commando.c").read_bytes()) < 2 * player_size()
