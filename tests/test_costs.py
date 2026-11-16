"""tools/costs.py names each code-size construct by line and totals its kinds K1-K6, a cost
report apart from the residue census (docs/costs.md)."""

import costs

SAMPLE = """#include <stdint.h>
uint8_t rd(uint16_t a);
uint8_t R0[4];
static void f1(uint8_t v1) {
  uint8_t v2 = R0[v1];
  if ((v2 != 0) && (R0[v1] = v2 - 1, v2 == 1))
    R0[2] = rd(0x1000 + v1);
  R0[3] = v1 == 1;
}
void tune_play(void) {
  f1(0);
  f1(1);
}
"""


def test_sites_name_each_construct(tmp_path, capsys):
    """`--sites` lists each construct by line: an instance-dependent `rd()` is K1, an index
    compared as a value K3, and a comparison that is a comma expression's value inside a
    branch condition is no construct."""
    src = tmp_path / "s.c"
    src.write_text(SAMPLE, encoding="ascii")
    costs.main([str(src), "--sites"])
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["7: K1 f1: rd(0x1000 + v1);", "8: K3 f1: v1 == 1;"]
    assert out[-1] == "total K1=1 K2=0 K3=1 K4=0 K5=0 K6=0"
