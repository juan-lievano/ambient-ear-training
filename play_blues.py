#!/usr/bin/env python3
"""A standard 12-bar blues to play over, looping for as long as you ask.

Usage:
    python3 play_blues.py [--minutes N] [--bpm N] [--key NOTE]
                          [--bass walking|drone] [--chords]
                          [--metronome] [--plain]

    python3 play_blues.py                        # 10 min in C at 100 BPM:
                                                 # a walking bass, nothing else
    python3 play_blues.py --minutes 20 --bpm 80
    python3 play_blues.py --key A --chords       # the chords over the bass
    python3 play_blues.py --bass drone           # no chords, a sliding drone
    python3 play_blues.py --metronome            # with a click on every beat
    python3 play_blues.py --plain                # the machine version

The form is the plain one, every chord a dominant seventh, with the V in
bar 12 turning each chorus round into the next:

    I    I    I    I
    IV   IV   I    I
    V    IV   I    V

By default the bass is all there is to hear the form by, and it does one
of two things:

  --bass walking   a boogie-woogie line, a quarter note per beat: mostly
                   1 3 5 6 up through one bar and b7 6 5 3 back down
                   through the next wherever a chord lasts two bars, as
                   taught at studybass.com/lessons/blues-bass/
                   the-boogie-woogie-blues-pattern/ — drawn afresh for
                   every chorus, so that once in a long while a bar goes
                   1 3 5 b7, or 1 5 6 b7, or comes down 8 b7 6 5, and no
                   two choruses are quite the same (the default)
  --bass drone     holds the root of the chord, one unbroken note for as
                   long as the chord lasts

--chords puts the chords in over the bass, each bar the chord struck once
and left to ring.

Nothing is struck at quite the same level twice. Beat 1 is the strongest,
3 a little behind it, 2 and 4 lighter; bar 1 of the form is leaned on,
and so, less, is any bar where the chord changes; whole bars come out a
little softer or firmer than written; every note is nudged a few percent
either way on top, and lands a few milliseconds early or late. Softer
notes are also rounder, their overtones weaker and their attack slower,
the way a gentler pluck is, and every overtone is a little stronger or
weaker than last time regardless, so the touch varies from pluck to
pluck. A handful of choruses are played out this way, each with its own
line, and shuffled through the session, so it never quite repeats.
--plain switches all of that off: the standard line in every bar, every
note struck alike and dead on the beat, one chorus looped, and a tone
whose overtones hold their balance as the note dies — the way it was
first written, which has a charm of its own.

Without them the drone never stops: it holds each root to the end of
its last bar and slides, quickly, into the next one, arriving on the
downbeat, so bars 1 to 4 are one unbroken note and every change of chord
is a glide. A very faint click on every beat goes with it, to keep your
place in a bar where nothing else is moving; the walking bass on its own
already plays every beat, so it gets none.

Otherwise there is no click unless --metronome asks for one. After the
last chorus the turnaround resolves to a final I that rings out.

--minutes is filled with as many whole choruses as fit, never fewer than
one, so the session ends at the end of the form rather than part-way
through it. Every run starts with a one-line reminder of the flags, then
the progression as a table, and the chorus and bar being played are shown
while it runs.

One chorus is rendered and the session is that chorus written out over and
over into one WAV up front, then played with macOS's afplay, so the tempo
is in the file. Standard library only.
"""

import functools
import math
import operator
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
import wave

SAMPLE_RATE = 22050

DEFAULT_MINUTES = 10.0
DEFAULT_BPM = 100
MIN_BPM = 30
MAX_BPM = 240
DEFAULT_KEY = "C"

BEATS_PER_BAR = 4
PROGRESSION = ("I", "I", "I", "I",
               "IV", "IV", "I", "I",
               "V", "IV", "I", "V")
ROOTS = {"I": 0, "IV": 5, "V": 7}     # semitones above the tonic
CHORD = (0, 4, 7, 10)                 # dominant seventh
# The walking bass, a bar at a time, as intervals from the chord's root
# with how often each bar is chosen. Odd bars of a chord walk up, even
# bars come back down. The standard line nearly always; the others are
# there so that the choruses are not all the same, about one bar in
# twenty between them.
WALK_UP = {(0, 4, 7, 9): 36,          # 1 3 5 6, the boogie-woogie line
           (0, 4, 7, 10): 1,          # 1 3 5 b7
           (0, 7, 9, 10): 1}          # 1 5 6 b7
WALK_DOWN = {(10, 9, 7, 4): 36,       # b7 6 5 3
             (12, 10, 9, 7): 1,       # 8 b7 6 5
             (0, 4, 7, 9): 1}         # or up again
APPROACH_CHANCE = 0.0                 # last beat before a change slides in
                                      # to the next root from a half step away
EIGHTHS_CHANCE = 0.0                  # a beat split into two eighths, the
                                      # second a half step short of what follows
BASS_STYLES = ("walking", "drone")
ENDING_BARS = 2                       # the final I7 rings this long

CHORD_LOW = 48           # chord roots sit in C3-B3
BASS_LOW = 33            # bass roots sit in A1-G#2
CHORD_LEVEL = 0.50       # the four chord tones together
BASS_LEVEL = 0.46        # a walking note at its start
DRONE_LEVEL = 0.30       # the held root, which never dies away
SOLO_DRONE_LEVEL = 0.50  # and the same root when the chords are left out
DRONE_FADE = 0.08        # seconds for the held root to come in and go out
SLIDE_SECONDS = 0.15     # how long the drone takes to slide to the next root,
SLIDE_BEATS = 0.25       # or this much of a beat if that is shorter
CLICK_LEVEL = 0.10
FAINT_CLICK_LEVEL = 0.02 # the click that goes with the drone on its own
CHORD_DECAY = 1.4        # the chord falls by e to this power across its bar
BASS_DECAY = 2.0         # and a bass note across its beat
ENDING_DECAY = 4.0

# The tone of every note: a fundamental and three overtones, each at this
# level when the note starts. The overtones die away a little faster than
# the fundamental, harmonic h at (1 + BRIGHTNESS_DECAY * (h - 1)) times its
# rate, so a struck note settles towards a rounder hum; 0 holds the mix
# steady. A held note (decay 0) keeps every overtone regardless.
HARMONICS = ((1, 1.0), (2, 0.4), (3, 0.2), (4, 0.1))
HARMONICS_PEAK = sum(level for _, level in HARMONICS)
BRIGHTNESS_DECAY = 0.25

# Dynamics. Every struck note's level is its beat's accent, times its
# bar's — the bar accent below wobbled by up to BAR_JITTER, so whole bars
# come out a little softer or firmer — times a wobble of its own of up to
# JITTER, and it lands up to TIMING seconds early or late. All of that
# would repeat every twelve bars if the chorus were rendered once, so
# VARIANTS of them are played out, each with its own line too, and
# shuffled through the session, never the same one twice in a row.
BEAT_ACCENTS = (1.0, 0.85, 0.92, 0.85)  # beats 1 to 4
OFFBEAT_ACCENT = 0.8                  # the second of a pair of eighths
TOP_ACCENT = 1.12                     # bar 1, the top of the form
CHANGE_ACCENT = 1.06                  # any other bar where the chord changes
BAR_JITTER = 0.10
JITTER = 0.08
TIMING = 0.006
# The touch: at each strike every overtone's level is drawn up to TOUCH
# either way from its place in HARMONICS, so no two plucks have quite the
# same colour; then the overtones follow the loudness, harmonic h scaled by
# the note's dynamic to the power SOFT_COLOUR * (h - 1), and the attack
# slows as the note softens — a gentle pluck is rounder and less sharp.
TOUCH = 0.35
SOFT_COLOUR = 1.0
ATTACK = 0.008                        # seconds, at full strength
VARIANTS = 8

PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def midi_to_freq(midi_note):
    return 440.0 * 2 ** ((midi_note - 69) / 12)


@functools.lru_cache(maxsize=None)
def partial_samples(midi_note, harmonic, n, decay, release, brightness):
    """One harmonic of one note at unit level, dying away over `n` samples.

    The decay is measured against the note's own length rather than in
    seconds, so a bar sounds the same shape at any tempo; a decay of zero
    is a held note. It is the fundamental's decay: each overtone's is a
    little faster (`brightness`, normally BRIGHTNESS_DECAY), carried along
    by multiplying the level down a notch every sample. The last few milliseconds are faded so the
    note ends at silence — which is what lets the chorus be laid end to
    end with itself without a step at the join.

    Cached, since the same note at the same length is struck over and
    over; add_note weighs the harmonics as each strike wants them, and
    puts the attack on, since that differs from strike to strike.
    """
    step = 2 * math.pi * midi_to_freq(midi_note) * harmonic / SAMPLE_RATE
    release *= SAMPLE_RATE
    fade = math.exp(-decay * (1 + brightness * (harmonic - 1)) / n)
    out, level = [], 1.0
    for i in range(n):
        out.append(min(1.0, (n - i) / release) * level * math.sin(step * i))
        level *= fade
    return out


def add_note(samples, start, midi_note, length, level, decay,
             attack=ATTACK, release=0.03, touch=None, brightness=BRIGHTNESS_DECAY):
    """A note mixed in at `start` at `level`, dying away over `length` samples.

    `touch` is a multiplier per overtone, from the second harmonic up, for
    this strike's colour; without it the note is HARMONICS as written.
    `brightness` is how much faster than the fundamental the overtones die.
    """
    n = min(length, len(samples) - start)
    ramp = min(n, round(attack * SAMPLE_RATE))
    for index, (harmonic, weight) in enumerate(HARMONICS):
        gain = level * weight / HARMONICS_PEAK
        if touch and index:
            gain *= touch[index - 1]
        part = [gain * v for v in partial_samples(midi_note, harmonic, n, decay,
                                                  release, brightness)]
        for i in range(ramp):
            part[i] *= i / ramp
        samples[start:start + n] = map(operator.add, samples[start:start + n], part)


def add_click(samples, start, level):
    """The soft tick that marks a beat."""
    n = int(SAMPLE_RATE * 0.03)
    for i in range(min(n, len(samples) - start)):
        decay = 1.0 - i / n
        samples[start + i] += (level * decay * decay
                               * math.sin(2 * math.pi * 1800.0 * i / SAMPLE_RATE))


def pack_frames(samples):
    ints = [max(-32767, min(32767, int(v * 32767))) for v in samples]
    return struct.pack(f"<{len(ints)}h", *ints)


def chord_roots(tonic, numeral):
    """Where this chord's root sits for the chord and for the bass, as MIDI notes."""
    pitch_class = (tonic + ROOTS[numeral]) % 12
    return (CHORD_LOW + pitch_class,
            BASS_LOW + (pitch_class - BASS_LOW) % 12)


def chord_runs():
    """The form as stretches of one chord: (numeral, first bar, bars)."""
    runs = []
    for bar, numeral in enumerate(PROGRESSION):
        if runs and runs[-1][0] == numeral:
            runs[-1][2] += 1
        else:
            runs.append([numeral, bar, 1])
    return runs


def sliding_drone(tonic, beat_samples):
    """The held root for one chorus with nothing over it, as samples.

    One oscillator runs through the whole form, so the note never stops:
    it sits on a chord's root until the last moments of that chord's last
    bar, then slides to the next root and lands on it at the bar line. The
    slide eases in and out and is even in pitch rather than in frequency.

    The chorus is played end to end with itself, so it has to hold a whole
    number of cycles for the note to carry on unbroken from bar 12 into
    bar 1; every pitch is nudged by the same hair, well under a cent, to
    make it so.
    """
    bar_samples = round(BEATS_PER_BAR * beat_samples)
    slide = round(min(SLIDE_SECONDS * SAMPLE_RATE, SLIDE_BEATS * beat_samples))
    runs = chord_runs()
    steps = []      # cycles advanced by each sample
    for index, (numeral, _, bars) in enumerate(runs):
        here = chord_roots(tonic, numeral)[1]
        there = chord_roots(tonic, runs[(index + 1) % len(runs)][0])[1]
        steps += [midi_to_freq(here) / SAMPLE_RATE] * (bars * bar_samples - slide)
        steps += [midi_to_freq(here + (there - here)
                               * (0.5 - 0.5 * math.cos(math.pi * (i + 1) / slide)))
                  / SAMPLE_RATE for i in range(slide)]
    nudge = round(sum(steps)) / sum(steps)

    samples, cycles = [], 0.0
    for step in steps:
        phase = 2 * math.pi * cycles
        samples.append(SOLO_DRONE_LEVEL / HARMONICS_PEAK
                       * sum(a * math.sin(h * phase) for h, a in HARMONICS))
        cycles += step * nudge
    return samples


def weighted_choice(rng, weights):
    """A key of `weights`, drawn in proportion to its weight."""
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def walking_line(tonic, rng, plain=False):
    """One chorus of walking bass, as (bar, beat, beats long, MIDI note).

    Each bar is drawn from WALK_UP or WALK_DOWN; then, sometimes, the last
    beat before a chord change becomes a chromatic approach to the next
    root, and a beat in the middle of a bar is split into two eighths with
    a passing note a half step short of the beat after. `plain` is the
    first pattern of each table in every bar and none of that.
    """
    runs = chord_runs()
    notes = []
    for index, (numeral, first, bars) in enumerate(runs):
        root = chord_roots(tonic, numeral)[1]
        next_root = chord_roots(tonic, runs[(index + 1) % len(runs)][0])[1]
        for bar in range(bars):
            table = WALK_DOWN if bar % 2 else WALK_UP
            pattern = next(iter(table)) if plain else weighted_choice(rng, table)
            pitches = [root + interval for interval in pattern]
            if not plain and bar == bars - 1 and rng.random() < APPROACH_CHANCE:
                pitches[-1] = next_root + rng.choice((-1, 1))
            split = (rng.randrange(1, BEATS_PER_BAR - 1)
                     if not plain and rng.random() < EIGHTHS_CHANCE else None)
            for beat, pitch in enumerate(pitches):
                following = pitches[beat + 1] if beat + 1 < len(pitches) else None
                if beat == split and following is not None and abs(following - pitch) > 1:
                    passing = following - 1 if following > pitch else following + 1
                    notes.append((first + bar, beat, 0.5, pitch))
                    notes.append((first + bar, beat + 0.5, 0.5, passing))
                else:
                    notes.append((first + bar, beat, 1.0, pitch))
    return notes


def bar_accents():
    """How hard each bar of the form is played, as level multipliers."""
    changes = {first for _, first, _ in chord_runs()}
    return [TOP_ACCENT if bar == 0 else CHANGE_ACCENT if bar in changes else 1.0
            for bar in range(len(PROGRESSION))]


def synth_chorus(tonic, beat_samples, bass, bass_only, click_level, rng, plain):
    """One pass through the form: chords, the bass, and clicks if asked for.

    Every struck note is played at its own level: the accent of its beat
    and bar, wobbled by `rng`. `plain` is none of that: every note the
    same, exactly on the beat, with an unchanging tone. The sliding drone
    is not here; it is laid over the finished chorus by synth_session.
    """
    bar_samples = round(BEATS_PER_BAR * beat_samples)
    samples = [0.0] * (len(PROGRESSION) * bar_samples)
    bar_levels = [1.0 if plain else accent * (1 + rng.uniform(-BAR_JITTER, BAR_JITTER))
                  for accent in bar_accents()]

    def wobble():
        return 1.0 if plain else 1 + rng.uniform(-JITTER, JITTER)

    def strike(start, midi_note, length, level, dynamic, decay):
        """A note at `level` whose colour and attack follow `dynamic`."""
        if plain:
            add_note(samples, start, midi_note, length, level, decay, brightness=0.0)
            return
        touch = [(1 + rng.uniform(-TOUCH, TOUCH)) * dynamic ** (SOFT_COLOUR * (h - 1))
                 for h, _ in HARMONICS[1:]]
        start = max(0, start + round(rng.uniform(-TIMING, TIMING) * SAMPLE_RATE))
        add_note(samples, start, midi_note, length, level * dynamic, decay,
                 attack=ATTACK / dynamic, touch=touch)

    if not bass_only:
        for bar, numeral in enumerate(PROGRESSION):
            dynamic = bar_levels[bar] * wobble()
            for interval in CHORD:
                strike(bar * bar_samples, chord_roots(tonic, numeral)[0] + interval,
                       bar_samples, CHORD_LEVEL / len(CHORD), dynamic, CHORD_DECAY)

    if bass == "drone":
        if not bass_only:
            for numeral, first, bars in chord_runs():
                add_note(samples, first * bar_samples, chord_roots(tonic, numeral)[1],
                         bars * bar_samples, DRONE_LEVEL, 0.0,
                         attack=DRONE_FADE, release=DRONE_FADE)
    else:
        for bar, beat, beats, midi_note in walking_line(tonic, rng, plain):
            accent = 1.0 if plain else (BEAT_ACCENTS[int(beat)]
                                        * (OFFBEAT_ACCENT if beat % 1 else 1))
            strike(bar * bar_samples + round(beat * beat_samples), midi_note,
                   round(beats * beat_samples), BASS_LEVEL,
                   accent * bar_levels[bar] * wobble(), BASS_DECAY)

    if click_level:
        for bar in range(len(PROGRESSION)):
            for beat in range(BEATS_PER_BAR):
                add_click(samples, bar * bar_samples + round(beat * beat_samples),
                          click_level)
    return samples


def synth_session(tonic, beat_samples, bass, bass_only, click_level, choruses,
                  plain=False):
    """Every chorus of the session, as (opening, variants, order).

    `variants` are differently played choruses, packed (`plain` has just
    the one); `order` says which is played when, never the same one twice
    running. `opening` stands in for the first: it differs only when the
    bass is the sliding drone, which has no beginning of its own, so the
    first time through it fades in.
    """
    rng = random.Random()
    variants = [synth_chorus(tonic, beat_samples, bass, bass_only, click_level, rng, plain)
                for _ in range(1 if plain else min(VARIANTS, choruses))]
    order = []
    for _ in range(choruses):
        choices = [i for i in range(len(variants))
                   if len(variants) == 1 or not order or i != order[-1]]
        order.append(rng.choice(choices))

    if not (bass == "drone" and bass_only):
        variants = [pack_frames(v) for v in variants]
        return variants[order[0]], variants, order
    drone = sliding_drone(tonic, beat_samples)
    fade = int(SAMPLE_RATE * DRONE_FADE)
    first = variants[order[0]]
    opening = pack_frames([s + d * i / fade
                           for i, (s, d) in enumerate(zip(first[:fade], drone))])
    variants = [pack_frames([s + d for s, d in zip(v, drone)]) for v in variants]
    return opening + variants[order[0]][len(opening):], variants, order


def synth_ending(tonic, beat_samples, bass, bass_only, plain=False):
    """The final I: chord and bass root struck together and left to ring.

    With the bass on its own there is no chord, and if that bass is the
    drone it is not struck either — it arrives already sounding and this
    is it dying away.
    """
    chord_root, bass_root = chord_roots(tonic, "I")
    n = round(ENDING_BARS * BEATS_PER_BAR * beat_samples)
    samples = [0.0] * n
    brightness = 0.0 if plain else BRIGHTNESS_DECAY
    if not bass_only:
        for interval in CHORD:
            add_note(samples, 0, chord_root + interval, n,
                     CHORD_LEVEL / len(CHORD), ENDING_DECAY, brightness=brightness)
    if bass == "drone" and bass_only:
        add_note(samples, 0, bass_root, n, SOLO_DRONE_LEVEL, ENDING_DECAY,
                 attack=1.0 / SAMPLE_RATE, brightness=brightness)
    else:
        add_note(samples, 0, bass_root, n, BASS_LEVEL, ENDING_DECAY,
                 brightness=brightness)
    return pack_frames(samples)


def render(path, opening, variants, order, ending):
    """Write the session: the choruses in order, then the ending."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for index, variant in enumerate(order):
            wf.writeframes(opening if index == 0 else variants[variant])
        wf.writeframes(ending)


def progression_table():
    """The twelve bars as numerals, four to a row."""
    width = 6

    def rule(left, joint, right):
        return "  " + left + joint.join(["─" * width] * 4) + right

    lines = [rule("┌", "┬", "┐")]
    for first in range(0, len(PROGRESSION), 4):
        if first:
            lines.append(rule("├", "┼", "┤"))
        lines.append("  │" + "│".join(f" {numeral:<{width - 1}}"
                                      for numeral in PROGRESSION[first:first + 4]) + "│")
    lines.append(rule("└", "┴", "┘"))
    return "\n".join(lines)


USAGE = f"""A standard 12-bar blues to play over. Every setting is a flag:

  --minutes N   how long it runs; filled with as many whole choruses as
                fit, never fewer than one  (default {DEFAULT_MINUTES:g})
  --bpm N       tempo, between {MIN_BPM:g} and {MAX_BPM:g}; a chorus is {len(PROGRESSION) * BEATS_PER_BAR} beats
                (default {DEFAULT_BPM:g})
  --key NOTE    the key: C D E F G A B, each optionally with # or b
                (default {DEFAULT_KEY})
  --bass STYLE  what the bass does:
                walking  the boogie-woogie line, 1 3 5 6 b7 6 5 3 in
                         quarter notes
                drone    holds the root of each chord
                (default {BASS_STYLES[0]})
  --chords      put the chords in over the bass, each bar struck once and
                left to ring. Without them the drone slides from each root
                to the next without stopping, with a very faint click on
                the beats; the walking bass needs none and gets none
                (default: the bass on its own)
  --metronome   add a click on every beat  (default: no click)
  --plain       the machine version: 1 3 5 6 b7 6 5 3 in every bar, every
                note struck alike and dead on the beat, the same chorus
                over and over, and a steady tone  (default: played with
                a human touch — accents, bars that lean, notes that
                wobble a little in level, timing and colour, and the
                odd different bar)
  --help        print this and stop

  python3 play_blues.py --minutes 20 --bpm 80 --key A --chords
"""


GUIDE = (f"flags: --minutes N   --bpm N   --key NOTE   --bass {'|'.join(BASS_STYLES)}   "
         "--chords   --metronome   --plain   --help")


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(minutes=DEFAULT_MINUTES, bpm=DEFAULT_BPM, key=DEFAULT_KEY,
                    bass=BASS_STYLES[0], chords=False, metronome=False,
                    plain=False)
    switches = {"--chords": "chords", "--metronome": "metronome", "--plain": "plain"}
    numeric = {"--minutes": "minutes", "--bpm": "bpm"}
    verbatim = {"--key": "key", "--bass": "bass"}

    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--help":
            print(USAGE, end="")
            return None
        if flag in switches:
            settings[switches[flag]] = True
            continue
        if flag not in numeric and flag not in verbatim:
            hint = "every setting is passed as a named flag"
            if flag.startswith("-"):
                hint = "no such flag"
            return fail(f"{flag!r}: {hint}.")
        if not args:
            return fail(f"{flag} needs a value after it.")
        value = args.pop(0)
        if flag in numeric:
            try:
                settings[numeric[flag]] = float(value)
            except ValueError:
                return fail(f"{flag} needs a number, not {value!r}.")
        else:
            settings[verbatim[flag]] = value

    if not settings["minutes"] > 0 or math.isinf(settings["minutes"]):
        return fail(f"--minutes {settings['minutes']:g} is not a length; "
                    "it needs to be positive.")
    if not MIN_BPM <= settings["bpm"] <= MAX_BPM:
        return fail(f"--bpm {settings['bpm']:g} is out of range "
                    f"({MIN_BPM:g} to {MAX_BPM:g}).")
    if settings["bass"] not in BASS_STYLES:
        return fail(f"--bass {settings['bass']!r} is not one of "
                    f"{' | '.join(BASS_STYLES)}.")

    key = settings["key"].strip().capitalize().replace("s", "#")
    root, accidental = key[:1], key[1:]
    if root not in PITCH_CLASS or accidental not in ("", "#", "b"):
        return fail(f"--key {settings['key']!r} is not a note name.")
    settings["tonic"] = (PITCH_CLASS[root]
                         + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12
    return settings


def main():
    settings = parse_args(sys.argv[1:])
    if settings is None:
        return 2
    minutes, bpm, tonic = settings["minutes"], settings["bpm"], settings["tonic"]
    bass, bass_only = settings["bass"], not settings["chords"]
    metronome, plain = settings["metronome"], settings["plain"]
    faint_click = bass_only and bass == "drone" and not metronome
    click_level = (CLICK_LEVEL if metronome
                   else FAINT_CLICK_LEVEL if faint_click else 0.0)

    beat_samples = SAMPLE_RATE * 60.0 / bpm
    bar_seconds = round(BEATS_PER_BAR * beat_samples) / SAMPLE_RATE
    chorus_seconds = len(PROGRESSION) * bar_seconds
    ending_seconds = ENDING_BARS * bar_seconds
    choruses = max(1, int((minutes * 60 - ending_seconds) / chorus_seconds))
    length = round(choruses * chorus_seconds + ending_seconds)

    tmpdir = tempfile.mkdtemp(prefix="blues_")
    wav_path = os.path.join(tmpdir, "session.wav")
    player = None
    start = time.time()
    try:
        print(GUIDE + "\n")
        print("Rendering...", end="", flush=True)
        opening, variants, order = synth_session(tonic, beat_samples, bass,
                                                 bass_only, click_level, choruses,
                                                 plain)
        render(wav_path, opening, variants, order,
               synth_ending(tonic, beat_samples, bass, bass_only, plain))

        print(f"\r12-bar blues in {NOTE_NAMES[tonic]} — {bpm:g} BPM, {choruses} chorus"
              + ("" if choruses == 1 else "es")
              + f", {length // 60}:{length % 60:02d}, {bass} bass"
              + (" on its own" if bass_only else "")
              + (", plain" if plain else "")
              + (", metronome" if metronome else ", faint click" if faint_click else "")
              + ". Ctrl+C to stop.\n")
        print(progression_table() + "\n")

        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()
        for index in range(choruses * len(PROGRESSION)):
            time.sleep(max(0.0, index * bar_seconds - (time.time() - start)))
            if player.poll() is not None:
                break
            chorus, bar = divmod(index, len(PROGRESSION))
            print(f"\r  chorus {chorus + 1}/{choruses}   bar {bar + 1:<2}   "
                  f"{PROGRESSION[bar]:<2}", end="", flush=True)
        player.wait()
        print()
    except FileNotFoundError:
        print("\nafplay not found — this plays audio on macOS.")
        return
    except KeyboardInterrupt:
        if player is not None:
            player.terminate()
        print("\nStopped.")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
        os.rmdir(tmpdir)

    minutes_played = round((time.time() - start) / 60)
    print(f"Done — {minutes_played} minute" + ("" if minutes_played == 1 else "s") + ".")


if __name__ == "__main__":
    sys.exit(main())
