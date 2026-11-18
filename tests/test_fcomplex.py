"""Tests for tools/fcomplex.py: clang-tidy's cognitive complexity of C, per increment and per
function."""

import json
import shutil

import pytest

import fcomplex

pytestmark = pytest.mark.skipif(
    not shutil.which("clang-tidy"), reason="needs clang-tidy"
)

C = """#include <stdint.h>
uint8_t a, b;
void flat(void) { a = b; }
void nested(void) {
  for (uint8_t i = 0; i < 4; i++) {
    if (a && b) {
      a = 1;
    } else {
      goto out;
    }
  }
out:
  b = 0;
}
"""


@pytest.fixture(name="src")
def fixture_src(tmp_path):
    """A C file with a flat and a nested function."""
    path = tmp_path / "gen.c"
    path.write_text(C, encoding="ascii")
    return path


def test_increments_follow_sonar_rules(src):
    """A loop counts 1, an `if` in it 1 plus 1 of nesting, its `&&` 1, its `else` 1 and the
    goto 1; a function with no increment is not listed."""
    inc = fcomplex.increments(src, [])
    assert set(inc) == {"nested"}
    kinds = [(k, p, n) for _, k, p, n in inc["nested"]]
    assert kinds == [
        ("for", 1, 0),
        ("if", 2, 1),
        ("&&", 1, 0),
        ("else", 1, 0),
        ("goto", 1, 0),
    ]


def test_score_totals_and_main(src, capsys):
    """The file's total is its functions' sum; the CLI prints it with the most complex first."""
    got = fcomplex.score(src)
    assert got["total"]["cognitive"] == 6 and got["total"]["scored functions"] == 1
    assert got["functions"]["nested"]["gotos"] == 1
    assert got["functions"]["nested"]["by construct"]["if"] == 2
    assert fcomplex.main([str(src), "--top", "1"]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert row["top"] == {"nested": 6} and row[str(src)]["nesting"] == 1


def test_units_score_as_one(src, capsys):
    """A translation's units score as their functions together: a second unit's function adds
    its increments to the first's."""
    other = src.with_name("gen_play.c")
    other.write_text("int g(int a) {\n  if (a)\n    return 1;\n  return 0;\n}\n")
    got = fcomplex.score([src, other])
    assert set(got["functions"]) == {"nested", "g"}
    assert got["total"]["cognitive"] == 7
    assert fcomplex.main([str(src), str(other), "--units"]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert row[f"{src} {other}"]["scored functions"] == 2
