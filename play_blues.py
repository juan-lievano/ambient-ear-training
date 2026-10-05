#!/usr/bin/env python3
"""A standard 12-bar blues to play over, looping for as long as you ask.

Usage:
    python3 play_blues.py [--minutes N] [--bpm N] [--key NOTE]
                          [--bass walking|drone] [--chords]
                          [--metronome]

    python3 play_blues.py                        # 10 min in C at 100 BPM:
                                                 # a walking bass, nothing else
    python3 play_blues.py --minutes 20 --bpm 80
    python3 play_blues.py --key A --chords       # the chords over the bass
    python3 play_blues.py --bass drone           # no chords, a sliding drone
    python3 play_blues.py --metronome            # with a click on every beat

The form is the plain one, every chord a dominant seventh, with the V in
bar 12 turning each chorus round into the next:

    I    I    I    I
    IV   IV   I    I
    V    IV   I    V

By default the bass is all there is to hear the form by, and it does one
of two things:

  --bass walking   the boogie-woogie line, a quarter note per beat: 1 3 5 6
                   up through one bar and b7 6 5 3 back down through the
                   next wherever a chord lasts two bars, and just the way
                   up on the one-bar chords of the last line. The pattern
                   as taught at studybass.com/lessons/blues-bass/
                   the-boogie-woogie-blues-pattern/ (the default)
  --bass drone     holds the root of the chord, one unbroken note for as
                   long as the chord lasts

--chords puts the chords in over the bass, each bar the chord struck once
and left to ring.

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

import math
import os
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
WALK_UP = (0, 4, 7, 9)                # 1 3 5 6, one per beat
WALK_DOWN = (10, 9, 7, 4)             # b7 6 5 3, the second bar of a chord
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

HARMONICS = ((1, 1.0), (2, 0.4), (3, 0.2), (4, 0.1))
HARMONICS_PEAK = sum(level for _, level in HARMONICS)

PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def midi_to_freq(midi_note):
    return 440.0 * 2 ** ((midi_note - 69) / 12)


def add_note(samples, start, midi_note, length, level, decay,
             attack=0.008, release=0.03):
    """A note mixed in at `start`, dying away over `length` samples.

    The decay is measured against the note's own length rather than in
    seconds, so a bar sounds the same shape at any tempo; a decay of zero
    is a held note. The last few milliseconds are faded so the note ends
    at silence — which is what lets the chorus be laid end to end with
    itself without a step at the join.
    """
    freq = midi_to_freq(midi_note)
    attack *= SAMPLE_RATE
    release *= SAMPLE_RATE
    n = min(length, len(samples) - start)
    for i in range(n):
        env = min(1.0, i / attack, (n - i) / release) * math.exp(-decay * i / n)
        phase = 2 * math.pi * freq * i / SAMPLE_RATE
        samples[start + i] += (level * env / HARMONICS_PEAK
                               * sum(a * math.sin(h * phase) for h, a in HARMONICS))


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


def synth_chorus(tonic, beat_samples, bass, bass_only, click_level):
    """One pass through the form: chords, the bass, and clicks if asked for.

    Returns it twice, as (opening, chorus). They differ only when the bass
    is the sliding drone, which has no beginning of its own: the opening
    is the same chorus with the drone faded in, for the first time through.
    """
    bar_samples = round(BEATS_PER_BAR * beat_samples)
    samples = [0.0] * (len(PROGRESSION) * bar_samples)

    if not bass_only:
        chords = {}
        for numeral in ROOTS:
            chords[numeral] = [0.0] * bar_samples
            for interval in CHORD:
                add_note(chords[numeral], 0, chord_roots(tonic, numeral)[0] + interval,
                         bar_samples, CHORD_LEVEL / len(CHORD), CHORD_DECAY)
        for bar, numeral in enumerate(PROGRESSION):
            samples[bar * bar_samples:(bar + 1) * bar_samples] = chords[numeral]

    for numeral, first, bars in chord_runs():
        bass_root = chord_roots(tonic, numeral)[1]
        if bass == "drone":
            if not bass_only:
                add_note(samples, first * bar_samples, bass_root, bars * bar_samples,
                         DRONE_LEVEL, 0.0, attack=DRONE_FADE, release=DRONE_FADE)
            continue
        for bar in range(bars):
            for beat, interval in enumerate(WALK_DOWN if bar % 2 else WALK_UP):
                add_note(samples,
                         (first + bar) * bar_samples + round(beat * beat_samples),
                         bass_root + interval, round(beat_samples),
                         BASS_LEVEL, BASS_DECAY)

    if click_level:
        for bar in range(len(PROGRESSION)):
            for beat in range(BEATS_PER_BAR):
                add_click(samples, bar * bar_samples + round(beat * beat_samples),
                          click_level)

    if not (bass == "drone" and bass_only):
        chorus = pack_frames(samples)
        return chorus, chorus
    drone = sliding_drone(tonic, beat_samples)
    fade = int(SAMPLE_RATE * DRONE_FADE)
    opening = pack_frames([s + d * i / fade
                           for i, (s, d) in enumerate(zip(samples[:fade], drone))])
    chorus = pack_frames([s + d for s, d in zip(samples, drone)])
    return opening + chorus[len(opening):], chorus


def synth_ending(tonic, beat_samples, bass, bass_only):
    """The final I: chord and bass root struck together and left to ring.

    With the bass on its own there is no chord, and if that bass is the
    drone it is not struck either — it arrives already sounding and this
    is it dying away.
    """
    chord_root, bass_root = chord_roots(tonic, "I")
    n = round(ENDING_BARS * BEATS_PER_BAR * beat_samples)
    samples = [0.0] * n
    if not bass_only:
        for interval in CHORD:
            add_note(samples, 0, chord_root + interval, n,
                     CHORD_LEVEL / len(CHORD), ENDING_DECAY)
    if bass == "drone" and bass_only:
        add_note(samples, 0, bass_root, n, SOLO_DRONE_LEVEL, ENDING_DECAY,
                 attack=1.0 / SAMPLE_RATE)
    else:
        add_note(samples, 0, bass_root, n, BASS_LEVEL, ENDING_DECAY)
    return pack_frames(samples)


def render(path, opening, chorus, ending, choruses):
    """Write the session: the chorus over and over, then the ending."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for index in range(choruses):
            wf.writeframes(chorus if index else opening)
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
  --help        print this and stop

  python3 play_blues.py --minutes 20 --bpm 80 --key A --chords
"""


GUIDE = (f"flags: --minutes N   --bpm N   --key NOTE   --bass {'|'.join(BASS_STYLES)}   "
         "--chords   --metronome   --help")


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(minutes=DEFAULT_MINUTES, bpm=DEFAULT_BPM, key=DEFAULT_KEY,
                    bass=BASS_STYLES[0], chords=False, metronome=False)
    switches = {"--chords": "chords", "--metronome": "metronome"}
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
    metronome = settings["metronome"]
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
        opening, chorus = synth_chorus(tonic, beat_samples, bass, bass_only,
                                       click_level)
        render(wav_path, opening, chorus,
               synth_ending(tonic, beat_samples, bass, bass_only), choruses)

        print(f"\r12-bar blues in {NOTE_NAMES[tonic]} — {bpm:g} BPM, {choruses} chorus"
              + ("" if choruses == 1 else "es")
              + f", {length // 60}:{length % 60:02d}, {bass} bass"
              + (" on its own" if bass_only else "")
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
