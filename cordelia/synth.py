"""Render a Score to a WAV: the additive tone and breathing drone of the
original drills, plus voice clips mixed in from a VoiceBank.

Everything is rendered in blocks of about a second, each block a whole
number of drone periods, so the drone tables never need rotating and the
file is streamed out as it is made.
"""

import math
import operator
import random
import struct
import wave

SAMPLE_RATE = 22050

DRONE_LEVEL = 0.36
MELODY_LEVEL = 0.38
VOICE_LEVEL = 0.22
FADE_SECONDS = 6.0           # fade the whole file in and out
LFO_MIN_PERIOD = 25.0        # the drone partials breathe this slowly
LFO_MAX_PERIOD = 70.0

# Drone partials as harmonics of the root: (harmonic, level, floor). Harmonics
# 2 and 4 are the core the key sits on, so they only dip to their floor.
PARTIALS = (
    (1, 0.50, 0.00),         # sub-octave
    (2, 1.00, 0.65),         # core
    (3, 0.26, 0.00),         # fifth
    (4, 0.55, 0.55),         # core, octave up
    (6, 0.17, 0.00),         # fifth
    (8, 0.20, 0.00),         # two octaves up
    (12, 0.07, 0.00),        # shimmer
    (16, 0.04, 0.00),        # shimmer, octave up
)


def midi_to_freq(midi):
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


# The note's harmonics, as in play_ambient_drone.py: no 5th (a major third
# would fight the minor scales), each rolled off the higher it sits so a low
# note keeps its warmth and a high one thins towards a sine.
HARMONICS = ((1, 1.00), (2, 0.30), (3, 0.12), (4, 0.06))
TONE_DAMPING = 1000.0        # Hz: a partial here is at half strength
TONE_TILT = 0.6              # level falls as (TONE_ANCHOR / f) ** TILT, ~3.6 dB an octave
TONE_ANCHOR = 196.0          # G3: the lowest question note keeps full level
ATTACK = 0.08                # seconds, raised cosine
RELEASE_SHARE = 0.35         # of the note, spent fading out


def partials(freq):
    out = [(h, level / (1.0 + (h * freq / TONE_DAMPING) ** 2)) for h, level in HARMONICS]
    total = sum(level for _, level in out)
    return [(h, level / total) for h, level in out]


def tone(t, f, parts):
    return sum(level * math.sin(2 * math.pi * h * f * t) for h, level in parts)


_segments = {}


def tone_segment(freq, n, level):
    """A whole note as a list of samples; identical notes are rendered once."""
    key = (round(freq, 2), n, round(level, 4))
    seg = _segments.get(key)
    if seg is None:
        attack = max(1, int(SAMPLE_RATE * ATTACK))
        release = max(1, int(n * RELEASE_SHARE))
        parts = partials(freq)
        gain = level * min(1.0, (TONE_ANCHOR / freq) ** TONE_TILT)
        seg = []
        for i in range(n):
            t = i / SAMPLE_RATE
            env = 1.0 if i >= attack else 0.5 - 0.5 * math.cos(math.pi * i / attack)
            remaining = n - i
            if remaining < release:
                env *= 0.5 - 0.5 * math.cos(math.pi * remaining / release)
            seg.append(gain * env * tone(t, freq, parts))
        _segments[key] = seg
    return seg


class Drone:
    """One period of each partial, tabulated once, and an LFO per partial."""

    def __init__(self, midi, rng):
        self.period = max(2, round(SAMPLE_RATE / midi_to_freq(midi)))
        p = self.period
        tables = [[math.sin(2 * math.pi * h * i / p) for i in range(p)] for h, _, _ in PARTIALS]
        self.zipped = list(zip(*tables))
        self.lfos = [(level, floor, rng.uniform(LFO_MIN_PERIOD, LFO_MAX_PERIOD),
                      rng.uniform(0, 2 * math.pi)) for _, level, floor in PARTIALS]
        self.scale = DRONE_LEVEL / sum(level for _, level, _ in PARTIALS)

    def amps(self, seconds):
        out = []
        for level, floor, period, phase in self.lfos:
            breath = 0.5 - 0.5 * math.cos(2 * math.pi * seconds / period + phase)
            out.append(self.scale * level * (floor + (1.0 - floor) * breath))
        return out

    def block(self, start, n):
        """n samples from sample `start`, which must be a multiple of the period."""
        out = []
        pos = 0
        while pos < n:
            a1, a2, a3, a4, a6, a8, a12, a16 = self.amps((start + pos) / SAMPLE_RATE)
            # unrolled on purpose: this runs once per sample of the session
            one = [a1 * s1 + a2 * s2 + a3 * s3 + a4 * s4 + a6 * s6 + a8 * s8 + a12 * s12 + a16 * s16
                   for s1, s2, s3, s4, s6, s8, s12, s16 in self.zipped]
            out.extend(one[: n - pos])
            pos += self.period
        return out


def render(score, path, bank=None, progress=None, seed=None):
    """Write `score` to the WAV at `path`. `bank` supplies the clips."""
    rng = random.Random(seed)
    total = int(SAMPLE_RATE * score.seconds)
    drone = Drone(score.drone["midi"], rng) if score.drone else None
    period = drone.period if drone else SAMPLE_RATE
    block = period * max(1, SAMPLE_RATE // period)

    pending = []
    for n in score.notes:
        seg = tone_segment(midi_to_freq(n.midi), int(SAMPLE_RATE * n.dur), MELODY_LEVEL * n.level)
        pending.append((int(SAMPLE_RATE * n.t), seg))
    for c in score.clips:
        if bank is None:
            raise ValueError("the score has voice clips but no voice bank was given")
        seg = [VOICE_LEVEL * c.level * v for v in bank.load(c.label)]
        pending.append((int(SAMPLE_RATE * c.t), seg))
    pending.sort(key=lambda e: e[0], reverse=True)   # pop() gives the earliest
    active = []
    fade = int(SAMPLE_RATE * FADE_SECONDS)

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for b0 in range(0, total, block):
            b1 = min(total, b0 + block)
            n = b1 - b0
            buf = drone.block(b0, n) if drone else [0.0] * n

            while pending and pending[-1][0] < b1:
                active.append(pending.pop())
            keep = []
            for start, seg in active:
                end = start + len(seg)
                if end <= b0:
                    continue
                keep.append((start, seg))
                lo, hi = max(start, b0), min(end, b1)
                s, so = lo - b0, lo - start
                buf[s:s + hi - lo] = map(operator.add, buf[s:s + hi - lo], seg[so:so + hi - lo])
            active = keep

            if b0 < fade or b1 > total - fade:
                for i in range(n):
                    pos = b0 + i
                    g = max(0.0, min(1.0, pos / fade, (total - pos) / fade))
                    buf[i] *= 0.5 - 0.5 * math.cos(math.pi * g)

            frames = [max(-32767, min(32767, int(32767 * x))) for x in buf]
            wf.writeframes(struct.pack(f"<{n}h", *frames))
            if progress:
                progress(b1 / total)
