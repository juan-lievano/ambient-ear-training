"""Pitch-shift a sung note without changing its speed or its vowel.

TD-PSOLA: the note is cut into one-period grains, each a Hann window two
periods wide centred on a pitch mark, and the grains are laid down again
spaced by the new period. The grains keep the vocal tract's shape, so the
voice stays the same singer; only the spacing, the pitch, changes. Plain
resampling, by contrast, speeds the whole recording up and shifts the
vowel with it, which six semitones up turns into a cartoon.

Made for the solfege samples: one sustained sung note each, so a single
pitch anchors the tracking. Standard library only, about half a second a
note.
"""

import math
import operator

FMIN, FMAX = 120.0, 900.0     # the pitch is looked for in this range
WOBBLE = 0.15                 # a sung note's pitch strays this far from its own, at most
VOICED = 0.7                  # correlation a frame needs for its period to be trusted


def _dot(a, b):
    return sum(map(operator.mul, a, b))


def global_period(x, sr):
    """Autocorrelation over the loudest half second: the note's own period."""
    peak_i = max(range(len(x)), key=lambda i: abs(x[i]))
    a, b = max(0, peak_i - sr // 4), min(len(x), peak_i + sr // 4)
    seg = x[a:b]
    best, best_r = int(sr / FMAX), -1.0
    for lag in range(int(sr / FMAX), int(sr / FMIN)):
        r = _dot(seg[:-lag], seg[lag:])
        if r > best_r:
            best, best_r = lag, r
    return best


def _period_at(x, pos, anchor, width, sr):
    """The lag near `anchor` that best repeats x at `pos`, and how well (0-1)."""
    lo = max(int(sr / FMAX), int(anchor * (1 - WOBBLE)))
    hi = min(int(sr / FMIN), int(anchor * (1 + WOBBLE)))
    seg = x[pos:pos + width]
    if len(seg) < width or pos + hi + width > len(x):
        return anchor, 0.0
    e0 = _dot(seg, seg) + 1e-9
    best, best_r = anchor, -1.0
    for lag in range(lo, hi + 1):
        other = x[pos + lag:pos + lag + width]
        r = _dot(seg, other) / math.sqrt(e0 * (_dot(other, other) + 1e-9))
        if r > best_r:
            best, best_r = lag, r
    return best, best_r


def pitch_marks(x, sr):
    """Marks one period apart through the note, following its pitch. The
    search stays anchored on the note's own period: a run of noisy frames
    would otherwise walk the estimate to the octave below, where the signal
    repeats just as well."""
    anchor = global_period(x, sr)
    width = 2 * anchor
    marks, periods = [], []
    pos, period = 0, anchor
    while pos + width < len(x):
        lag, r = _period_at(x, pos, anchor, width, sr)
        if r > VOICED:
            period = lag
        marks.append(pos)
        periods.append(period)
        pos += period
    return marks, periods


def shift(x, semitones, sr):
    """x with its pitch moved by `semitones`, the same length and timbre."""
    if semitones == 0:
        return list(x)
    ratio = 2.0 ** (semitones / 12.0)
    marks, periods = pitch_marks(x, sr)
    out = [0.0] * len(x)
    windows = {}
    t, k = float(marks[0]), 0
    while t < len(x):
        while k + 1 < len(marks) and abs(marks[k + 1] - t) < abs(marks[k] - t):
            k += 1                               # the grain nearest in time
        m, T = marks[k], periods[k]
        if T not in windows:
            windows[T] = [0.5 - 0.5 * math.cos(math.pi * i / T) for i in range(2 * T)]
        w = windows[T]
        start, src = int(round(t)) - T, m - T
        lo = max(0, -start, -src)
        hi = min(2 * T, len(out) - start, len(x) - src)
        for i in range(lo, hi):
            out[start + i] += x[src + i] * w[i]
        t += T / ratio
    gain = 1.0 / ratio            # grains overlap more when the pitch goes up
    return [v * gain for v in out]
