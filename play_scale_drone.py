#!/usr/bin/env python3
"""Ear training: major scale degrees over a slowly breathing drone.

Usage:
    python3 play_scale_drone.py [minutes] [tonic] [-o out.wav]

    python3 play_scale_drone.py            # 15 minutes in G
    python3 play_scale_drone.py 30 D       # 30 minutes in D
    python3 play_scale_drone.py 15 G -o drone.wav   # keep the render

A drone on the tonic hums underneath the whole session: the core is the
tonic two octaves below the melody (G2) and its octave (G3), and around
it the sub-octave (G1), the fifths (D3/D4) and the upper octaves (G4/D5)
fade in and out on their own very slow LFOs (35-95 second cycles, random
phase per run), so the pad keeps moving instead of sitting there as one
static chord.

Over that, one random note of the major scale per round, at BPM:

    2 beats  the NOTE      — which scale degree is it?
    2 beats  silence       — answer in your head
    2 beats  the ANSWER    — a run from that degree to the nearest tonic,
                             DOWN for degrees 1-4, UP for 5-7:
                             4 -> 4 3 2 1     5 -> 5 6 7 8
    2 beats  rest          — the answer rings out over the drone

The run has as many notes as it needs, spread evenly over the two beats,
so degree 5 answers in four eighth notes and degree 7 in two quarters —
the rhythm of the answer is itself a hint at how far from home you were.

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

BPM = 80
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

MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11, 12)   # degrees 1-8 in semitones
PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

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

BEAT_SECONDS = 60.0 / BPM
CYCLE_BEATS = NOTE_BEATS + PAUSE_BEATS + ANSWER_BEATS + REST_BEATS
CYCLE_SECONDS = BEAT_SECONDS * CYCLE_BEATS
CYCLE_SAMPLES = round(SAMPLE_RATE * CYCLE_SECONDS)
TAIL_SAMPLES = round(SAMPLE_RATE * BEAT_SECONDS * TAIL_BEATS)
ANSWER_OFFSET = BEAT_SECONDS * (NOTE_BEATS + PAUSE_BEATS)


def midi_to_freq(midi_note):
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def answer_run(degree):
    """The resolution: down to the tonic from 1-4, up to it from 5-7."""
    if degree == 1:
        return [1]
    if degree <= 4:
        return list(range(degree, 0, -1))
    return list(range(degree, 9))


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


def build_melody(freqs):
    """One buffer per scale degree: the question note, then its answer run.

    Every round of a given degree sounds identical, so the seven buffers are
    rendered once and then just mixed into the drone. The arrival note rings on
    into the two rest beats that close the round.
    """
    layers = {}
    for degree in DEGREE_WEIGHTS:
        buf = [0.0] * (CYCLE_SAMPLES + TAIL_SAMPLES)
        add_note(buf, 0, freqs[degree], NOTE_BEATS * BEAT_SECONDS + 0.25)

        run = answer_run(degree)
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


def main():
    minutes, tonic, out_path = DEFAULT_MINUTES, DEFAULT_TONIC, None
    args = sys.argv[1:]
    while args:
        arg = args.pop(0)
        if arg in ("-o", "--save"):
            if not args:
                print("-o needs a path")
                return
            out_path = args.pop(0)
            continue
        try:
            minutes = float(arg)
        except ValueError:
            tonic = arg

    key = tonic.strip().capitalize().replace("s", "#")
    root, accidental = key[:1], key[1:]
    if root not in PITCH_CLASS or accidental not in ("", "#", "b"):
        print(f"Unknown tonic {tonic!r}. Try one of C D E F G A B, optionally with # or b.")
        return
    pitch_class = (PITCH_CLASS[root] + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12

    # The drone's fundamental is the tonic in octave 1; rounding its period to a
    # whole number of samples (under two cents) lets one cycle of each partial be
    # tabulated once and reused for the whole session.
    period = max(2, round(SAMPLE_RATE / midi_to_freq(24 + pitch_class)))
    fundamental = SAMPLE_RATE / period
    # melody sits two octaves above the drone core, i.e. on the 8th harmonic
    freqs = {d: fundamental * 8 * (2 ** (MAJOR_SCALE[d - 1] / 12.0)) for d in range(1, 9)}

    rounds = max(1, int(minutes * 60 / CYCLE_SECONDS))
    degrees = pick_degrees(rounds)
    plan = [None] * INTRO_CYCLES + degrees

    tables, lfos = build_drone(period)
    layers = build_melody(freqs)

    name = NOTE_NAMES[pitch_class]
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
{rounds} notes in {name} major at {BPM} BPM, {rounds * CYCLE_SECONDS / 60:.0f} minutes — Ctrl+C to stop.

Drone on {name}2/{name}3 with the octaves and fifths breathing in and out.
Each round, over 6 beats:

  beats 1-2   the note — which degree of the scale is it?
  beats 3-4   silence  — answer in your head
  beats 5-6   the run home: 1-4 resolve DOWN, 5-7 resolve UP
              1 = 1 | 2 = 2 1 | 3 = 3 2 1 | 4 = 4 3 2 1
              5 = 5 6 7 8 | 6 = 6 7 8 | 7 = 7 8
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
            run = " ".join(str(d) for d in answer_run(degree))
            print(f"degree {degree}   ->   {run}")
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
    main()
