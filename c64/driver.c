#include <c64.h>

#include "driver.h"

volatile uint8_t song_number;

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
