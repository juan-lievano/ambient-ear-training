"""Render a Score to a WAV: the additive tone and breathing drone of the
original drills, plus voice clips mixed in from a VoiceBank.

Everything is rendered in blocks of about a second, each block a whole
number of drone periods, so the drone tables never need rotating and the
file is streamed out as it is made.
"""

import itertools
import math
import operator
import random
import struct
import wave

SAMPLE_RATE = 22050

DRONE_LEVEL = 0.27           # down from 0.40 with the root an octave up: the
MELODY_LEVEL = 0.38          # same energy sits where the ear is more sensitive
VOICE_LEVEL = 0.22
FADE_IN_SECONDS = 1.0        # from silence
FADE_OUT_SECONDS = 2.0       # the drone alone, after the last note or clip

# Drone partials as harmonics of the root, which sits two octaves under the
# middle-register tonic (C2 for C, 65 Hz: the old C1 root was mostly below
# what laptop speakers reproduce, and a weight on the ears in headphones).
# (harmonic, level, floor, slowest breath, quickest breath): each partial
# swells on its own LFO, a cosine between floor and 1, with a period drawn
# once from that range. The core (2 and 4) and the sub-octave breathe over
# tens of seconds and never drop below their floor, so the key never
# wavers; the fifths and the shimmer above move a little quicker, for some
# colour, but still over several seconds: faster than that, and with more
# partials, the drone stopped sounding like one thing. No 5th harmonic (a
# major third would fight the minor scales) and no 7th.
PARTIALS = (
    (1, 0.55, 0.50, 22.0, 45.0),    # sub-octave
    (2, 1.00, 0.60, 18.0, 40.0),    # core
    (3, 0.24, 0.15, 11.0, 22.0),    # fifth
    (4, 0.35, 0.35, 18.0, 40.0),    # core, octave up: the mid-register tonic
    (6, 0.14, 0.05, 9.0, 18.0),     # fifth
    (8, 0.14, 0.05, 9.0, 18.0),     # two octaves up
    (12, 0.06, 0.00, 7.0, 15.0),    # shimmer
    (16, 0.03, 0.00, 7.0, 15.0),    # shimmer, octave up
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

# A metronome click, as in play_metronome.py: a sine that starts at full
# level and dies away.
CLICK_FREQ = 1000.0
CLICK_SECONDS = 0.03
CLICK_DECAY = 0.005          # seconds to fall to 1/e
CLIP_ATTACK = 0.04           # seconds: every clip eases in over this
CLIP_RELEASE = 0.25          # and fades out over this, cut short or not


def partials(freq):
    out = [(h, level / (1.0 + (h * freq / TONE_DAMPING) ** 2)) for h, level in HARMONICS]
    total = sum(level for _, level in out)
    return [(h, level / total) for h, level in out]


def tone(t, f, parts):
    return sum(level * math.sin(2 * math.pi * h * f * t) for h, level in parts)


_segments = {}


def click_segment(level):
    key = ("click", round(level, 4))
    seg = _segments.get(key)
    if seg is None:
        seg = [level * math.exp(-i / (SAMPLE_RATE * CLICK_DECAY))
               * math.sin(2 * math.pi * CLICK_FREQ * i / SAMPLE_RATE)
               for i in range(int(SAMPLE_RATE * CLICK_SECONDS))]
        _segments[key] = seg
    return seg


def shape(samples, seconds=None):
    """A clip, cut to `seconds` when given, eased in over CLIP_ATTACK and
    faded out over CLIP_RELEASE (raised cosines) so no edge is abrupt."""
    n = len(samples) if seconds is None else min(len(samples), int(SAMPLE_RATE * seconds))
    out = samples[:n]
    attack = min(n, int(SAMPLE_RATE * CLIP_ATTACK))
    for i in range(attack):
        out[i] *= 0.5 - 0.5 * math.cos(math.pi * i / attack)
    release = min(n, int(SAMPLE_RATE * CLIP_RELEASE))
    for i in range(release):
        out[n - 1 - i] *= 0.5 - 0.5 * math.cos(math.pi * i / release)
    return out


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
        self.tables = [[math.sin(2 * math.pi * h * i / p) for i in range(p)] for h, *_ in PARTIALS]
        self.lfos = [(level, floor, rng.uniform(slow, quick), rng.uniform(0, 2 * math.pi))
                     for _, level, floor, slow, quick in PARTIALS]
        self.scale = DRONE_LEVEL / sum(level for _, level, *_ in PARTIALS)

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
            # the amplitudes hold for one period (a few ms), then each table is
            # scaled and summed at C speed: no per-sample Python
            one = [0.0] * self.period
            for a, table in zip(self.amps((start + pos) / SAMPLE_RATE), self.tables):
                one = list(map(operator.add, one, map(operator.mul, table, itertools.repeat(a))))
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
        if n.voice == "click":
            seg = click_segment(MELODY_LEVEL * n.level)
        else:
            seg = tone_segment(midi_to_freq(n.midi), int(SAMPLE_RATE * n.dur), MELODY_LEVEL * n.level)
        pending.append((int(SAMPLE_RATE * n.t), seg))
    for c in score.clips:
        if bank is None:
            raise ValueError("the score has voice clips but no voice bank was given")
        seg = [VOICE_LEVEL * c.level * v for v in shape(bank.load(c.label), c.dur)]
        pending.append((int(SAMPLE_RATE * c.t), seg))
    # The fade-out never touches a note or a clip: it starts after the last one
    # ends, over whatever tail the score leaves, up to FADE_OUT_SECONDS.
    last_end = max((start + len(seg) for start, seg in pending), default=0)
    pending.sort(key=lambda e: e[0], reverse=True)   # pop() gives the earliest
    active = []
    fade = int(SAMPLE_RATE * FADE_IN_SECONDS)
    out_start = max(total - int(SAMPLE_RATE * FADE_OUT_SECONDS), last_end)
    out_len = max(1, total - out_start)

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

            if b0 < fade or b1 > out_start:
                for i in range(n):
                    pos = b0 + i
                    g = max(0.0, min(1.0, pos / fade, (total - pos) / out_len))
                    buf[i] *= 0.5 - 0.5 * math.cos(math.pi * g)

            frames = [max(-32767, min(32767, int(32767 * x))) for x in buf]
            wf.writeframes(struct.pack(f"<{n}h", *frames))
            if progress:
                progress(b1 / total)
