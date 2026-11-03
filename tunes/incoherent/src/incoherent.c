/* C translation of the GoatTracker 2 playroutine exported for Linus & Fanta's
 * Incoherent Nightmare (tune 2).
 *
 * Each play call steps the filter table, then each voice: the tempo counter,
 * on tick 0 the order list, instrument and pattern command; then the wave
 * table, the continuous effect, and either the next pattern row (on the
 * instrument's gate timer tick) or the pulse table; then the SID registers.
 *
 * DATA (incoherent_data.h) is the original image from DATA_ORG to its end:
 * frequency, order list, pattern, instrument, wave, pulse, filter and speed
 * tables, order lists and patterns. Addresses below are original ones, read
 * with at(). Tables indexed from 1 are given by their address minus one, as
 * in the 6502 code. VOICE_INIT, FILTER_INIT and the other INIT_ values are
 * the state the original keeps in its image, which its init only partly
 * resets.
 */
#include <c64.h>
#include <stdint.h>

#include "driver.h"
#include "incoherent_data.h"

#define SIDREG(o) (((volatile uint8_t *)&SID)[o])
enum { FREQ_LO_REG, FREQ_HI_REG, PW_LO, PW_HI, CTRL, AD_REG, SR_REG };
enum { CUTOFF_LO = 0x15, CUTOFF_HI, RES_ROUTE, MODE_VOL };

enum { FREQ_LO = 0x0ccf, FREQ_HI = 0x0d2f };
enum {
  ORDER_LO = 0x0d8f,
  ORDER_HI = 0x0d92,
  PATT_LO = 0x0d95,
  PATT_HI = 0x0e0f
};
/* instrument columns, indexed by instrument number */
enum {
  INS_AD = 0x0e88,
  INS_SR = 0x0eb3,
  INS_WAVE = 0x0ede,
  INS_PULSE = 0x0f09,
  INS_FILTER = 0x0f34,
  INS_VIB = 0x0f5f,
  INS_VIB_DELAY = 0x0f8a,
  INS_GATE_TIMER = 0x0fb5,
  INS_FIRST_WAVE = 0x0fe0
};
/* left and right columns of the wave, pulse, filter and speed tables */
enum { WAVE_L = 0x100b, WAVE_R = 0x1079, PULSE_L = 0x10e7, PULSE_R = 0x1133 };
enum { FILT_L = 0x117f, FILT_R = 0x1224, SPEED_L = 0x12ca, SPEED_R = 0x12e2 };

enum { JUMP = 0xff, TRANSPOSE = 0xe0, TRANSPOSE_BASE = 0xf0 };
/* pattern bytes: instrument, command (with a note after below CMD_ONLY),
 * note, rest, key off/on, and repeat counts from REPEAT */
enum { CMD = 0x40, CMD_ONLY = 0x50, NOTE = 0x60, REPEAT = 0xc0 };
enum { REST = 0xbd, NOTE_BASE = 0x60, WAVE_CMD = 0xe0 };
enum { WAVE_DELAY = 0x10, WAVE_OFFSET = 0x10 };
enum { GATE_ON = 0xff, GATE_OFF = 0xfe, HR_AD = 0x0f, HR_SR = 0x00 };
enum { NUM_EFFECTS = 5, VOLUME = 0x0f };
/* effects 0-4 run each tick; commands also run on tick 0 */
enum {
  INS_VIBRATO,
  PORTA_UP,
  PORTA_DOWN,
  TONE_PORTA,
  VIBRATO,
  SET_AD,
  SET_SR,
  SET_WAVE,
  SET_WAVE_PTR,
  SET_PULSE_PTR,
  SET_FILTER_PTR,
  SET_CUTOFF,
  SET_CUTOFF2,
  FUNKTEMPO,
  FUNKTEMPO2,
  SET_TEMPO
};

static const uint8_t data[] = {DATA};
#define at(a) data[(a) - DATA_ORG]
#define word(lo, hi) ((uint16_t)(hi) << 8 | (lo))
#define freq_of(n) word(at(FREQ_LO + (n)), at(FREQ_HI + (n)))
#define ptr_at(lo, hi, i) word(at((lo) + (i)), at((hi) + (i)))

struct voice {
  /* cleared by init */
  uint8_t song_pos, transpose, patt_pos, repeat, cmd, cmd_data;
  uint8_t effect, param, new_note, wave_ptr, wave, pulse_ptr, pulse_time;
  /* kept from the image */
  uint8_t order, pattern, tempo, tick, note, instr, gate;
  uint8_t vib_phase, vib_delay, wave_time;
  uint16_t freq, pulse;
  uint8_t ad, sr, gate_timer, last_note;
};

static struct voice voices[3] = VOICE_INIT;
static struct {
  uint8_t ptr, time, cutoff, ctrl, type;
} filter = FILTER_INIT;
static uint8_t funktempo[2] = INIT_FUNKTEMPO;
static uint8_t init_song = INIT_SONG;
static uint16_t speed;

void tune_init(uint8_t song) { init_song = song; }

static void filter_next(uint8_t y) {
  uint8_t next = at(FILT_L + 1 + y);
  ++y;
  filter.ptr = next == JUMP ? at(FILT_R + y) : y;
}

static void filter_step(void) {
  uint8_t y = filter.ptr, a;
  if (y) {
    if (!filter.time) {
      a = at(FILT_L + y);
      if (a & 0x80) {
        filter.type = a << 1;
        filter.ctrl = at(FILT_R + y);
        if (!at(FILT_L + 1 + y))
          filter.cutoff = at(FILT_R + ++y);
        filter_next(y);
        goto out;
      }
      if (!a) {
        filter.cutoff = at(FILT_R + y);
        filter_next(y);
        goto out;
      }
      filter.time = a;
    }
    filter.cutoff += at(FILT_R + y);
    if (!--filter.time)
      filter_next(y);
  }
out:
  SIDREG(CUTOFF_HI) = filter.cutoff;
  SIDREG(RES_ROUTE) = filter.ctrl;
  SIDREG(MODE_VOL) = filter.type | VOLUME;
}

static void set_tempo_all(uint8_t t) {
  voices[0].tempo = voices[1].tempo = voices[2].tempo = t;
}

/* tick 0 pattern commands and wave table commands 5-15; `y` is the
 * instrument for INS_VIBRATO */
static void command(struct voice *v, uint8_t cmd, uint8_t d, uint8_t y) {
  switch (cmd) {
  case INS_VIBRATO:
    d = at(INS_VIB + y);
    goto effect;
  case PORTA_UP:
  case PORTA_DOWN:
    v->vib_phase = 0;
    /* fall through */
  case TONE_PORTA:
  case VIBRATO:
  effect:
    v->param = d;
    v->effect = v->cmd;
    break;
  case SET_AD:
    v->ad = d;
    break;
  case SET_SR:
    v->sr = d;
    break;
  case SET_WAVE:
    v->wave = d;
    break;
  case SET_WAVE_PTR:
    v->wave_ptr = d;
    v->wave_time = 0;
    break;
  case SET_PULSE_PTR:
    v->pulse_ptr = d;
    v->pulse_time = 0;
    break;
  case SET_FILTER_PTR:
    filter.time = 0;
    filter.ptr = d;
    break;
  case SET_CUTOFF:
  case SET_CUTOFF2:
    filter.cutoff = d;
    break;
  case FUNKTEMPO:
  case FUNKTEMPO2:
    funktempo[0] = at(SPEED_L + d);
    funktempo[1] = at(SPEED_R + d);
    set_tempo_all(0);
    break;
  default:
    if (d & 0x80)
      v->tempo = d & 0x7f;
    else
      set_tempo_all(d);
  }
}

static void set_note(struct voice *v, uint8_t n) {
  v->last_note = n;
  v->vib_phase = 0;
  v->freq = freq_of(n);
}

/* speed table entry `y`: a 16-bit speed, or with bit 7 of the left column
 * the last note's semitone width shifted right by the right column. Returns
 * the 6502 zero flag the instrument vibrato tests. */
static uint8_t load_speed(struct voice *v, uint8_t y) {
  uint8_t shift = at(SPEED_R + y);
  if (!(at(SPEED_L + y) & 0x80)) {
    speed = word(shift, at(SPEED_L + y));
    return !shift;
  }
  speed = freq_of(v->last_note + 1) - freq_of(v->last_note);
  while (shift--)
    speed >>= 1;
  return !y;
}

static void vibrato(struct voice *v, uint8_t y) {
  uint8_t depth = at(SPEED_L + y), p = v->vib_phase;
  if (!(depth & 0x80))
    speed &= 0xff;
  depth &= 0x7f;
  if (!(p & 0x80) && p > depth)
    p = ~p;
  v->vib_phase = p += 2;
  v->freq += p & 1 ? -speed : speed;
}

/* toneportamento towards `note`; on overshoot the step keeps the carry of
 * the overshoot test */
static void tone_porta(struct voice *v) {
  uint16_t target = freq_of(v->note), d = v->freq - target, r;
  if (v->freq >= target) {
    r = d - speed;
    if (!(r & 0x8000)) {
      v->freq -= speed + (d < speed);
      return;
    }
  } else {
    r = d + speed;
    if (r & 0x8000) {
      v->freq += speed + (r < d);
      return;
    }
  }
  set_note(v, v->note);
}

static void effect(struct voice *v, uint8_t cmd, uint8_t y) {
  uint8_t zero = load_speed(v, y);
  switch (cmd) {
  case INS_VIBRATO:
    if (zero)
      break;
    if (v->vib_delay) {
      --v->vib_delay;
      break;
    }
    /* fall through */
  case VIBRATO:
    vibrato(v, y);
    break;
  case PORTA_UP:
    v->freq += speed;
    break;
  case PORTA_DOWN:
    v->freq -= speed;
    break;
  default:
    if (y)
      tone_porta(v);
    else
      set_note(v, v->note);
  }
}

/* wave table step; returns nonzero if it replaced this tick's effect */
static uint8_t wave_step(struct voice *v) {
  uint8_t i = v->wave_ptr, y, a;
  if (!i)
    return 0;
  a = at(WAVE_L + i);
  if (a < WAVE_DELAY) {
    if (a != v->wave_time && ++v->wave_time)
      return 0;
  } else if ((uint8_t)(a - WAVE_OFFSET) < WAVE_CMD) {
    v->wave = a - WAVE_OFFSET;
  }
  y = i + 1;
  v->wave_ptr = at(WAVE_L + 1 + i) == JUMP ? at(WAVE_R + y) : y;
  v->wave_time = 0;
  a = at(WAVE_L - 1 + y);
  if (a >= WAVE_CMD) {
    uint8_t c = a & 0x0f, d = at(WAVE_R - 1 + y);
    if (c < NUM_EFFECTS)
      effect(v, c, d);
    else
      command(v, c, d, c);
    return 1;
  }
  a = at(WAVE_R - 1 + y);
  if (!a)
    return 0;
  set_note(v, a & 0x80 ? (a + v->note) & 0x7f : a);
  return 1;
}

static void next_order(struct voice *v) {
  uint16_t p = ptr_at(ORDER_LO, ORDER_HI, v->order);
  uint8_t y = v->song_pos, a = at(p + y);
  if (a == JUMP) {
    y = at(p + (uint8_t)(y + 1));
    a = at(p + y);
  }
  if (a >= TRANSPOSE) {
    v->transpose = a - TRANSPOSE_BASE;
    a = at(p + ++y);
  }
  v->pattern = a;
  v->song_pos = y + 1;
}

static void read_row(struct voice *v) {
  uint16_t p = ptr_at(PATT_LO, PATT_HI, v->pattern);
  uint8_t y = v->patt_pos, a = at(p + y);
  if (a >= REPEAT) {
    v->repeat = (v->repeat ? v->repeat : a) + 1;
    if (v->repeat)
      return;
    goto next;
  }
  if (a < CMD) {
    v->instr = a;
    a = at(p + ++y);
  }
  if (a < NOTE) {
    uint8_t only = a >= CMD_ONLY;
    if ((v->cmd = a & 0x0f))
      v->cmd_data = at(p + ++y);
    if (only)
      goto next;
    a = at(p + ++y);
  }
  if (a == REST)
    goto next;
  if (a > REST) {
    v->gate = a | 0xf0;
    goto next;
  }
  v->new_note = a + v->transpose;
  if (v->cmd == TONE_PORTA)
    goto next;
  if (v->instr < HR_LIMIT) {
    v->sr = HR_SR;
    v->ad = HR_AD;
  }
  v->gate = GATE_OFF;
next:
  ++y;
  v->patt_pos = at(p + y) ? y : 0;
}

static void pulse_step(struct voice *v) {
  uint8_t y = v->pulse_ptr, a;
  if (!v->pulse_time) {
    a = at(PULSE_L + y);
    if (a & 0x80) {
      v->pulse = word(at(PULSE_R + y), a);
      goto next;
    }
    v->pulse_time = a;
  }
  v->pulse += (int8_t)at(PULSE_R + y);
  if (--v->pulse_time)
    return;
next:
  a = at(PULSE_L + 1 + y);
  ++y;
  v->pulse_ptr = a == JUMP ? at(PULSE_R + y) : y;
}

static void write_regs(const struct voice *v, uint8_t r) {
  SIDREG(r + AD_REG) = v->ad;
  SIDREG(r + SR_REG) = v->sr;
  SIDREG(r + PW_LO) = v->pulse;
  SIDREG(r + PW_HI) = v->pulse >> 8;
  SIDREG(r + FREQ_LO_REG) = v->freq;
  SIDREG(r + FREQ_HI_REG) = v->freq >> 8;
  SIDREG(r + CTRL) = v->wave & v->gate;
}

/* tick 0: returns nonzero if a new note started the instrument */
static uint8_t start_row(struct voice *v) {
  uint8_t i, w;
  if (!v->patt_pos)
    next_order(v);
  i = v->instr;
  v->gate_timer = at(INS_GATE_TIMER + i);
  if (v->new_note) {
    v->note = v->new_note - NOTE_BASE;
    v->effect = v->new_note = 0;
    v->vib_delay = at(INS_VIB_DELAY + i);
    v->param = at(INS_VIB + i);
    if (v->cmd != TONE_PORTA) {
      if ((w = at(INS_FIRST_WAVE + i))) {
        if (w < GATE_OFF) {
          v->wave = w;
          w = GATE_ON;
        }
        v->gate = w;
      }
      if ((w = at(INS_PULSE + i))) {
        v->pulse_ptr = w;
        v->pulse_time = 0;
      }
      if ((w = at(INS_FILTER + i))) {
        filter.ptr = w;
        filter.time = 0;
      }
      v->wave_ptr = at(INS_WAVE + i);
      v->sr = at(INS_SR + i);
      v->ad = at(INS_AD + i);
      command(v, v->cmd, v->cmd_data, i);
      return 1;
    }
  }
  command(v, v->cmd, v->cmd_data, i);
  return 0;
}

static void voice_play(struct voice *v, uint8_t r) {
  if (!--v->tick) {
    if (start_row(v))
      goto regs;
  } else if (v->tick & 0x80) {
    uint8_t t = v->tempo;
    if (t >= 2) {
      v->tick = t;
    } else {
      v->tempo = t ^ 1;
      v->tick = funktempo[t] - 1;
    }
  }
  if (!wave_step(v) && v->tick)
    effect(v, v->effect, v->param);
  if (v->tick == v->gate_timer)
    read_row(v);
  else if (v->pulse_ptr && (v->tick | v->patt_pos))
    pulse_step(v);
regs:
  write_regs(v, r);
}

void tune_play(void) {
  uint8_t k;
  if (!(init_song & 0x80)) {
    SIDREG(CUTOFF_LO) = 0;
    filter.ctrl = filter.ptr = 0;
    init_song = 0xff;
    for (k = 0; k < 3; ++k) {
      struct voice *v = &voices[k];
      v->song_pos = v->transpose = v->patt_pos = v->repeat = v->cmd = 0;
      v->cmd_data = v->effect = v->param = v->new_note = v->wave_ptr = 0;
      v->wave = v->pulse_ptr = v->pulse_time = 0;
      v->tempo = INIT_TEMPO;
      v->tick = v->instr = 1;
      SIDREG(k * 7 + CTRL) = v->wave & v->gate;
    }
    return;
  }
  filter_step();
  for (k = 0; k < 3; ++k)
    voice_play(&voices[k], k * 7);
}
