/* Frama-C EVA harness of a generated translation (tools/eva.py, docs/bounds.md):
 * init of any song, then any number of play calls, every I/O read any byte. */
#define CGEN_HOST
#include "@TUNE@.c"
#include "__fc_builtin.h"
#include <stdlib.h>
volatile uint8_t song_number;
volatile uint16_t cgen_trap;
uint8_t io_read(uint16_t a) { return Frama_C_unsigned_char_interval(0, 255); }
void io_write(uint16_t a, uint8_t v) {}
void trap(uint16_t k) { abort(); }
int main(void) {
  tune_init(Frama_C_unsigned_char_interval(0, 255));
  while (Frama_C_nondet(0, 1))
    tune_play();
  return 0;
}
