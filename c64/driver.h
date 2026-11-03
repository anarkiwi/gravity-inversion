#ifndef DRIVER_H
#define DRIVER_H
#include <stdint.h>

/* Implemented by each tune. */
void tune_init(uint8_t song);
void tune_play(void);

/* Subtune passed to tune_init; the harness sets it on entry to main. */
extern volatile uint8_t song_number;

/* Waits for the next play call: PAL raster line $ff, or with FRAME_CIA the
 * CIA 1 timer A underflow. The harness returns from it at once. */
void frame_wait(void);

#endif
