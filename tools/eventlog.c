/* Host harness for a cgen translation instrumented by tools/irmodule.py and
 * compiled with -DCGEN_HOST. step() runs one entry call and logs, in order,
 * its song data reads (cgen_ld at a site, of a byte song[] marks), its stores
 * to the regions cgen_regions() registers (cgen_st, with the byte's old
 * value) and its SID writes. io_read() answers the I/O reads of the call from
 * the reference's, in order per address, else the last value written. */
#include <setjmp.h>
#include <stdint.h>

void cgen_init(uint8_t a);
void cgen_play(uint8_t a);
void cgen_regions(void);

#define MAXE (1 << 18)
#define MAXR 256
#define MAXIO 65536

enum { READ, CELL, SIDW };

uint8_t song[0x10000], cgen_copy;
uint32_t ne, nio;
uint8_t ek[MAXE], ev[MAXE], eo[MAXE], ec[MAXE];
uint16_t es[MAXE], ea[MAXE];
uint16_t ioa[MAXIO];
uint8_t iov[MAXIO];
static uint8_t io[0x1000], *rbase[MAXR];
static uint32_t rlen[MAXR], rorg[MAXR], nr, iop;
static jmp_buf env;
static int trapped;

void cgen_region(uint8_t *p, uint32_t n, uint32_t org) {
  rbase[nr] = p;
  rlen[nr] = n;
  rorg[nr++] = org;
}

static int32_t addr_of(const uint8_t *p) {
  for (uint32_t i = 0; i < nr; i++)
    if (p >= rbase[i] && p < rbase[i] + rlen[i])
      return (int32_t)((rorg[i] + (uint32_t)(p - rbase[i])) & 0xffff);
  return -1;
}

static void put(uint8_t k, uint16_t s, uint16_t a, uint8_t v, uint8_t o) {
  if (ne >= MAXE)
    return;
  ek[ne] = k;
  es[ne] = s;
  ea[ne] = a;
  ev[ne] = v;
  eo[ne] = o;
  ec[ne++] = cgen_copy;
}

void cgen_ld(uint32_t site, const uint8_t *p, uint8_t v) {
  int32_t a = addr_of(p);
  if (a >= 0 && song[a])
    put(READ, (uint16_t)site, (uint16_t)a, v, v);
}

void cgen_st(uint8_t *p, uint8_t v) {
  int32_t a = addr_of(p);
  if (a >= 0)
    put(CELL, 0, (uint16_t)a, v, *p);
}

uint8_t io_read(uint16_t a) {
  for (uint32_t i = iop; i < nio; i++)
    if (ioa[i] == a) {
      iop = i + 1;
      return io[a & 0xfff] = iov[i];
    }
  return io[a & 0xfff];
}

void io_write(uint16_t a, uint8_t v) {
  io[a & 0xfff] = v;
  if (a >= 0xd400 && a < 0xd800 && (a & 0x1f) < 0x19)
    put(SIDW, a & 0x1f, a & 0x1f, v, v);
}

void trap(uint16_t k) {
  trapped = k;
  longjmp(env, 1);
}

int step(int init, uint8_t a) {
  ne = iop = 0;
  cgen_copy = 0;
  trapped = -1;
  if (!nr)
    cgen_regions();
  if (!setjmp(env)) {
    if (init)
      cgen_init(a);
    else
      cgen_play(a);
  }
  return trapped;
}
