/* C translation of the defMON playroutine of Goto80's Automatas.
 *
 * Every play call writes the SID from the voice state ("band"), then steps
 * the cutoff, then on every eighth call the arranger and pattern rows, then
 * the two sidTAB program layers of each voice, then pitch and pulse.
 *
 * DATA (automatas_data.h) is the original image from DATA_ORG to its end:
 * the note/slide word table, the sidTAB, pattern and arranger tables, the
 * pattern and sidTAB streams. Addresses below are original ones, read with
 * at(). VOICE_INIT holds the voice state the original keeps in its code.
 */
#include <c64.h>
#include <stdint.h>

#include "automatas_data.h"
#include "driver.h"

#define SIDREG(o) (((volatile uint8_t *)&SID)[o])
enum { FREQ_LO, FREQ_HI, PW_LO, PW_HI, CTRL, AD, SR, VOICE_REGS };
enum { CUTOFF_HI = 0x16, ROUTING, MODE_VOL, OSC3 = 0x1b };

/* word table: lo bytes at WORD_LO, hi at WORD_HI; note n is word NOTE + n */
enum { WORD_LO = 0x1554, WORD_HI = 0x15f0, NOTE = 0x24 };
/* sidTAB: row pointer lo/hi and wait per row; a zero hi byte jumps to lo */
enum { ROW_LO = 0x1800, ROW_HI = 0x1900, ROW_WAIT = 0x1e00 };
/* patterns: pointer lo/hi per pattern; arranger: pattern per voice and row,
 * a voice 1 entry with bit 7 set jumps to the row in the voice 2 column */
enum {
  PAT_LO = 0x1a00,
  PAT_HI = 0x1a80,
  ARRANGER = 0x1b00,
  ARR_STRIDE = 0x100
};

/* pattern row header: data bytes for layer A, layer B, note; end; delay */
enum { HD_A = 0x40, HD_B = 0x20, HD_NOTE = 0x10, HD_END = 0x80, HD_DELAY = 15 };
/* sidTAB row, first byte: data for */
enum {
  R1_EOR = 0x80,
  R1_WAVE = 0x40,
  R1_AD = 0x20,
  R1_SR = 0x10,
  R1_NOTE = 0x08,
  R1_PITCH = 0x04,
  R1_PULSE = 0x02
};
/* sidTAB row, second byte: data for */
enum {
  R2_SWEEP = 0x80,
  R2_ROUTE = 0x40,
  R2_MODE = 0x20,
  R2_CP = 0x10,
  R2_CUTOFF = 0x08
};
/* routing byte: 0 drops the voice; with RT_ADD it replaces the resonance and
 * adds the voice; otherwise it replaces the whole register */
enum { RT_ADD = 0x08 };
/* cutoff word: bit 15 makes it a step, bit 14 a downward one */
enum { CUT_STEP = 0x80, CUT_DOWN = 0x40 };
/* pitch mode: 0 note + detune, 1-$7f semitone offset, $80+ slide */
enum { PITCH_SLIDE = 0x80, SLIDE_DOWN = 0x40 };
/* pulse sweep: bit 7 up, else down; rate in the rest */
enum { SWEEP_UP = 0x80 };
enum { STOPPED = 0x80, FULL_CALLS = 8 };
enum { DETECT_LINE = 0xfc, CIA_PERIOD = 0x0998 };

static const uint8_t data[] = {DATA};

struct voice {
  uint16_t slide;
  uint8_t detune, route_or, route_and;
  uint8_t pw_lo, pw_hi, freq_lo, freq_hi, sr, ad, wave, eor;
  uint8_t pitch, sweep, note_base, note;
  uint16_t pat;    /* pattern stream */
  uint8_t delay;   /* rows: full calls to the next header; $ff: row due */
  uint8_t head;    /* pattern row header */
  uint8_t wait[2]; /* layers A, B: calls to the next row; 0 due, $80+ off */
  uint8_t prow[2]; /* layers A, B: next sidTAB row */
};
static struct voice voice[3] = {VOICE_INIT};

static uint8_t calls, seq, pos;
static uint8_t route, mode, cp, cut_lo, cut_hi, step_lo, step_hi;
static uint8_t cut_sub = CUT_SUB; /* kept across init, as in the original */
static uint8_t cut_min, cut_shift;

static uint8_t at(uint16_t a) { return data[a - DATA_ORG]; }
static uint16_t word(uint16_t i) {
  return at(WORD_LO + i) | at(WORD_HI + i) << 8;
}

/* 6502 ADC/SBC of the cutoff step: returns the sum, *c the carry */
static uint8_t cut_add(uint8_t a, uint8_t b, uint8_t *c) {
  uint16_t s = cut_sub ? a + (uint8_t)~b + *c : a + b + *c;
  *c = s >> 8;
  return s;
}

static void band(void) {
  uint8_t so = 0;
  /* 6502: no loop; copies at $1022/$1053/$1084 ($31 apart, JMP-chained),
   * each voice's state in its own copy's immediate operands. */
  for (struct voice *v = voice; v < voice + 3; ++v, so += VOICE_REGS) {
    SIDREG(so + PW_LO) = v->pw_lo;
    SIDREG(so + PW_HI) = v->pw_hi;
    SIDREG(so + FREQ_LO) = v->freq_lo;
    SIDREG(so + FREQ_HI) = v->freq_hi;
    SIDREG(so + SR) = v->sr;
    SIDREG(so + AD) = v->ad;
    SIDREG(so + CTRL) = v->wave ^ v->eor;
  }
  SIDREG(ROUTING) = route;
  SIDREG(MODE_VOL) = mode | 0x0f;
  uint8_t c = 0, a;
  cut_lo = cut_add(cut_lo, step_lo, &c);
  a = cut_add(cut_hi, step_hi, &c);
  cut_hi = a & 0x80 ? cut_min : a;
  uint16_t s = cut_hi + cp + c;
  a = s & 0x80 || (uint8_t)s < cut_min ? cut_min : s;
  SIDREG(CUTOFF_HI) = a << cut_shift;
}

static void arranger(void) {
  uint8_t y = pos, x = at(ARRANGER + y);
  if (x & 0x80) {
    y = at(ARRANGER + ARR_STRIDE + y);
    x = at(ARRANGER + y);
  }
  /* 6502: unrolled at $10F8-$1120, each voice's pattern pointer stored into
   * the operands of its row copy ($1165/$11ED/$1275). */
  for (uint8_t k = 0; k < 3; ++k) {
    if (k)
      x = at(ARRANGER + k * ARR_STRIDE + y);
    voice[k].pat = at(PAT_LO + x) | at(PAT_HI + x) << 8;
  }
  pos = y + 1;
}

static void row(struct voice *v) {
  uint8_t h = v->head, y = 1;
  if (h & HD_A) {
    v->prow[0] = at(v->pat + y++);
    v->wait[0] = 0;
  }
  if (h & HD_B) {
    v->prow[1] = at(v->pat + y++);
    v->wait[1] = 0;
  }
  if (h & HD_NOTE) {
    v->note_base = v->note = at(v->pat + y++);
    v->slide = 0;
    v->pitch = 0;
  }
  if (h & HD_END) {
    seq = h;
  } else {
    v->pat += y;
    v->delay = h & HD_DELAY;
  }
}

static void rows(void) {
  if (seq & STOPPED) {
    seq &= HD_DELAY;
    /* 6502: three stores, $10E1-$10E7, into the row copies' operands. */
    for (uint8_t k = 0; k < 3; ++k)
      voice[k].delay = seq;
    arranger();
  }
  /* 6502: no loop; copies at $112A/$11B2/$123A ($88 apart, fall-through). */
  for (struct voice *v = voice; v < voice + 3; ++v) {
    if (v->delay & 0x80)
      row(v);
    else if ((int8_t)--v->delay < 0)
      v->head = at(v->pat);
  }
}

/* Apply the sidTAB row at p to voice v. */
static void apply(struct voice *v, uint16_t p) {
  uint8_t f = at(p);
  if (f) {
    if (f & R1_WAVE)
      v->wave = at(++p);
    if (f & R1_EOR)
      v->eor = at(++p);
    if (f & R1_AD)
      v->ad = at(++p);
    if (f & R1_SR)
      v->sr = at(++p);
    if (f & R1_NOTE)
      v->note = at(++p) + v->note_base;
    if (f & R1_PITCH)
      v->pitch = at(++p);
    if (f & R1_PULSE) {
      v->pw_hi = at(++p);
      v->pw_lo = v->pw_hi & 0xf0;
    }
  }
  f = at(++p);
  if (f & R2_SWEEP)
    v->sweep = at(++p);
  if (f & R2_ROUTE) {
    uint8_t b = at(++p);
    if (!b)
      route &= v->route_and;
    else if (b & RT_ADD)
      route = (route & 0x0f) | b | v->route_or;
    else
      route = b;
  }
  if (f & R2_MODE)
    mode = at(++p);
  if (f & R2_CP)
    cp = at(++p);
  if (f & R2_CUTOFF) {
    uint8_t lo = at(++p), hi = at(++p);
    if (!(hi & CUT_STEP)) {
      cut_lo = lo;
      cut_hi = hi;
      step_lo = step_hi = 0;
    } else {
      step_lo = lo;
      step_hi = hi & 0x3f;
      cut_sub = !!(hi & CUT_DOWN);
    }
  }
}

static void programs(void) {
  /* 6502: no loop; six copies from $12BE, $31 apart (layer A voices 0-2,
   * then layer B), each calling apply() at $168C. */
  for (uint8_t l = 0; l < 2; ++l)
    for (struct voice *v = voice; v < voice + 3; ++v) {
      uint8_t w = v->wait[l];
      if (w & 0x80)
        continue;
      if (w) {
        v->wait[l] = w - 1;
        continue;
      }
      uint8_t y = v->prow[l], hi = at(ROW_HI + y);
      if (!hi) {
        y = at(ROW_LO + y);
        hi = at(ROW_HI + y);
      }
      v->wait[l] = at(ROW_WAIT + y);
      v->prow[l] = y + 1;
      apply(v, hi << 8 | at(ROW_LO + y));
    }
}

static void pitch_pulse(struct voice *v) {
  uint8_t m = v->pitch, c;
  uint16_t base = word(NOTE + v->note), f;
  if (!m) {
    uint16_t lo = (base & 0xff) + v->detune;
    c = lo >> 8;
    f = (base & 0xff00) | (uint8_t)lo; /* the carry is not added */
  } else {
    uint16_t d;
    if (m & PITCH_SLIDE) {
      d = word(m << 1 & 0x7e);
      d = v->slide += m & SLIDE_DOWN ? -d : d;
    } else {
      uint8_t y = m + v->note;
      d = word(y + 12) - word(y + 11);
    }
    f = base + d;
    c = f < base;
  }
  v->freq_lo = f;
  v->freq_hi = f >> 8;

  uint8_t s = v->sweep;
  if (!s)
    return;
  if (s & SWEEP_UP) {
    uint16_t sum = v->pw_lo + (s & 0x7f);
    v->pw_lo = sum;
    if (sum >> 8) {
      if (v->pw_hi == 0x0f) {
        v->pw_lo = 0xf8;
        v->sweep = s ^ SWEEP_UP;
      } else {
        ++v->pw_hi;
      }
    }
  } else {
    uint16_t dif = v->pw_lo - s - !c; /* borrows the pitch carry */
    v->pw_lo = dif;
    if (dif >> 8) {
      if (!v->pw_hi) {
        v->pw_lo = 1;
        v->sweep = s ^ SWEEP_UP;
      } else {
        --v->pw_hi;
      }
    }
  }
}

void tune_init(uint8_t song) {
  calls = 0;
  pos = song;
  for (int8_t r = 0x17; r >= 0; --r) /* 6502 loop $1505 */
    SIDREG(r) = 0;
  cut_lo = cut_hi = step_lo = step_hi = cp = route = mode = 0;
  /* SID model: osc 3 sawtooth read straight after a test bit release; the
   * driver polls the CIA, so its interrupt stays masked from here on. */
  CIA1.icr = 0x7f;
  while (VIC.rasterline != DETECT_LINE)
    ;
  SIDREG(2 * VOICE_REGS + CTRL) = 0xff;
  SIDREG(2 * VOICE_REGS + FREQ_LO) = 0xff;
  SIDREG(2 * VOICE_REGS + FREQ_HI) = 0xff;
  SIDREG(2 * VOICE_REGS + CTRL) = 0x20;
  uint8_t is8580 = SIDREG(OSC3) & 1;
  cut_min = is8580 ? 0 : 2;
  cut_shift = is8580;
  for (struct voice *v = voice; v < voice + 3; ++v) { /* 6502 loop $1525 */
    v->prow[0] = v->prow[1] = v->ad = v->sr = v->wave = v->eor = 0;
    v->pitch = v->sweep = v->note = 0;
    v->wait[0] = v->wait[1] = 0xff;
  }
  seq = STOPPED;
  CIA1.ta_lo = CIA_PERIOD & 0xff;
  CIA1.ta_hi = CIA_PERIOD >> 8;
}

void tune_play(void) {
  band();
  if (!(calls++ & (FULL_CALLS - 1)))
    rows();
  programs();
  /* 6502 loop $13E4-$14C7: X = $62, $31, 0 (SBX #$31) over code-resident state.
   */
  for (struct voice *v = voice + 2; v >= voice; --v)
    pitch_pulse(v);
}
