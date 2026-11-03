#include <c64.h>

#include "commando.h"

int main(void) {
  commando_init(0);
  for (;;) {
    while (VIC.rasterline != 0xff)
      ;
    commando_play();
    while (VIC.rasterline == 0xff)
      ;
  }
}
