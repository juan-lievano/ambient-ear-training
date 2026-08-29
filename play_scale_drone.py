#!/usr/bin/env python3
"""Ear training: scale degrees over a slowly breathing drone.

Usage:
    python3 play_scale_drone.py [--minutes N] [--key G] [--scale NAME]
                                [--bpm N] [--output FILE]

    python3 play_scale_drone.py                        # 15 min in G major, 80 BPM
    python3 play_scale_drone.py --minutes 30 --key D
    python3 play_scale_drone.py --key A --scale natural-minor
    python3 play_scale_drone.py --key A --scale harmonic-minor --bpm 60
    python3 play_scale_drone.py --output drone.wav     # keep the render

A drone on the tonic hums underneath the whole session: the core is the
tonic two octaves below the melody (G2) and its octave (G3), and around
it the sub-octave (G1), the fifths (D3/D4) and the upper octaves (G4/D5)
fade in and out on their own very slow LFOs (35-95 second cycles, random
phase per run), so the pad keeps moving instead of sitting there as one
static chord.

Over that, one random note of the scale per round, at BPM:

    2 beats  the NOTE      — which scale degree is it?
    2 beats  silence       — answer in your head
    2 beats  the ANSWER    — a run from that degree home to the tonic
    2 beats  rest          — the answer rings out over the drone

The run has as many notes as it needs, spread evenly over the two beats,
so a four-note answer comes out in eighths and a two-note one in quarters —
the rhythm of the answer is itself a hint at how far from home you were.

Three scales, chosen with --scale. In major every degree walks home by
step, down from 1-4 and up from 5-7. Both minors follow each degree's
actual pull instead: b6 falls the semitone to 5 and then home, 5 leaps
home rather than crawling down, and the 7th resolves up to the octave —
a whole step in natural minor, a leading-tone semitone in harmonic minor.
That raised 7th is the only note the two minors disagree about, and
hearing which one is sounding is most of the point of having both.

The whole session is rendered as one WAV up front (a few seconds) so the
drone never breaks, then played with macOS's afplay. Standard library
only. The terminal prints each answer as it sounds, so look away if you
don't want the confirmation.
"""

import math
import os
import struct
import random
import subprocess
import sys
import tempfile
import time
import wave

SAMPLE_RATE = 22050

DEFAULT_BPM = 80
MIN_BPM = 20
MAX_BPM = 240
NOTE_BEATS = 2           # the question note
PAUSE_BEATS = 2          # silence to answer in your head
ANSWER_BEATS = 2         # the resolving run
REST_BEATS = 2           # drone alone before the next question
TAIL_BEATS = 1           # how far a note may ring past the answer
INTRO_CYCLES = 2         # drone alone before the first question

DRONE_LEVEL = 0.30
MELODY_LEVEL = 0.38
FADE_SECONDS = 6.0       # fade the whole session in and out
LFO_MIN_PERIOD = 35.0    # slowest partials breathe once per ~95 seconds
LFO_MAX_PERIOD = 95.0

DEFAULT_MINUTES = 15.0
DEFAULT_TONIC = "G"

PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# Each scale is three parallel facts about degrees 1-8: how many semitones
# each one sits above the tonic, what to call it on screen, and the run it
# takes to get home. Major walks home by step in both directions; the minors
# follow each degree's pull, so b6 drops its semitone to 5 and 5 leaps home
# instead of crawling down through four notes.
SCALES = {
    "major": dict(
        steps=(0, 2, 4, 5, 7, 9, 11, 12),
        labels=("1", "2", "3", "4", "5", "6", "7", "8"),
        runs={1: (1,), 2: (2, 1), 3: (3, 2, 1), 4: (4, 3, 2, 1),
              5: (5, 6, 7, 8), 6: (6, 7, 8), 7: (7, 8)},
    ),
    "natural-minor": dict(
        steps=(0, 2, 3, 5, 7, 8, 10, 12),
        labels=("1", "2", "b3", "4", "5", "b6", "b7", "8"),
        runs={1: (1,), 2: (2, 1), 3: (3, 2, 1), 4: (4, 3, 2, 1),
              5: (5, 1), 6: (6, 5, 1), 7: (7, 8)},
    ),
    "harmonic-minor": dict(
        steps=(0, 2, 3, 5, 7, 8, 11, 12),
        labels=("1", "2", "b3", "4", "5", "b6", "7", "8"),
        runs={1: (1,), 2: (2, 1), 3: (3, 2, 1), 4: (4, 3, 2, 1),
              5: (5, 1), 6: (6, 5, 1), 7: (7, 8)},
    ),
}
SCALE_ALIASES = {"minor": "natural-minor", "aeolian": "natural-minor",
                 "ionian": "major", "harmonic": "harmonic-minor"}
DEFAULT_SCALE = "major"

# Degree 1 is the boring one (the answer is "you are already home"), so it
# turns up less often than the rest.
DEGREE_WEIGHTS = {1: 1, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3, 7: 3}

# Drone partials as harmonics of the tonic one octave below the drone core:
# (harmonic, level, floor). Harmonic 2 and 4 are the core the key sits on, so
# they only dip to their floor; everything else can fade away completely.
PARTIALS = (
    (1, 0.50, 0.00),     # sub-octave       G1
    (2, 1.00, 0.72),     # core             G2
    (3, 0.26, 0.00),     # fifth            D3
    (4, 0.55, 0.62),     # core octave up   G3
    (6, 0.17, 0.00),     # fifth            D4
    (8, 0.20, 0.00),     # two octaves up   G4
    (12, 0.07, 0.00),    # shimmer          D5
)

CYCLE_BEATS = NOTE_BEATS + PAUSE_BEATS + ANSWER_BEATS + REST_BEATS


def set_tempo(bpm):
    """Fix the tempo for this run: everything else is counted in beats."""
    global BPM, BEAT_SECONDS, CYCLE_SECONDS, CYCLE_SAMPLES, TAIL_SAMPLES, ANSWER_OFFSET
    BPM = bpm
    BEAT_SECONDS = 60.0 / bpm
    CYCLE_SECONDS = BEAT_SECONDS * CYCLE_BEATS
    CYCLE_SAMPLES = round(SAMPLE_RATE * CYCLE_SECONDS)
    TAIL_SAMPLES = round(SAMPLE_RATE * BEAT_SECONDS * TAIL_BEATS)
    ANSWER_OFFSET = BEAT_SECONDS * (NOTE_BEATS + PAUSE_BEATS)


set_tempo(DEFAULT_BPM)


def midi_to_freq(midi_note):
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def answer_run(scale, degree):
    """The run that takes this degree home, in the active scale."""
    return SCALES[scale]["runs"][degree]


def spell(scale, degrees):
    """Degree numbers as they are printed: 4 3 2 1, or b6 5 1."""
    labels = SCALES[scale]["labels"]
    return " ".join(labels[d - 1] for d in degrees)


def tone(t, f):
    # fundamental plus a couple of quiet harmonics so it sounds less like a test tone
    return (math.sin(2 * math.pi * f * t)
            + 0.3 * math.sin(2 * math.pi * 2 * f * t)
            + 0.1 * math.sin(2 * math.pi * 3 * f * t))


def add_note(samples, start, freq, seconds, level=MELODY_LEVEL):
    """A soft sustained note mixed in at sample offset `start`."""
    n = min(int(SAMPLE_RATE * seconds), len(samples) - start)
    attack = int(SAMPLE_RATE * 0.03)
    release = max(1, int(n * 0.35))
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, i / attack) * (0.55 + 0.45 * math.exp(-2.5 * t))
        remaining = n - i
        if remaining < release:
            env *= 0.5 - 0.5 * math.cos(math.pi * remaining / release)
        samples[start + i] += level * env * tone(t, freq) / 1.4


def build_melody(scale, freqs):
    """One buffer per scale degree: the question note, then its answer run.

    Every round of a given degree sounds identical, so the seven buffers are
    rendered once and then just mixed into the drone. The arrival note rings on
    into the two rest beats that close the round.
    """
    layers = {}
    for degree in DEGREE_WEIGHTS:
        buf = [0.0] * (CYCLE_SAMPLES + TAIL_SAMPLES)
        add_note(buf, 0, freqs[degree], NOTE_BEATS * BEAT_SECONDS + 0.25)

        run = answer_run(scale, degree)
        slot = ANSWER_BEATS * BEAT_SECONDS / len(run)
        for n, step in enumerate(run):
            start = int(SAMPLE_RATE * (ANSWER_OFFSET + n * slot))
            # the arrival note rings on into the tail, the rest are legato
            last = n == len(run) - 1
            seconds = slot + (TAIL_BEATS * BEAT_SECONDS if last else 0.12)
            add_note(buf, start, freqs[step], seconds)
        layers[degree] = buf
    return layers


def build_drone(fundamental_period):
    """One cycle of each partial, plus its LFO period and phase for this run."""
    tables, lfos = [], []
    for harmonic, level, floor in PARTIALS:
        tables.append([math.sin(2 * math.pi * harmonic * i / fundamental_period)
                       for i in range(fundamental_period)])
        lfos.append((level, floor,
                     random.uniform(LFO_MIN_PERIOD, LFO_MAX_PERIOD),
                     random.uniform(0, 2 * math.pi)))
    return tables, lfos


def drone_amps(lfos, seconds, scale):
    """Each partial's amplitude right now — a raised cosine between floor and 1."""
    out = []
    for level, floor, period, phase in lfos:
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * seconds / period + phase)
        out.append(scale * level * (floor + (1.0 - floor) * breath))
    return out


def master_gain(start, total_samples):
    """Per-sample fade for a round, or None when it plays at full level."""
    fade = int(SAMPLE_RATE * FADE_SECONDS)
    if start > fade and start + CYCLE_SAMPLES < total_samples - fade:
        return None
    gains = []
    for i in range(CYCLE_SAMPLES):
        pos = start + i
        g = min(1.0, pos / fade, (total_samples - pos) / fade)
        gains.append(0.5 - 0.5 * math.cos(math.pi * max(0.0, g)))
    return gains


def render(path, plan, tables, lfos, layers, period):
    """Render the session round by round, straight into the WAV."""
    scale = DRONE_LEVEL / sum(level for _, level, _ in PARTIALS)
    total_samples = len(plan) * CYCLE_SAMPLES
    silence = [0.0] * (CYCLE_SAMPLES + TAIL_SAMPLES)
    carry = [0.0] * TAIL_SAMPLES

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)

        for index, degree in enumerate(plan):
            start = index * CYCLE_SAMPLES

            # melody: this round's layer, plus whatever the last one is still ringing
            layer = silence if degree is None else layers[degree]
            melody = ([a + b for a, b in zip(layer, carry)]
                      + layer[TAIL_SAMPLES:CYCLE_SAMPLES])
            carry = layer[CYCLE_SAMPLES:]

            # drone: rotate the partial tables so they line up with where the
            # session's phase actually is, then rebuild one period at a time —
            # the LFOs only need re-reading every period (about 20ms).
            rotation = start % period
            zipped = list(zip(*[t[rotation:] + t[:rotation] for t in tables]))
            drone = []
            pos = 0
            while pos < CYCLE_SAMPLES:
                a1, a2, a3, a4, a6, a8, a12 = drone_amps(
                    lfos, (start + pos) / SAMPLE_RATE, scale)
                # unrolled on purpose: this comprehension runs once per sample
                # of the session and a generic sum() here doubles render time
                one = [a1 * s1 + a2 * s2 + a3 * s3 + a4 * s4
                       + a6 * s6 + a8 * s8 + a12 * s12
                       for s1, s2, s3, s4, s6, s8, s12 in zipped]
                drone.extend(one[:CYCLE_SAMPLES - pos])
                pos += period

            gains = master_gain(start, total_samples)
            if gains is None:
                frames = [max(-32767, min(32767, int(32767 * (m + d))))
                          for m, d in zip(melody, drone)]
            else:
                frames = [max(-32767, min(32767, int(32767 * (m + d) * g)))
                          for m, d, g in zip(melody, drone, gains)]
            wf.writeframes(struct.pack(f"<{len(frames)}h", *frames))

            print(f"\rRendering... round {index + 1}/{len(plan)}", end="", flush=True)
    print()


def pick_degrees(count):
    """Random degrees, never the same one twice in a row."""
    degrees = list(DEGREE_WEIGHTS)
    weights = [DEGREE_WEIGHTS[d] for d in degrees]
    out, previous = [], None
    for _ in range(count):
        degree = random.choices(degrees, weights)[0]
        while degree == previous:
            degree = random.choices(degrees, weights)[0]
        out.append(degree)
        previous = degree
    return out


USAGE = f"""Ear training: scale degrees over a drone. Every setting is a flag:

  --minutes N     how long the whole session lasts, intro included; a
                  slower tempo buys fewer questions, not more minutes
                  (default {DEFAULT_MINUTES:g})
  --key NOTE      the tonic the drone sits on: C D E F G A B, each
                  optionally with # or b  (default {DEFAULT_TONIC})
  --scale NAME    which scale the degrees come from:
                  {" | ".join(SCALES)}
                  (default {DEFAULT_SCALE})
  --bpm N         tempo, between {MIN_BPM:g} and {MAX_BPM:g}; every round is 8 beats
                  (default {DEFAULT_BPM:g})
  --output FILE   keep the rendered WAV at FILE instead of a temp file
  --help          print this and stop

  python3 play_scale_drone.py --minutes 20 --key A --scale natural-minor
"""


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(minutes=DEFAULT_MINUTES, key=DEFAULT_TONIC,
                    scale=DEFAULT_SCALE, bpm=DEFAULT_BPM, output=None)
    numeric = {"--minutes": "minutes", "--bpm": "bpm"}
    verbatim = {"--key": "key", "--scale": "scale", "--output": "output"}

    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--help":
            print(USAGE, end="")
            return None
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

    if settings["minutes"] <= 0:
        return fail(f"--minutes {settings['minutes']:g} is not a length; "
                    "it needs to be positive.")
    if not MIN_BPM <= settings["bpm"] <= MAX_BPM:
        return fail(f"--bpm {settings['bpm']:g} is out of range "
                    f"({MIN_BPM:g} to {MAX_BPM:g}).")

    name = settings["scale"].strip().lower().replace("_", "-").replace(" ", "-")
    name = SCALE_ALIASES.get(name, name)
    if name not in SCALES:
        return fail(f"--scale {settings['scale']!r} is not a scale I know.")
    settings["scale"] = name

    # The key is validated here too, so that every bad argument comes back
    # the same way: the problem, then the full list of what is accepted.
    key = settings["key"].strip().capitalize().replace("s", "#")
    root, accidental = key[:1], key[1:]
    if root not in PITCH_CLASS or accidental not in ("", "#", "b"):
        return fail(f"--key {settings['key']!r} is not a note name.")
    settings["pitch_class"] = (PITCH_CLASS[root]
                               + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12
    return settings


def main():
    settings = parse_args(sys.argv[1:])
    if settings is None:
        return 2
    minutes, scale, bpm, out_path = (settings["minutes"], settings["scale"],
                                     settings["bpm"], settings["output"])
    pitch_class = settings["pitch_class"]
    set_tempo(bpm)

    # The drone's fundamental is the tonic in octave 1; rounding its period to a
    # whole number of samples (under two cents) lets one cycle of each partial be
    # tabulated once and reused for the whole session.
    period = max(2, round(SAMPLE_RATE / midi_to_freq(24 + pitch_class)))
    fundamental = SAMPLE_RATE / period
    # melody sits two octaves above the drone core, i.e. on the 8th harmonic
    steps = SCALES[scale]["steps"]
    freqs = {d: fundamental * 8 * (2 ** (steps[d - 1] / 12.0)) for d in range(1, 9)}

    # The session lasts the minutes asked for, whatever the tempo: the drone
    # intro and the questions are all counted against it, so a slower BPM buys
    # fewer questions rather than a longer session.
    cycles = max(1 + INTRO_CYCLES, round(minutes * 60 / CYCLE_SECONDS))
    degrees = pick_degrees(cycles - INTRO_CYCLES)
    plan = [None] * INTRO_CYCLES + degrees
    rounds = len(degrees)

    tables, lfos = build_drone(period)
    layers = build_melody(scale, freqs)

    name = NOTE_NAMES[pitch_class]
    key_name = f"{name} {scale.replace('-', ' ')}"
    # the run table, three degrees to a line, straight from the active scale
    runs = [f"{spell(scale, [d])} = {spell(scale, answer_run(scale, d))}"
            for d in sorted(SCALES[scale]["runs"])]
    run_table = "\n".join("              " + " | ".join(runs[i:i + 3])
                          for i in range(0, len(runs), 3)).strip()
    tmpdir = None
    if out_path is None:
        tmpdir = tempfile.mkdtemp(prefix="drone_")
        wav_path = os.path.join(tmpdir, "session.wav")
    else:
        wav_path = out_path

    player = None
    count = 0
    start = time.time()
    try:
        render(wav_path, plan, tables, lfos, layers, period)

        print(f"""
{rounds} notes in {key_name} at {BPM:g} BPM, {len(plan) * CYCLE_SECONDS / 60:.0f} minutes — Ctrl+C to stop.

Drone on {name}2/{name}3 with the octaves and fifths breathing in and out.
Each round, over 6 beats:

  beats 1-2   the note — which degree of the scale is it?
  beats 3-4   silence  — answer in your head
  beats 5-6   the run home:
              {run_table}
  beats 7-8   rest — the tonic rings out over the drone

The answer is printed here as it plays — look away if you'd rather not see it.
""")
        if out_path is not None:
            print(f"Saved to {wav_path}\n")

        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()

        target = INTRO_CYCLES * CYCLE_SECONDS   # absolute schedule, so it can't drift
        for degree in degrees:
            if player.poll() is not None:
                break
            count += 1
            print(f"Note #{count} ... ", end="", flush=True)
            time.sleep(max(0.0, target + ANSWER_OFFSET - (time.time() - start)))
            run = spell(scale, answer_run(scale, degree))
            print(f"degree {spell(scale, [degree])}   ->   {run}")
            target += CYCLE_SECONDS
            time.sleep(max(0.0, target - (time.time() - start)))
        player.wait()
    except FileNotFoundError:
        print("afplay not found — this plays audio on macOS. "
              f"The session is rendered at {wav_path}")
        return
    except KeyboardInterrupt:
        if player is not None:
            player.terminate()
        print("\nStopped early.")
    finally:
        if tmpdir is not None:
            if os.path.exists(wav_path):
                os.remove(wav_path)
            os.rmdir(tmpdir)

    elapsed = (time.time() - start) / 60
    print(f"Done — {count} notes over {elapsed:.0f} minutes.")


if __name__ == "__main__":
    sys.exit(main())
