#!/usr/bin/env python3
"""Metronome: a click on every beat, optionally over a drone on C.

Usage:
    python3 play_metronome.py [--minutes N] [--bpm N] [--drone]
                              [--drone-harmonics N]

    python3 play_metronome.py                       # 10 min at 80 BPM
    python3 play_metronome.py --minutes 5 --bpm 120
    python3 play_metronome.py --drone               # the same, over a C drone
    python3 play_metronome.py --drone-harmonics 16  # a brighter drone than 5

The whole session is rendered as one WAV up front, then played with
macOS's afplay, so the timing is in the file rather than in this process:
each click is placed at its own beat's sample, rounded from the start of
the session, so the rounding never adds up and the last click is as on
time as the first. Standard library only.

--drone puts the first five harmonics of C2 underneath — C2 C3 G3 C4 E4.
Harmonic h sits at 1/h of the fundamental's level on average,
and each one swells above that and sinks below it on its own very slow
sine, one to three minutes a cycle with the speed and phase drawn fresh
every run, so the tone keeps shifting its weight without the level of any
harmonic ever being wrong on average.

--drone-harmonics changes how many there are, and turns the drone on by
itself. 6 to 8 are G4, a flat Bb4 and C5, which leans it towards C7; past
the eighth they stop spelling a chord and start filling in a scale — 9 to
16 are D E F# G Ab Bb B C, the 11th and 13th a good way off any piano
key — each one quieter than the last, so what more of them adds
is brightness and, as they breathe, single overtones surfacing and going
under again.
"""

import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
import wave
from operator import mul

SAMPLE_RATE = 22050

DEFAULT_MINUTES = 10.0
DEFAULT_BPM = 80
MIN_BPM = 20
MAX_BPM = 400

CLICK_FREQ = 1000.0      # Hz
CLICK_SECONDS = 0.03
CLICK_DECAY = 0.005      # seconds for the click to fall to 1/e
CLICK_LEVEL = 0.7

DRONE_FREQ = 65.41       # C2
DEFAULT_DRONE_HARMONICS = 5   # harmonic h averages 1/h of the fundamental's level
MAX_DRONE_HARMONICS = 64      # C8, and about as slow as the render should get
DRONE_LEVEL = 0.3        # all of them together, each at its average
DRONE_BREATH = 0.7       # how far a harmonic swells and sinks, as a share of its average
DRONE_MIN_PERIOD = 60.0  # seconds per breath, drawn per harmonic every run
DRONE_MAX_PERIOD = 180.0
DRONE_FADE = 4.0         # seconds to fade the drone in and out
DRONE_CYCLES = 16        # cycles of C2 between re-reading the breaths, about 0.25s


def build_click():
    """One click: a sine that starts at full level and dies away."""
    return [int(32767 * CLICK_LEVEL * math.exp(-i / (SAMPLE_RATE * CLICK_DECAY))
                * math.sin(2 * math.pi * CLICK_FREQ * i / SAMPLE_RATE))
            for i in range(int(SAMPLE_RATE * CLICK_SECONDS))]


def build_drone(count):
    """One cycle of every harmonic, and how each one breathes this run.

    Rounding C2's period to a whole number of samples (under a cent) is
    what lets one cycle be tabulated and repeated. The table is turned on
    its side — one row per sample, holding every harmonic at that sample —
    because that is the order drone_block mixes it in.
    """
    period = round(SAMPLE_RATE / DRONE_FREQ)
    harmonics = range(1, count + 1)
    rows = [[math.sin(2 * math.pi * h * i / period) for h in harmonics]
            for i in range(period)]
    total = sum(1.0 / h for h in harmonics)
    breaths = [(DRONE_LEVEL / (h * total),
                random.uniform(DRONE_MIN_PERIOD, DRONE_MAX_PERIOD),
                random.uniform(0, 2 * math.pi)) for h in harmonics]
    return rows, breaths


def drone_block(rows, breaths, pos, total_samples):
    """DRONE_CYCLES cycles of the drone as it stands at `pos`, as frames.

    The breaths move over minutes, so reading them once per block is often
    enough, and every block starts where all the harmonics cross zero, so
    the level can change from one block to the next without a step in the
    sound. The session's fade in and out is folded into the same reading;
    it reaches zero on the last block, which is the one that may be cut
    short.
    """
    block = len(rows) * DRONE_CYCLES
    fade = min(1.0, (pos + block) / (SAMPLE_RATE * DRONE_FADE),
               (total_samples - pos - block) / (SAMPLE_RATE * DRONE_FADE))
    gain = 32767 * (0.5 - 0.5 * math.cos(math.pi * max(0.0, fade)))
    amps = [gain * level * (1.0 + DRONE_BREATH
                            * math.sin(2 * math.pi * pos / SAMPLE_RATE / period + phase))
            for level, period, phase in breaths]
    cycle = [int(sum(map(mul, amps, row))) for row in rows]
    return struct.pack(f"<{len(cycle)}h", *cycle) * DRONE_CYCLES


def mix_clicks(frames, pos, click, samples_per_beat):
    """Add the clicks that fall in the block at `pos`; returns how many start in it."""
    end = pos + len(frames) // 2
    beat = max(0, int((pos - len(click)) / samples_per_beat))
    started = 0
    while (start := round(beat * samples_per_beat)) < end:
        lo, hi = max(start, pos), min(start + len(click), end)
        if lo < hi:
            under = struct.unpack_from(f"<{hi - lo}h", frames, 2 * (lo - pos))
            mixed = [max(-32767, min(32767, u + c))
                     for u, c in zip(under, click[lo - start:hi - start])]
            struct.pack_into(f"<{hi - lo}h", frames, 2 * (lo - pos), *mixed)
        started += start >= pos
        beat += 1
    return started


def render(path, bpm, total_samples, drone, harmonics=DEFAULT_DRONE_HARMONICS):
    """Write the session block by block: the drone or silence, clicks on top."""
    click = build_click()
    samples_per_beat = SAMPLE_RATE * 60.0 / bpm
    rows, breaths = build_drone(harmonics)
    block = len(rows) * DRONE_CYCLES
    silence = bytes(2 * block)

    clicks = 0
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for pos in range(0, total_samples, block):
            bed = drone_block(rows, breaths, pos, total_samples) if drone else silence
            frames = bytearray(bed[:2 * min(block, total_samples - pos)])
            clicks += mix_clicks(frames, pos, click, samples_per_beat)
            wf.writeframes(frames)
    return clicks


USAGE = f"""Metronome: a click on every beat. Every setting is a flag:

  --minutes N   how long it runs  (default {DEFAULT_MINUTES:g})
  --bpm N       clicks per minute, between {MIN_BPM:g} and {MAX_BPM:g}  (default {DEFAULT_BPM:g})
  --drone       put a drone on C underneath: the first {DEFAULT_DRONE_HARMONICS} harmonics of C2,
                each breathing in and out at its own speed, {DRONE_MIN_PERIOD / 60:g}-{DRONE_MAX_PERIOD / 60:g} minutes
                a cycle  (default: clicks alone)
  --drone-harmonics N
                how many harmonics the drone has, 1 to {MAX_DRONE_HARMONICS}; more is
                brighter. Turns the drone on by itself  (default {DEFAULT_DRONE_HARMONICS})
  --help        print this and stop

  python3 play_metronome.py --minutes 5 --bpm 120 --drone
"""


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(minutes=DEFAULT_MINUTES, bpm=DEFAULT_BPM, drone=False,
                    harmonics=DEFAULT_DRONE_HARMONICS)
    numeric = {"--minutes": "minutes", "--bpm": "bpm",
               "--drone-harmonics": "harmonics"}

    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--help":
            print(USAGE, end="")
            return None
        if flag == "--drone":
            settings["drone"] = True
            continue
        if flag not in numeric:
            hint = "every setting is passed as a named flag"
            if flag.startswith("-"):
                hint = "no such flag"
            return fail(f"{flag!r}: {hint}.")
        if not args:
            return fail(f"{flag} needs a value after it.")
        value = args.pop(0)
        try:
            settings[numeric[flag]] = float(value)
        except ValueError:
            return fail(f"{flag} needs a number, not {value!r}.")
        if flag == "--drone-harmonics":
            settings["drone"] = True

    if not settings["minutes"] > 0 or math.isinf(settings["minutes"]):
        return fail(f"--minutes {settings['minutes']:g} is not a length; "
                    "it needs to be positive.")
    if not MIN_BPM <= settings["bpm"] <= MAX_BPM:
        return fail(f"--bpm {settings['bpm']:g} is out of range "
                    f"({MIN_BPM:g} to {MAX_BPM:g}).")
    harmonics = settings["harmonics"]
    if harmonics != int(harmonics) or not 1 <= harmonics <= MAX_DRONE_HARMONICS:
        return fail(f"--drone-harmonics {harmonics:g} is not a whole number "
                    f"between 1 and {MAX_DRONE_HARMONICS}.")
    settings["harmonics"] = int(harmonics)
    return settings


def main():
    settings = parse_args(sys.argv[1:])
    if settings is None:
        return 2
    minutes, bpm, drone = settings["minutes"], settings["bpm"], settings["drone"]
    harmonics = settings["harmonics"]

    tmpdir = tempfile.mkdtemp(prefix="metronome_")
    wav_path = os.path.join(tmpdir, "session.wav")
    player = None
    start = time.time()
    try:
        print("Rendering...", end="", flush=True)
        clicks = render(wav_path, bpm, round(SAMPLE_RATE * minutes * 60), drone,
                        harmonics)
        print(f"\r{bpm:g} BPM for {minutes:g} minute" + ("" if minutes == 1 else "s")
              + f" — {clicks} clicks" + (f" over a drone on C, {harmonics} harmonics" if drone else "")
              + ". Ctrl+C to stop.")
        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()
        player.wait()
    except FileNotFoundError:
        print("afplay not found — this plays audio on macOS.")
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
