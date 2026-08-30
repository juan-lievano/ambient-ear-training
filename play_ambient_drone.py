#!/usr/bin/env python3
"""Ambient: one seventh chord breathing over a drone. Not a drill.

Usage:
    python3 play_ambient_drone.py [--minutes N] [--key G] [--chord NAME]
                                  [--bpm N] [--lowest-octave N]
                                  [--highest-octave N] [--output FILE]

    python3 play_ambient_drone.py                       # 30 min, random chord
    python3 play_ambient_drone.py --minutes 60
    python3 play_ambient_drone.py --key A --chord min7
    python3 play_ambient_drone.py --bpm 10              # even slower breathing
    python3 play_ambient_drone.py --output ambient.wav  # keep the render

One chord for the whole session — maj7, min7 or dom7 — on a root that is
picked at random every run unless --key and --chord say otherwise.

Underneath, a drone on the root: the core is the root in octave 2 and its
octave above, and around it the sub-octave and the fifths fade in and out
on their own very slow LFOs (35-95 second cycles, random phase per run),
so the bed keeps moving instead of sitting there as one static tone.

Over that, the chord tones come and go one at a time. One of 3 5 7 is drawn
uniformly from the bag and given a random octave; if that exact tone is
already sounding it goes to one of its free octaves instead. The root is
not in the bag — the drone is already three octaves of it — so what the pad
adds is only ever the part of the chord the drone does not have. The gap
before the next tone is drawn too, 2 to 6 beats, so it averages four
without ever settling into a period. Each tone lasts 18 to 36 beats, and
spends a third of that fading in, a third held at full level, and a third
fading out — so several are always overlapping, each at a different point
in its own arc, and nothing ever starts or stops. Beats are only a unit of
time here: at the default 16 BPM one is 3.75 seconds, so a tone enters
every 8 to 23 seconds and lasts between one and two and a quarter minutes.
Nothing is struck, so there is no pulse to hear.

How loud a tone is depends on its own pitch rather than on which octave
it was filed under: level falls as (lowest / f) ** 1.4, roughly 8 dB per
octave, so the 3rd sits a little under the root beneath it, the octave up
comes in at 38% and the 7th two octaves up at 6%. Since the ear hears the
top of that range far more keenly than the bottom, a fall that steep is
what it takes for high tones to actually sound softer instead of merely
measuring softer. They lose their harmonics on the way up too, thinning
towards a plain sine, so nothing up there ever pushes to the front.

When 3, 5 and 7 are set to stand at full level at the same moment — the
whole seventh at its loudest, since the drone is holding the root — the bag
closes: no new tone enters until every one of them has faded away and only
the drone is left. It is not enough for the three to merely overlap; they
have to meet in their middle thirds, which is a good deal rarer and is the
one moment in the piece where you hear the chord whole. So it keeps filling
up to that and emptying back out to the root, about every four minutes, at
a rate nobody chose. Three and a half tones sound at once on average, five
or more of them a third of the time, and all nine the register has room for
has happened.

The whole session is rendered as one WAV up front, then played with
macOS's afplay. Standard library only.
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
from collections import deque, namedtuple
from itertools import cycle, islice, product

SAMPLE_RATE = 22050

DEFAULT_MINUTES = 30.0
DEFAULT_BPM = 16
MIN_BPM = 6
MAX_BPM = 60

SPAWN_BEATS = (2, 3, 4, 5, 6)         # wait before the next tone, drawn each time
TONE_BEATS = tuple(range(18, 37))     # how long a tone lasts, drawn uniformly
FADE_SHARE = 1.0 / 3.0                # of that, spent fading in — and again out
INTRO_BEATS = 2                       # drone alone before the first tone
OUTRO_BEATS = 4                       # drone alone after the last one

DRONE_LEVEL = 0.26
VOICE_LEVEL = 0.24       # the lowest tone in play; everything higher is softer
VOICE_TILT = 1.4         # how steeply level falls with pitch — see tone_levels
TONE_DAMPING = 1000.0    # Hz above which a harmonic is rolled off
FADE_SECONDS = 8.0       # fade the whole session in and out
LFO_MIN_PERIOD = 35.0    # the drone's partials breathe once per 35-95 seconds
LFO_MAX_PERIOD = 95.0

DEFAULT_LOWEST_OCTAVE = 3
DEFAULT_HIGHEST_OCTAVE = 5
MIN_OCTAVE = 2
MAX_OCTAVE = 7

CHUNK = 512              # samples mixed at a time; tone envelopes step here
DRONE_REFRESH = 4096     # and the drone's LFO amplitudes are re-read here
MIN_TABLE_SAMPLES = 2048 # shortest wave table, so rounding stays under a cent

PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# The three sevenths, as semitones above the root and as the names printed
# for them, degree by degree.
CHORDS = {
    "maj7": dict(intervals={1: 0, 3: 4, 5: 7, 7: 11},
                 labels={1: "1", 3: "3", 5: "5", 7: "7"}, name="major 7"),
    "min7": dict(intervals={1: 0, 3: 3, 5: 7, 7: 10},
                 labels={1: "1", 3: "b3", 5: "5", 7: "b7"}, name="minor 7"),
    "dom7": dict(intervals={1: 0, 3: 4, 5: 7, 7: 10},
                 labels={1: "1", 3: "3", 5: "5", 7: "b7"}, name="dominant 7"),
}
CHORD_ALIASES = {"major7": "maj7", "ma7": "maj7", "maj": "maj7", "m7": "min7",
                 "minor7": "min7", "min": "min7", "-7": "min7", "7": "dom7",
                 "dominant7": "dom7", "dom": "dom7"}
# What the pad may draw. The root is not in the bag: the drone is already
# three octaves of it, so a pad root would only thicken what is there. The
# 3rd and the 7th are what make the chord the chord, and the 5th is in the
# drone only low down and quietly, so up in the pad's register it still
# counts as something arriving.
DEGREES = (3, 5, 7)

# Drone partials as harmonics of the root one octave below the drone core:
# (harmonic, level, floor). Harmonics 2 and 4 are the core the piece sits on,
# so they only dip to their floor; the rest can fade away completely.
PARTIALS = (
    (1, 0.35, 0.10),     # sub-octave       G1
    (2, 1.00, 0.75),     # core             G2
    (3, 0.22, 0.00),     # fifth            D3
    (4, 0.45, 0.60),     # core octave up   G3
    (6, 0.10, 0.00),     # fifth            D4
)

Voice = namedtuple("Voice", "beat beats degree octave")


def set_tempo(bpm):
    """Fix the tempo for this run: everything else is counted in beats."""
    global BPM, BEAT_SECONDS
    BPM = bpm
    BEAT_SECONDS = 60.0 / bpm


set_tempo(DEFAULT_BPM)


def midi_to_freq(midi_note):
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


# Harmonics of the pad tone: octaves and fifths only. A 5th harmonic would
# put a major third on top of every tone, which fights the b3 of a minor
# seventh, so it is left out.
HARMONICS = ((1, 1.00), (2, 0.30), (3, 0.12), (4, 0.06))


def pad_partials(freq):
    """This tone's harmonics, rolled off the higher up the keyboard it sits.

    A partial at TONE_DAMPING Hz comes out at half strength and it falls away
    faster above that, so a low tone keeps its warmth while a high one thins
    towards a plain sine — which is what keeps the top octaves from ever
    pushing to the front of the mix.
    """
    out = [(h, level / (1.0 + (h * freq / TONE_DAMPING) ** 2))
           for h, level in HARMONICS]
    total = sum(level for _, level in out)
    return [(h, level / total) for h, level in out]


def wave_table(freq):
    """Whole cycles of the pad tone, close enough to freq to loop seamlessly.

    A table has to be a whole number of samples long, which at pad range would
    put a single cycle several cents off. Holding enough cycles to fill at
    least MIN_TABLE_SAMPLES spreads that rounding out to under a cent.
    """
    partials = pad_partials(freq)
    cycles = max(1, math.ceil(MIN_TABLE_SAMPLES * freq / SAMPLE_RATE))
    n = max(2, round(cycles * SAMPLE_RATE / freq))
    return [sum(level * math.sin(2 * math.pi * h * cycles * i / n)
                for h, level in partials) for i in range(n)]


def tone_levels(freqs):
    """How loud each tone is, from its pitch alone.

    Amplitude falls as a power of frequency, measured from the lowest tone
    the run can produce — the root of the bottom octave, which is the one
    that keeps VOICE_LEVEL. A tilt of 1 would be the familiar 6 dB per
    octave, which only cancels out how much more keenly the ear hears the
    top of this range; going steeper than that is what actually makes the
    high tones soft, and it applies within an octave as well as across
    them, so the 7th of a chord is always softer than its root.
    """
    anchor = min(freqs.values())
    return {key: VOICE_LEVEL * (anchor / freq) ** VOICE_TILT
            for key, freq in freqs.items()}


def fade_shape(frac):
    """A tone's level at `frac` of the way through it.

    In over the first third, held at full through the middle third, out over
    the last third. The fades are raised cosines, so a tone leaves silence
    and returns to it without a corner anywhere in the shape.
    """
    if frac <= 0.0 or frac >= 1.0:
        return 0.0
    rise = min(frac, 1.0 - frac) / FADE_SHARE
    return 1.0 if rise >= 1.0 else 0.5 - 0.5 * math.cos(math.pi * rise)


def sustain_window(voice):
    """When a tone is at full level: its middle third, in beats."""
    return (voice.beat + FADE_SHARE * voice.beats,
            voice.beat + (1.0 - FADE_SHARE) * voice.beats)


def chord_lands(voices, after):
    """Is the whole chord going to stand at full level at some moment?

    Not just sounding together — sounding together at the top of the shape,
    every degree inside its own middle third at the same time. Because the
    tones are already scheduled, this can be asked the moment the last of
    them is drawn, which is when the bag closes: what follows is the chord
    arriving in full and then emptying out, with nothing new laid over it.
    """
    windows = {degree: [sustain_window(v) for v in voices
                        if v.degree == degree and sustain_window(v)[1] >= after]
               for degree in DEGREES}
    if not all(windows.values()):
        return False
    for combination in product(*(windows[degree] for degree in DEGREES)):
        if max([after] + [lo for lo, _ in combination]) <= min(hi for _, hi in combination):
            return True
    return False


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


def drone_cycle(tables, lfos, period, pos, scale):
    """The drone from `pos` on, as an endless stream of samples.

    Every partial is a harmonic of the same fundamental, so once their
    amplitudes are read they collapse into a single one-cycle table that just
    repeats. The LFOs move over tens of seconds, so re-reading them every
    DRONE_REFRESH samples (about 0.2s) is often enough; rotating the table to
    where the session's phase actually is keeps the drone continuous across
    each re-read.
    """
    amps = []
    for level, floor, lfo_period, phase in lfos:
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * (pos / SAMPLE_RATE) / lfo_period + phase)
        amps.append(scale * level * (floor + (1.0 - floor) * breath))
    combined = [sum(a * t[i] for a, t in zip(amps, tables)) for i in range(period)]
    rotation = pos % period
    return cycle(combined[rotation:] + combined[:rotation])


def voice_chunks(samples, table, level):
    """A tone as a stream of CHUNK-sized blocks, its fade already applied.

    The envelope is re-read once per block rather than once per sample. A
    block is 23ms and the shortest fade is several seconds long, so the steps
    between blocks are a small fraction of a percent of the level.
    """
    wave_iter = cycle(table)
    done = 0
    while done < samples:
        n = min(CHUNK, samples - done)
        env = level * fade_shape((done + n / 2) / samples)
        yield [env * s for s in islice(wave_iter, n)]
        done += n


def sounding_now(voices, beat, labels, levels):
    """What is sounding at `beat`, low to high, and how loud each one is.

    The percentage is of the loudest a tone ever gets — the lowest one at full
    level — so it carries both where a tone has reached in its fade and how
    much the pitch tilt has already taken off it. The mark after it is which
    way the tone is going: ^ rising, = held at full, v falling.
    """
    parts = []
    live = [v for v in voices if v.beat <= beat < v.beat + v.beats]
    for voice in sorted(live, key=lambda v: (v.octave, v.degree)):
        frac = (beat - voice.beat) / voice.beats
        share = 100 * levels[voice.degree, voice.octave] * fade_shape(frac) / VOICE_LEVEL
        going = "^" if frac < FADE_SHARE else "v" if frac > 1 - FADE_SHARE else "="
        parts.append(f"{labels[voice.degree]:<2} oct {voice.octave} {share:3.0f}%{going}")
    return "   ".join(parts)


def closing_tones(voices):
    """Which tones are the ones that complete the chord and close the bag.

    The same question plan_voices asked as it drew them, asked again of the
    finished plan so the log can mark them.
    """
    return {i for i, voice in enumerate(voices)
            if chord_lands(voices[:i + 1], voice.beat)}


def chunk_align(samples):
    """Rounded to a whole number of mixing blocks — 23ms, inaudible here."""
    return max(CHUNK, round(samples / CHUNK) * CHUNK)


def voice_span(voice):
    """Where a tone starts and how long it lasts, in samples."""
    start = chunk_align(SAMPLE_RATE * voice.beat * BEAT_SECONDS)
    return start, chunk_align(SAMPLE_RATE * voice.beats * BEAT_SECONDS)


def master_gain(pos, total_samples):
    """The session's own fade in and out, as a level for the sample at `pos`."""
    fade = SAMPLE_RATE * FADE_SECONDS
    g = min(1.0, pos / fade, (total_samples - pos) / fade)
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, g))


def render(path, voices, tables, lfos, period, total_samples, freqs, levels):
    """Mix the session block by block, straight into the WAV."""
    scale = DRONE_LEVEL / sum(level for _, level, _ in PARTIALS)
    tone_tables = {key: wave_table(freq) for key, freq in freqs.items()}
    pending = deque(sorted(voices, key=lambda v: voice_span(v)[0]))
    sounding, drone = [], None
    fade = int(SAMPLE_RATE * FADE_SECONDS)
    done = 0

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)

        for pos in range(0, total_samples, CHUNK):
            if pos % DRONE_REFRESH == 0:
                drone = drone_cycle(tables, lfos, period, pos, scale)
            mix = list(islice(drone, CHUNK))

            while pending and voice_span(pending[0])[0] <= pos:
                voice = pending.popleft()
                key = (voice.degree, voice.octave)
                sounding.append(voice_chunks(voice_span(voice)[1],
                                             tone_tables[key], levels[key]))
            still = []
            for tone in sounding:
                block = next(tone, None)
                if block is not None:
                    mix = [m + s for m, s in zip(mix, block)]
                    still.append(tone)
            sounding = still

            if pos < fade or pos + CHUNK > total_samples - fade:
                mix = [s * master_gain(pos + i, total_samples)
                       for i, s in enumerate(mix)]
            frames = [max(-32767, min(32767, int(32767 * s))) for s in mix]
            wf.writeframes(struct.pack(f"<{len(frames)}h", *frames))

            if pos // CHUNK % 200 == 0:
                print(f"\rRendering... {100 * pos // total_samples}%",
                      end="", flush=True)
    print("\rRendering... done")


def plan_voices(total_beats, octaves):
    """Which tone enters when, in which octave, and for how long.

    One degree is drawn uniformly from the bag and given a random octave; if
    that exact tone is already sounding it takes a free octave instead, and if
    it has none it sits this turn out. The wait until the next draw is itself
    drawn from SPAWN_BEATS, so the tones never settle into a period. As soon
    as the tones on the books are going to put the whole chord at full level
    at one moment, the bag closes: nothing new enters until every one of them
    has faded away.

    A tone only takes a length that still leaves OUTRO_BEATS of drone after
    it, so the session is exactly as long as it was asked to be rather than
    running on past the end to let a last tone finish.
    """
    voices, resting, beat = [], False, INTRO_BEATS
    while beat + TONE_BEATS[0] + OUTRO_BEATS <= total_beats:
        live = [v for v in voices if v.beat + v.beats > beat]
        if resting and live:
            beat += random.choice(SPAWN_BEATS)
            continue
        resting = False

        degree = random.choice(DEGREES)
        free = [o for o in octaves
                if o not in {v.octave for v in live if v.degree == degree}]
        if free:
            fits = [b for b in TONE_BEATS if beat + b + OUTRO_BEATS <= total_beats]
            voices.append(Voice(beat, random.choice(fits),
                                degree, random.choice(free)))
            resting = chord_lands(voices, beat)
        beat += random.choice(SPAWN_BEATS)
    return voices


USAGE = f"""Ambient: one seventh chord breathing over a drone. Every setting is a flag:

  --minutes N          how long the session lasts; shorter than a tone and
                       it is drone alone  (default {DEFAULT_MINUTES:g})
  --key NOTE           the root the drone sits on: C D E F G A B, each
                       optionally with # or b  (default: random each run)
  --chord NAME         which seventh its 3rd, 5th and 7th come from:
                       {" | ".join(CHORDS)}
                       (default: random each run)
  --bpm N              how fast tones come and go, between {MIN_BPM:g} and {MAX_BPM:g}. One enters
                       every {SPAWN_BEATS[0]}-{SPAWN_BEATS[-1]} beats and lasts {TONE_BEATS[0]}-{TONE_BEATS[-1]} of them, a third of that
                       fading in, a third at full level, a third fading out;
                       at the default {DEFAULT_BPM:g} that is a tone every {SPAWN_BEATS[0] * 60 / DEFAULT_BPM:.0f}-{SPAWN_BEATS[-1] * 60 / DEFAULT_BPM:.0f} seconds,
                       each lasting {TONE_BEATS[0] / DEFAULT_BPM:.1f}-{TONE_BEATS[-1] / DEFAULT_BPM:.1f} minutes  (default {DEFAULT_BPM:g})
  --lowest-octave N    lowest octave a chord tone can take, {MIN_OCTAVE} to {MAX_OCTAVE}
                       (default {DEFAULT_LOWEST_OCTAVE})
  --highest-octave N   and the highest  (default {DEFAULT_HIGHEST_OCTAVE})
  --output FILE        keep the rendered WAV at FILE instead of a temp file
  --help               print this and stop

  python3 play_ambient_drone.py --minutes 60 --key A --chord min7
"""


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(minutes=DEFAULT_MINUTES, key=None, chord=None,
                    bpm=DEFAULT_BPM, lowest=DEFAULT_LOWEST_OCTAVE,
                    highest=DEFAULT_HIGHEST_OCTAVE, output=None)
    numeric = {"--minutes": "minutes", "--bpm": "bpm",
               "--lowest-octave": "lowest", "--highest-octave": "highest"}
    verbatim = {"--key": "key", "--chord": "chord", "--output": "output"}

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

    for flag, field in (("--lowest-octave", "lowest"), ("--highest-octave", "highest")):
        octave = settings[field]
        if octave != int(octave) or not MIN_OCTAVE <= octave <= MAX_OCTAVE:
            return fail(f"{flag} {octave:g} is not a whole octave between "
                        f"{MIN_OCTAVE} and {MAX_OCTAVE}.")
        settings[field] = int(octave)
    if settings["lowest"] > settings["highest"]:
        return fail(f"--lowest-octave {settings['lowest']} is above "
                    f"--highest-octave {settings['highest']}.")

    if settings["chord"] is None:
        settings["chord"] = random.choice(sorted(CHORDS))
    else:
        name = settings["chord"].strip().lower().replace(" ", "").replace("_", "")
        name = CHORD_ALIASES.get(name, name)
        if name not in CHORDS:
            return fail(f"--chord {settings['chord']!r} is not a seventh I know.")
        settings["chord"] = name

    if settings["key"] is None:
        settings["pitch_class"] = random.randrange(12)
        return settings
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
    minutes, chord, out_path = settings["minutes"], settings["chord"], settings["output"]
    pitch_class = settings["pitch_class"]
    octaves = list(range(settings["lowest"], settings["highest"] + 1))
    set_tempo(settings["bpm"])

    # The drone's fundamental is the root in octave 1; rounding its period to a
    # whole number of samples (under two cents) lets one cycle of each partial
    # be tabulated once and reused for the whole session.
    period = max(2, round(SAMPLE_RATE / midi_to_freq(24 + pitch_class)))
    intervals = CHORDS[chord]["intervals"]
    labels = CHORDS[chord]["labels"]
    freqs = {(degree, octave):
             midi_to_freq(12 * (octave + 1) + pitch_class + intervals[degree])
             for degree in DEGREES for octave in octaves}
    levels = tone_levels(freqs)

    total_beats = minutes * 60 / BEAT_SECONDS
    voices = plan_voices(total_beats, octaves)
    if not voices:
        print(f"--minutes {minutes:g} at {BPM:g} BPM leaves no room for even the "
              f"shortest tone ({TONE_BEATS[0] * BEAT_SECONDS / 60:.1f} minutes "
              "of fade), so this one is the drone alone.\n")
    ends = [sum(voice_span(v)) for v in voices]
    total_samples = chunk_align(max([SAMPLE_RATE * total_beats * BEAT_SECONDS]
                                    + [e + SAMPLE_RATE * OUTRO_BEATS * BEAT_SECONDS
                                       for e in ends]))
    tables, lfos = build_drone(period)

    root = NOTE_NAMES[pitch_class]
    spelling = " ".join(labels[d] for d in DEGREES)
    length = round(total_samples / SAMPLE_RATE / 60)
    length = f"{length} minute" + ("" if length == 1 else "s")
    tmpdir = None
    if out_path is None:
        tmpdir = tempfile.mkdtemp(prefix="ambient_")
        wav_path = os.path.join(tmpdir, "session.wav")
    else:
        wav_path = out_path

    player = None
    start = time.time()
    try:
        render(wav_path, voices, tables, lfos, period, total_samples, freqs, levels)

        print(f"""
{root} {CHORDS[chord]["name"]} — {length} at {BPM:g} BPM. Ctrl+C to stop.

A drone on {root}2/{root}3, its sub-octave and fifths breathing underneath.
Over it, {spelling} in octaves {octaves[0]}-{octaves[-1]} — the root stays the drone's — a tone every
{SPAWN_BEATS[0] * BEAT_SECONDS:.0f}-{SPAWN_BEATS[-1] * BEAT_SECONDS:.0f} seconds, each lasting {TONE_BEATS[0] * BEAT_SECONDS / 60:.1f}-{TONE_BEATS[-1] * BEAT_SECONDS / 60:.1f} minutes: a third of that
fading in, a third at full level, a third fading out. When all {len(DEGREES)} stand
at full level together they clear back to the drone before anything
new comes in.

Each tone is logged as it enters, and under it what is sounding right
then: the tone, its octave, and how loud it is as a share of the loudest
a tone ever gets — ^ still rising, = held at full, v falling away.
""")
        if out_path is not None:
            print(f"Saved to {wav_path}\n")

        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()

        closing = closing_tones(voices)
        for index, voice in enumerate(voices):
            if player.poll() is not None:
                break
            time.sleep(max(0.0, voice.beat * BEAT_SECONDS - (time.time() - start)))
            elapsed = time.time() - start
            closes = "   full chord ahead — the bag closes" if index in closing else ""
            print(f"  {int(elapsed) // 60:>3}:{int(elapsed) % 60:02d}   "
                  f"{labels[voice.degree]:<2} oct {voice.octave} in, over "
                  f"{voice.beats * BEAT_SECONDS / 60:.1f} min{closes}")
            print("           " + sounding_now(voices, voice.beat, labels, levels))
        player.wait()
    except FileNotFoundError:
        print("afplay not found — this plays audio on macOS. "
              f"The session is rendered at {wav_path}")
        return
    except KeyboardInterrupt:
        if player is not None:
            player.terminate()
        print("\nStopped.")
    finally:
        if tmpdir is not None:
            if os.path.exists(wav_path):
                os.remove(wav_path)
            os.rmdir(tmpdir)

    minutes_played = round((time.time() - start) / 60)
    print(f"Done — {minutes_played} minute" + ("" if minutes_played == 1 else "s") + ".")


if __name__ == "__main__":
    sys.exit(main())
