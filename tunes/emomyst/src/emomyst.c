/* C translation of the SID-Wizard 1.x (SWP export) playroutine of Hermit's
 * Emomyst.
 *
 * emomyst_data.h is generated from the original PSID: TABLES is the player's
 * table block as loaded (ADSR conversion, exponent and frequency tables, to 255
 * past the frequency-low table, where 8-bit indexed reads run into the code),
 * MUSIC the SWP music data from its base address. Pointers in the music are
 * relative to that base, as are the table addresses init reads from the SWP
 * header. Tempo effects write into the music's tempo table, so it is RAM.
 *
 * Voice state keeps the original's interleaving: bunches of 7 bytes, one
 * per voice at offsets 0, 7 and 14 (x), so cross-voice reads such as
 * PITCH + filter-controller voice behave as in the original.
 */
#include <c64.h>
#include <stdint.h>

#include "driver.h"
#include "emomyst_data.h"

static uint8_t tab[] = {TABLES};
static uint8_t music[] = {MUSIC};

/* player tables, by original address */
#define T(addr) tab[(uint16_t)((addr) - TABLES_ORG)]
#define ADSR_OFFS 0x1840
#define ADSR_EXPTB 0x1850
#define EXPTAB 0x185e
#define ADSR_EXPT2 0x1880
#define FREQ_HI 0x1869
#define FREQ_LO 0x18d1
#define EXP_LIMIT 0x6b /* exponent table length before the frequency table */
#define NOTE_FX_OFFS 0x18b9
/* note fx branch operand, within 8-bit reach of the frequency tables */
#define NOTE_FX_OPERAND 0x1952

/* SWP header: offsets of the base-relative table pointers */
enum {
  H_SUBTUNES = 4,
  H_PTN_LO = 6,
  H_PTN_HI = 8,
  H_INS_LO = 10,
  H_INS_HI = 12,
  H_CHORDS = 14,
  H_CHORD_PTR = 16,
  H_TEMPO = 18,
  H_TEMPO_PRG = 20
};

/* per-voice state, + x */
enum {
  FREQLO = 0x00,
  FREQHI,
  PWLO,
  PWHI,
  WAVE, /* waveform ghost register */
  GATE, /* gate mask from pattern */
  PW_SWEEP,
  PACK_CNT = 0x15, /* packed rest repeat counter */
  SPD_CNT,
  SEQ_POS,
  PTN_POS,
  WF_POS,
  PW_POS,
  ARP_CNT,
  CUR_PTN = 0x2a,
  CUR_NOTE,
  PITCH,
  CUR_IFX, /* instrument or instrument-fx column */
  CUR_INS,
  CUR_FX,
  CUR_VAL,
  SLIDE = 0x3f, /* $10-$30 vibrato type, $81/$82 slide, $83/$ff portamento */
  FMOD_LO,
  FMOD_HI,
  VIB_DELAY,
  VIB_FREQ,
  VIB_CNT,
  TEMPO_BASE = 0x54,
  TEMPO_POS,
  ARP_SPEED,
  PW_KEYTRACK,
  CHORD,
  CHORD_POS,
  ARP_PITCH,
  FILTER_ON = 0x69, /* constants: filter switch bit, its mask, tempo slot */
  FILTER_OFF,
  TRACK_TEMPO,
  CLEARED = FILTER_ON
};
static uint8_t ch[0x7a] = {[FILTER_ON] = 1,      0xfe, 2,
                           [FILTER_ON + 7] = 2,  0xfd, 4,
                           [FILTER_ON + 14] = 4, 0xfb, 6};
#define V(f) ch[x + (f)]

/* instrument bytes */
enum {
  I_CTRL,
  I_HR_AD,
  I_HR_SR,
  I_AD,
  I_SR,
  I_VIB,
  I_VIB_DELAY,
  I_ARP_SPEED,
  I_CHORD,
  I_OCTAVE,
  I_PW_POS,
  I_FLT_POS,
  I_WF_GATEOFF,
  I_PW_GATEOFF,
  I_FLT_GATEOFF,
  I_WAVE1
};
/* instrument control byte */
enum {
  C_WAVE1 = 0x08,
  C_HR_GATEOFF = 0x04,
  C_VIB_TYPE = 0x30,
  C_KEEP_PW = 0x40,
  C_KEEP_FLT = 0x80
};

#define SIDREG(r) (((volatile uint8_t *)&SID)[r])
#define SIDV(r) SIDREG(x + (r))
enum { R_FREQ_LO, R_FREQ_HI, R_PW_LO, R_PW_HI, R_CTRL, R_AD, R_SR };
#define NEG(v) ((uint8_t)(v) & 0x80)
#define SLOWDOWN 0 /* slowdown amount: only the host API changes it */
#define DITHER_FULL 0xff

static uint16_t seq_ptr[3], subtune_tempo, ptn_lo, ptn_hi, ins_lo, ins_hi,
    chords, chord_ptr, tempo, tempo_prg;
static uint16_t zp; /* pattern or instrument being read */
static uint8_t x, c, slow_cnt;
/* globals the original keeps in immediate operands */
static uint8_t filter_sw, reso, main_vol, band, keytrack, cutoff_hi, cutoff_lo,
    flt_ctrl, flt_pos, flt_sweep, ins_ctrl, table_reset, nib_val;

static uint8_t adc(uint8_t a, uint8_t b) {
  uint16_t r = a + b + c;
  c = r >> 8;
  return r;
}
static uint8_t sbc(uint8_t a, uint8_t b) { return adc(a, ~b); }
static uint8_t rd(uint8_t y) { return music[(uint16_t)(zp + y)]; }
static uint16_t word(uint16_t a) { return music[a] | music[a + 1] << 8; }

static void set_ptn(void) {
  uint8_t y = V(CUR_PTN);
  zp = music[ptn_lo + y] | music[ptn_hi + y] << 8;
}
static void set_ins(uint8_t y) {
  zp = music[ins_lo + y] | music[ins_hi + y] << 8;
}
static uint8_t seq_read(uint8_t y) {
  return music[seq_ptr[x == 7 ? 1 : x > 7 ? 2 : 0] + y];
}

/* nibble merges with the stored fx value */
static uint8_t nib_lo(uint8_t a) { return (a & 0xf0) | nib_val; }
static uint8_t nib_hi(uint8_t a) { return nib_val << 4 | (a & 0x0f); }

/* frequency modifier from a slide speed or vibrato amplitude */
static void set_fmod(uint8_t a) {
  if (a) {
    c = a & 1;
    a = sbc(adc(a >> 1, V(PITCH)), SLOWDOWN);
    if (NEG(a))
      a = 0;
    if (a >= EXP_LIMIT) {
      V(FMOD_LO) = T(FREQ_LO - EXP_LIMIT + a);
      V(FMOD_HI) = T(FREQ_HI - EXP_LIMIT + a);
      return;
    }
    a = T(EXPTAB + a);
  }
  V(FMOD_LO) = a;
  V(FMOD_HI) = 0;
}

static void set_slide(uint8_t type, uint8_t speed) {
  V(SLIDE) = type;
  set_fmod(speed);
}

static void set_vibrato(uint8_t a) {
  uint8_t f = (a & 0x0f) << 1, cnt = f >> 1, type = V(SLIDE);
  V(VIB_FREQ) = f;
  if (NEG(type - 0x20))
    cnt >>= 1;
  if (type == 0x30)
    cnt = 0;
  V(VIB_CNT) = cnt;
  set_fmod((a & 0xf0) >> 1);
}

static void vibrato_fx(uint8_t a) {
  V(SLIDE) = rd(I_CTRL) & C_VIB_TYPE;
  set_vibrato(a);
}

static void set_chord(uint8_t a) {
  V(CHORD) = a;
  V(CHORD_POS) = music[chord_ptr + a];
}

static void set_arp_speed(uint8_t a) {
  V(ARP_SPEED) = a;
  V(ARP_CNT) = 0xff;
}

static void tempo_pos_all(uint8_t a) {
  ch[TEMPO_POS] = ch[TEMPO_POS + 7] = ch[TEMPO_POS + 14] = a;
  ch[TEMPO_BASE] = ch[TEMPO_BASE + 7] = ch[TEMPO_BASE + 14] = a;
}

static uint8_t track_tempo(uint8_t a) {
  uint8_t y = V(TRACK_TEMPO);
  music[tempo + y] = a;
  V(TEMPO_BASE) = V(TEMPO_POS) = y;
  return y;
}

static uint8_t mul3(uint8_t a) {
  c = a >> 7;
  return adc(a << 1, a);
}

/* small fx $2x-$fx (also instrument fx $4x-$7x): type, value nibble */
static void small_fx(uint8_t a) {
  nib_val = a & 0x0f;
  switch (a >> 4) {
  case 2: /* attack */
    SIDV(R_AD) = nib_hi(rd(I_AD));
    break;
  case 3: /* decay */
    SIDV(R_AD) = nib_lo(rd(I_AD));
    break;
  case 4: /* waveform */
    V(WAVE) = nib_hi(V(WAVE));
    break;
  case 5: /* sustain */
    SIDV(R_SR) = nib_hi(rd(I_SR));
    break;
  case 6: /* release */
    SIDV(R_SR) = nib_lo(rd(I_SR));
    break;
  case 7:
    set_chord(nib_val);
    break;
  case 8: /* vibrato amplitude */
    vibrato_fx(nib_hi(rd(I_VIB)));
    break;
  case 10:
    main_vol = nib_val;
    break;
  case 12:
    set_arp_speed(nib_val);
    break;
  }
}

/* big fx $01-$1f with value a */
static void big_fx(uint8_t fx, uint8_t a) {
  switch (fx) {
  case 1: /* slide up, down, portamento */
  case 2:
  case 3:
    set_slide(0x80 + fx, a);
    break;
  case 4:
    V(WAVE) = a;
    break;
  case 5:
    SIDV(R_AD) = a;
    break;
  case 6:
    SIDV(R_SR) = a;
    break;
  case 7:
    set_chord(a);
    break;
  case 8:
    vibrato_fx(a);
    break;
  case 9: /* table positions, in rows of 3 */
    V(WF_POS) = adc(mul3(a), 0x10);
    break;
  case 10:
    V(PW_POS) = adc(mul3(a), rd(I_PW_POS));
    break;
  case 11:
    flt_pos = adc(mul3(a), rd(I_FLT_POS));
    break;
  case 12:
    set_arp_speed(a);
    break;
  case 14:
    V(PWHI) = a & 0x0f;
    break;
  case 15:
    cutoff_hi = a;
    break;
  case 0x11: /* funk tempo */
    music[tempo + 1] = (a & 0x0f) | 0x80;
    a >>= 4;
    music[tempo] = a;
    tempo_pos_all(0);
    break;
  case 0x10: /* main tempo */
    music[tempo] = a | 0x80;
    tempo_pos_all(0);
    break;
  case 0x12: /* tempo program */
    if (a)
      tempo_pos_all(music[tempo_prg + a]);
    break;
  case 0x13: /* track tempo */
    track_tempo(a | 0x80);
    break;
  case 0x14: /* track funk tempo */
    music[tempo + 1 + track_tempo(a >> 4)] = (a & 0x0f) | 0x80;
    break;
  case 0x15: /* track tempo program */
    if (a)
      V(TEMPO_BASE) = V(TEMPO_POS) = music[tempo_prg + a];
    break;
  case 0x16:
    V(SLIDE) = a & C_VIB_TYPE;
    break;
  }
}

/* instrument-column fx, then the fx column */
static void ins_ptn_fx(void) {
  uint8_t a = V(CUR_IFX);
  if (!NEG(a - 0x40))
    small_fx(a);
  a = V(CUR_FX);
  if (a >= 0x20)
    small_fx(a);
  else if (a)
    big_fx(a, V(CUR_VAL));
}

static void gate_off(void) {
  uint8_t a = rd(I_WF_GATEOFF);
  if (a) {
    V(WF_POS) = a;
  } else {
    V(GATE) = 0xfe;
    V(WAVE) &= 0xfe;
  }
  if ((a = rd(I_PW_GATEOFF)))
    V(PW_POS) = a;
  if (x == flt_ctrl && (a = rd(I_FLT_GATEOFF)))
    flt_pos = a;
}

/* note column $60-$7f */
static void note_fx(uint8_t a) {
  uint8_t w = V(WAVE);
  if (NEG(a - 0x78)) { /* vibrato amplitude */
    nib_val = a;
    vibrato_fx(nib_hi(rd(I_VIB)));
  } else {
    T(NOTE_FX_OPERAND) = T(NOTE_FX_OFFS + a);
    switch (a) {
    case 0x79:
      w |= 2; /* sync */
      break;
    case 0x7a:
      w &= ~2;
      break;
    case 0x7b:
      w |= 4; /* ring */
      break;
    case 0x7c:
      w &= ~4;
      break;
    case 0x7d:
      V(GATE) = 0xff;
      w |= 1;
      break;
    case 0x7e:
      gate_off();
      goto done;
    default: /* $78 and $7f: portamento */
      set_slide(0xff, 0x6e);
      goto done;
    }
    V(WAVE) = w;
  }
done:
  ins_ptn_fx();
}

static void write_wave(void) { SIDV(R_CTRL) = V(WAVE); }

static void write_pitch(void) {
  if (NEG(V(ARP_PITCH) - SLOWDOWN)) {
    SIDV(R_FREQ_LO) = 1;
    SIDV(R_FREQ_HI) = 1;
  } else {
    SIDV(R_FREQ_LO) = V(FREQLO);
    SIDV(R_FREQ_HI) = V(FREQHI);
  }
}

/* waveform/arpeggio table and chords */
static void wf_arp(void) {
  uint8_t a, y, arp;
  if (!NEG(--V(ARP_CNT)))
    goto end;
  V(ARP_CNT) = V(ARP_SPEED) & 0x3f;
row:
  y = V(WF_POS);
  a = rd(y);
  if (a == 0xfe) {
    a = rd(++y);
    if (NEG(a))
      goto end;
    V(WF_POS) = y = a;
    a = rd(y);
  } else if (a == 0xff) {
    goto end;
  } else if (a < 0x10) {
    V(ARP_CNT) = a;
    goto pitch;
  }
  V(WAVE) = a & V(GATE);
pitch:
  a = rd(++y);
  y++;
  if (a == 0x7f) { /* chord; the detune column is not used by this build */
    a = music[chords + V(CHORD_POS)];
    if (a == 0x7e) {
      V(CHORD_POS) = music[chord_ptr + V(CHORD)];
      V(WF_POS) += 3;
      goto row;
    }
    if (a == 0x7f) {
      y = V(CHORD_POS) = music[chord_ptr + V(CHORD)];
      a = music[chords + y];
    }
    V(CHORD_POS)++;
    goto relative;
  }
  arp = a;
  V(WF_POS) = y + 1;
  a = arp;
  if (!NEG(a))
    goto relative;
  if (a == 0x80)
    goto end;
  if (a < 0xe0)
    goto absolute;
relative:
  a += V(PITCH);
absolute:
  a &= 0x7f;
  V(ARP_PITCH) = a;
  V(FREQLO) = T(FREQ_LO + a);
  V(FREQHI) = T(FREQ_HI + a);
end:
  write_pitch();
  write_wave();
}

static void vib_slide(void) {
  uint8_t y = V(SLIDE), a, lo, hi;
  if (!y) { /* increasing vibrato */
    c = 0;
    V(FMOD_LO) = adc(V(FMOD_LO), V(VIB_DELAY));
    if (c)
      V(FMOD_HI)++;
    goto vibrato;
  }
  if (!NEG(y)) {
    if (!NEG(V(VIB_DELAY))) {
      V(VIB_DELAY)--;
      return;
    }
    goto vibrato;
  }
  c = y >= 0x82;
  if (y == 0x82)
    goto down;
  if (NEG(y - 0x82))
    goto up;
  y = V(PITCH); /* portamento */
  lo = sbc(T(FREQ_LO + y), V(FREQLO));
  hi = sbc(T(FREQ_HI + y), V(FREQHI));
  if (c) {
    sbc(lo, V(FMOD_LO));
    sbc(hi, V(FMOD_HI));
    if (c)
      goto up;
  } else {
    adc(lo, V(FMOD_LO));
    adc(hi, V(FMOD_HI));
    if (!c)
      goto down;
  }
  y = V(PITCH); /* target reached */
  V(FREQLO) = T(FREQ_LO + y);
  a = T(FREQ_HI + y);
  goto store;
vibrato:
  a = V(VIB_CNT);
  if (!a)
    a = V(VIB_FREQ);
  V(VIB_CNT) = --a;
  c = (uint8_t)(a << 1) >= V(VIB_FREQ);
  if (!c)
    goto up;
down:
  V(FREQLO) = sbc(V(FREQLO), V(FMOD_LO));
  a = sbc(V(FREQHI), V(FMOD_HI));
  goto store;
up:
  V(FREQLO) = adc(V(FREQLO), V(FMOD_LO));
  a = adc(V(FREQHI), V(FMOD_HI));
store:
  V(FREQHI) = a;
}

/* filter table, run by the filter-controller voice */
static void filter_prg(void) {
  uint8_t y = flt_pos, a = rd(y), carry;
  if (x != flt_ctrl)
    return;
  if (!NEG(a)) { /* sweep */
    y++;
    if (a == flt_sweep)
      goto advance;
    flt_sweep++;
    c = 0;
    a = rd(y);
    if (NEG(a)) {
      a = adc(a | 0xf8, cutoff_lo);
      carry = c;
      cutoff_lo = a & 7;
      a = ~((uint8_t)~rd(y) >> 3);
    } else {
      a = adc(a & 7, cutoff_lo);
      carry = a >= 8;
      cutoff_lo = a & 7;
      a = rd(y) >> 3;
    }
    c = carry;
    cutoff_hi = adc(a, cutoff_hi);
    return;
  }
  if (a == 0xff)
    return;
  if (a == 0xfe) {
    a = rd(++y);
    if (a == flt_pos)
      return;
    y = a;
    if (!NEG(a = rd(y)))
      goto set_pos;
  }
  band = a & 0x70;
  reso = a << 4;
  cutoff_hi = rd(++y);
  cutoff_lo = 0;
advance:
  a = rd(++y);
  if (NEG(a) && a < 0x90) {
    filter_sw = a & 0x0f;
    a = 0;
  }
  keytrack = a;
  y++;
set_pos:
  flt_pos = y;
  flt_sweep = 0;
}

/* pulse width table */
static void pw_prg(void) {
  uint8_t y = V(PW_POS), a = rd(y);
  if (!NEG(a)) { /* sweep */
    y++;
    if (a == V(PW_SWEEP))
      goto advance;
    V(PW_SWEEP)++;
    a = rd(y);
    if (NEG(a))
      V(PWHI)--;
    c = 0;
    V(PWLO) = adc(a, V(PWLO));
    if (c)
      V(PWHI)++;
    goto write;
  }
  if (a == 0xff)
    goto write;
  if (a == 0xfe) {
    a = rd(++y);
    if (a == V(PW_POS))
      goto write;
    y = a;
    if (!NEG(a = rd(y)))
      goto set_pos;
  }
  V(PWHI) = a & 0x7f;
  V(PWLO) = rd(++y);
advance:
  V(PW_KEYTRACK) = rd(++y);
  y++;
set_pos:
  V(PW_POS) = y;
  V(PW_SWEEP) = 0;
write:
  c = 0;
  a = V(PW_KEYTRACK);
  if (a) {
    y = adc(a, V(PITCH));
    a = sbc(T(EXPTAB + y), T(EXPTAB - 1 + y));
  }
  SIDV(R_PW_HI) = adc(a, V(PWHI));
  SIDV(R_PW_LO) = V(PWLO);
}

static void cnt_play2(void) {
  vib_slide();
  filter_prg();
  pw_prg();
  wf_arp();
}

static void cnt_play(void) {
  if (!V(CUR_INS))
    return;
  set_ins(V(CUR_INS));
  cnt_play2();
}

static void legato(void) {
  ins_ptn_fx();
  write_wave();
}

/* AD/SR through the slowdown conversion tables */
static uint8_t adsr_nib(uint8_t a, uint8_t nib) {
  return adc(a, T(ADSR_EXPTB + adc(T(ADSR_OFFS + nib), SLOWDOWN)));
}

static void start_sound(void) {
  uint8_t a, y;
  ins_ctrl = a = rd(I_CTRL) & table_reset;
  V(SLIDE) = a & C_VIB_TYPE;
  if (ins_ctrl & C_WAVE1) {
    SIDV(R_FREQ_HI) = T(FREQ_HI + x);
    V(WAVE) = rd(I_WAVE1);
  }
  V(WF_POS) = 0x10;
  V(GATE) = 0xff;
  V(ARP_SPEED) = rd(I_ARP_SPEED);
  V(VIB_DELAY) = rd(I_VIB_DELAY);
  set_vibrato(rd(I_VIB));
  set_chord(rd(I_CHORD));
  if (!(ins_ctrl & C_KEEP_PW))
    V(PW_POS) = rd(I_PW_POS);
  if (!(ins_ctrl & C_KEEP_FLT)) {
    y = rd(I_FLT_POS);
    a = rd(y);
    if (a == 0xff) {
      filter_sw &= V(FILTER_OFF);
    } else {
      if (a) {
        flt_ctrl = x;
        flt_pos = y;
      }
      filter_sw |= V(FILTER_ON);
    }
  }
  a = rd(I_AD);
  c = 0;
  y = adc(T(ADSR_OFFS + (a >> 4)), SLOWDOWN);
  a = adc(T(ADSR_EXPT2 + y) & 0xf0, a);
  SIDV(R_AD) = adsr_nib(a, a & 0x0f);
  a = rd(I_SR);
  SIDV(R_SR) = adsr_nib(a, a & 0x0f);
  legato();
}

static void tick2(void) {
  uint8_t a = V(CUR_IFX), y;
  table_reset = 0xff;
  if (a && NEG(a - 0x2f)) {
    V(CUR_INS) = a;
    table_reset = 0x3f;
  }
  if (!(y = V(CUR_INS))) {
    legato();
    return;
  }
  set_ins(y);
  a = V(CUR_NOTE);
  if (!a) {
    ins_ptn_fx();
  } else if (!NEG(a - 0x60)) {
    note_fx(a);
  } else {
    c = 0;
    V(PITCH) = V(ARP_PITCH) = adc(a, rd(I_OCTAVE));
    if (V(CUR_FX) == 3) {
      legato();
      return;
    }
    c = 3 >= V(CUR_FX);
    adc(3, V(SLIDE));
    if (!c) {
      if (V(CUR_IFX) != 0x3f) {
        start_sound();
        return;
      }
      V(FMOD_HI) = 0x7f; /* legato: maximal portamento */
    }
    V(SLIDE) = 0x83;
    legato();
    return;
  }
  cnt_play2();
}

/* hard restart ahead of a new note; tick is 2 at row start, 1 a frame on */
static void hard_restart(uint8_t tick) {
  uint8_t y = V(CUR_NOTE);
  if (!y || y >= 0x60 || V(CUR_FX) == 3 || V(SLIDE) == 0xff ||
      (y = V(CUR_IFX)) == 0x3f) {
    cnt_play();
    return;
  }
  if (!y || !NEG(y - 0x3f))
    y = V(CUR_INS);
  set_ins(y);
  if (tick & rd(I_CTRL)) {
    V(GATE) = 0xfe;
    V(WAVE) &= 0xfe;
    SIDV(R_AD) = rd(I_HR_AD);
    SIDV(R_SR) = rd(I_HR_SR);
    if (rd(I_CTRL) & C_HR_GATEOFF) {
      SIDV(R_CTRL) = V(WAVE) = 0x18;
      return;
    }
  }
  set_ins(V(CUR_INS));
  wf_arp();
}

/* row start: read the pattern row */
static void tick0(void) {
  uint8_t y, a;
  set_ptn();
  V(CUR_IFX) = V(CUR_FX) = 0;
  y = V(PTN_POS);
  if (!V(PACK_CNT)) {
    a = rd(y);
    if (a < 0x70 || a >= 0x78)
      goto note;
    V(PACK_CNT) = a - 0x6e;
  }
  V(PACK_CNT)--;
  a = 0;
note:
  if (NEG(a)) {
    V(CUR_NOTE) = a & 0x7f;
    a = rd(++y);
    if (NEG(a)) {
      V(CUR_IFX) = a & 0x7f;
      a = V(CUR_FX) = rd(++y);
      if (!(a & 0xe0))
        V(CUR_VAL) = rd(++y);
    } else {
      V(CUR_IFX) = a;
    }
  } else {
    V(CUR_NOTE) = a;
  }
  V(PTN_POS) = y;
  hard_restart(2);
}

/* advance to the next row, at a pattern end to the next orderlist entry */
static void tick1(void) {
  uint8_t y, a;
  set_ptn();
  y = V(PTN_POS);
  if (!V(PACK_CNT) && rd(++y) == 0xff) {
    y = V(SEQ_POS) + 1;
    while ((a = seq_read(y)) == 0xff) { /* jump */
      a = seq_read(y + 1);
      y = NEG(a) ? 0 : a;
    }
    if (a == 0xfe) { /* end: hold the speed counter */
      V(SPD_CNT)--;
      cnt_play();
      return;
    }
    if (!NEG(a)) {
      V(CUR_PTN) = a;
      V(SEQ_POS) = y;
    }
    y = 0;
  }
  V(PTN_POS) = y;
  hard_restart(1);
}

static void do_track(void) {
  uint8_t y = V(TEMPO_POS) + 1, a = V(SPD_CNT), t = music[tempo - 1 + y],
          r = a - t;
  if (!r || NEG((a ^ t) & (a ^ r))) { /* tempo reached, or $80 restart */
    if (r)
      y = V(TEMPO_BASE);
    V(SPD_CNT) = 0;
    V(TEMPO_POS) = y;
  }
  a = V(SPD_CNT)++;
  if (!a)
    tick0();
  else if (NEG(a - 2))
    tick1();
  else if (a == 2)
    tick2();
  else
    cnt_play();
}

void tune_init(uint8_t song) {
  uint8_t r, a;
  (void)song; /* this build has one subtune */
  for (r = 0; r < 3; r++)
    seq_ptr[r] = word(word(H_SUBTUNES) + 2 * r);
  subtune_tempo = word(H_SUBTUNES) + 6;
  ptn_lo = word(H_PTN_LO);
  ptn_hi = word(H_PTN_HI);
  ins_lo = word(H_INS_LO);
  ins_hi = word(H_INS_HI);
  chords = word(H_CHORDS);
  chord_ptr = word(H_CHORD_PTR);
  tempo = word(H_TEMPO);
  tempo_prg = word(H_TEMPO_PRG);
  music[tempo] = music[subtune_tempo];
  for (r = 0; r < CLEARED; r++)
    ch[r] = 0;
  r = 0x18;
  do
    SIDREG(--r) = 0;
  while (r);
  band = keytrack = 0;
  main_vol = flt_ctrl = 0x0f;
  for (x = 14;; x -= 7) {
    do {
      a = seq_read(V(SEQ_POS));
      if (!NEG(a))
        break;
      if (a >= 0xfe)
        goto next;
    } while (++V(SEQ_POS));
    V(CUR_PTN) = a;
  next:
    if (!x)
      break;
  }
}

void tune_play(void) {
  uint8_t a, play = slow_cnt & 1;
  if (!(slow_cnt >>= 1))
    slow_cnt = DITHER_FULL;
  if (!play)
    return;
  for (x = 14;; x -= 7) {
    do_track();
    if (!x)
      break;
  }
  SIDREG(0x17) = filter_sw | reso;
  SIDREG(0x18) = main_vol | band;
  c = 0;
  a = keytrack;
  if (a)
    a = T(EXPTAB + adc(a, ch[flt_ctrl + PITCH]));
  SIDREG(0x16) = adc(a, cutoff_hi);
  SIDREG(0x15) = cutoff_lo;
}
