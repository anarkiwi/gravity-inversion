#include <c64.h>

#include "driver.h"

volatile uint8_t song_number;

/* Weak: a generated translation links cgen's own cgen_trap.c. */
__attribute__((weak)) volatile uint16_t cgen_trap;

__attribute__((weak, noinline, noreturn)) void trap(uint16_t k) {
  cgen_trap = k;
  for (;;)
    __asm__ volatile(".byte 0x02");
}

__attribute__((noinline)) void frame_wait(void) {
#ifdef FRAME_CIA
  while (!(CIA1.icr & 1))
    ;
#else
  while (VIC.rasterline != 0xff)
    ;
  while (VIC.rasterline == 0xff)
    ;
#endif
}

int main(void) {
  tune_init(song_number);
  for (;;) {
    frame_wait();
    tune_play();
  }
}
