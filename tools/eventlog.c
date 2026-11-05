/* Host harness for a cgen translation compiled with -DCGEN_HOST,
 * -fsanitize-coverage=trace-pc and -finstrument-functions. step() runs one
 * entry call and logs its SID writes and the basic blocks it executes, each
 * with the SID voice it serves: the voice of the innermost dynamic call below
 * the entry whose SID writes all address that voice (bvia 0), else the voice
 * of the next voice register write in the call (bvia 1), else none (bvia 2). */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <setjmp.h>
#include <stdint.h>

void cgen_init(uint8_t a);
void cgen_play(uint8_t a);

#define MAXW 4096
#define MAXB (1 << 22)
#define MAXD 1024
#define UNSET (-2)
#define VOICE_REGS 21
#define VOICE_STRIDE 7

uint8_t io[0x1000];
uint32_t nw, nb;
uint8_t wreg[MAXW], wval[MAXW];
uint32_t wpos[MAXW];
uintptr_t bpc[MAXB];
int8_t bvoice[MAXB];
uint8_t bvia[MAXB];
static uint32_t start[MAXD];
static uint8_t mask[MAXD];
static int depth, rec;
static uintptr_t base;
static jmp_buf env;
static int trapped;

uint8_t io_read(uint16_t a) { return io[a & 0xfff]; }

void io_write(uint16_t a, uint8_t v) {
  uint8_t r = a & 0x1f;
  io[a & 0xfff] = v;
  if (a < 0xd400 || a >= 0xd800 || r >= 0x19 || nw >= MAXW)
    return;
  wreg[nw] = r;
  wval[nw] = v;
  wpos[nw++] = nb;
  if (r < VOICE_REGS && depth < MAXD)
    mask[depth] |= 1 << (r / VOICE_STRIDE);
}

void trap(uint16_t k) {
  trapped = k;
  longjmp(env, 1);
}

static int single(uint8_t m) {
  return m == 1 ? 0 : m == 2 ? 1 : m == 4 ? 2 : -1;
}

void __cyg_profile_func_enter(void *fn, void *site) {
  (void)fn;
  (void)site;
  if (rec && ++depth < MAXD) {
    start[depth] = nb;
    mask[depth] = 0;
  }
}

void __cyg_profile_func_exit(void *fn, void *site) {
  (void)fn;
  (void)site;
  if (!rec)
    return;
  if (depth < MAXD) {
    int v = depth > 1 ? single(mask[depth]) : -1;
    for (uint32_t i = start[depth]; v >= 0 && i < nb; i++)
      if (bvoice[i] == UNSET) {
        bvoice[i] = v;
        bvia[i] = 0;
      }
    if (depth > 0)
      mask[depth - 1] |= mask[depth];
  }
  depth--;
}

void __sanitizer_cov_trace_pc(void) {
  if (rec && nb < MAXB) {
    bpc[nb] = (uintptr_t)__builtin_return_address(0) - base;
    bvoice[nb++] = UNSET;
  }
}

static void next_write(void) {
  uint32_t w = 0;
  for (uint32_t i = 0; i < nb; i++) {
    while (w < nw && (wpos[w] <= i || wreg[w] >= VOICE_REGS))
      w++;
    if (bvoice[i] == UNSET) {
      bvoice[i] = w < nw ? wreg[w] / VOICE_STRIDE : -1;
      bvia[i] = w < nw ? 1 : 2;
    }
  }
}

int step(int init, uint8_t a) {
  Dl_info info;
  if (!base && dladdr((void *)cgen_play, &info))
    base = (uintptr_t)info.dli_fbase;
  nw = nb = 0;
  depth = 0;
  mask[0] = 0;
  trapped = -1;
  rec = 1;
  if (!setjmp(env)) {
    if (init)
      cgen_init(a);
    else
      cgen_play(a);
  }
  rec = 0;
  next_write();
  return trapped;
}
