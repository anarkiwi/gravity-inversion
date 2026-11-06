/* Host harness for a cgen translation instrumented by tools/irtags.py and
 * compiled with -DCGEN_HOST. step() runs one entry call and logs its SID
 * writes, each with the tag of its value (cgen_sink). A tag names a sequence
 * read instance: cgen_tag() makes one, recording the read, the call and the
 * writes before it; cgen_tag_load() and cgen_tag_store() keep the newest tag
 * each byte carries, in an open-addressing table keyed by address. */
#include <setjmp.h>
#include <stdint.h>

void cgen_init(uint8_t a);
void cgen_play(uint8_t a);

#define MAXW 4096
#define SLOTS (1 << 16)
#define MAXT (1 << 22)

uint8_t io[0x1000];
uint32_t nw, ntag;
uint8_t wreg[MAXW], wval[MAXW];
uint32_t wtag[MAXW];
uint16_t tread[MAXT];
uint32_t tcall[MAXT], tpos[MAXT];
static uintptr_t keys[SLOTS];
static uint32_t vals[SLOTS];
static uint32_t pending, call;
static jmp_buf env;
static int trapped;

uint8_t io_read(uint16_t a) { return io[a & 0xfff]; }

void io_write(uint16_t a, uint8_t v) {
  uint8_t r = a & 0x1f;
  uint32_t t = pending;
  pending = 0;
  io[a & 0xfff] = v;
  if (a < 0xd400 || a >= 0xd800 || r >= 0x19 || nw >= MAXW)
    return;
  wreg[nw] = r;
  wval[nw] = v;
  wtag[nw++] = t;
}

void trap(uint16_t k) {
  trapped = k;
  longjmp(env, 1);
}

void cgen_sink(uint32_t tag) { pending = tag; }

uint32_t cgen_tag(uint16_t read) {
  if (ntag + 1 >= MAXT)
    return 0;
  ntag++;
  tread[ntag] = read;
  tcall[ntag] = call;
  tpos[ntag] = nw;
  return ntag;
}

static uint32_t slot(const void *p) {
  uintptr_t k = (uintptr_t)p;
  uint32_t h = (uint32_t)((k * 0x9E3779B97F4A7C15u) >> 48);
  while (keys[h] && keys[h] != k)
    h = (h + 1) & (SLOTS - 1);
  return h;
}

uint32_t cgen_tag_load(const void *p) {
  uint32_t h = slot(p);
  return keys[h] ? vals[h] : 0;
}

void cgen_tag_store(const void *p, uint32_t tag) {
  uint32_t h = slot(p);
  keys[h] = (uintptr_t)p;
  vals[h] = tag;
}

int step(int init, uint8_t a, uint32_t index) {
  nw = 0;
  pending = 0;
  call = index;
  trapped = -1;
  if (!setjmp(env)) {
    if (init)
      cgen_init(a);
    else
      cgen_play(a);
  }
  return trapped;
}
