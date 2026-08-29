#!/usr/bin/env python3
"""Ear training: chord progressions from real songs over a continuous metronome.

Usage:
    python3 play_chords.py [minutes] [genre]

Genres are the .jsonl files in the progressions/ directory next to this
script (one song per line: {"title": ..., "key": ..., "chords": [...]}) —
add a new genre by adding a new file. Omit the genre to mix all of them;
use "random" for random chords with no songs.

Each round: the chord sounds as a block over CHORD_BEATS beats, then
arpeggiated one note per beat (notes ring and stack), then GUESS_BEATS
click-only beats to guess — and then the answer is played as part of
the music, on the two ANSWER_BEATS:

  beat 1 — FUNCTION: two plucked notes, the song's tonic then the chord
           root. The interval you hear is the chord's function (unison =
           I, up a fourth = IV, up a fifth = V, ...). Needs the song's
           "key"; omitted when there isn't one.
  beat 2 — QUALITY: each chord quality has its own drum sound.

The whole session is rendered as one WAV up front (unique chords are
cached, so it only takes a few seconds), then played as a single file so
the metronome never breaks between rounds. Everything is Python standard
library; playback uses macOS's afplay.
"""

import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
import wave

SAMPLE_RATE = 22050

BPM = 90
CHORD_BEATS = 4          # block chord rings over these beats
ARP_BEATS = 4            # then one arpeggio note per beat
GUESS_BEATS = 2          # click-only beats to make your guess
ANSWER_BEATS = 4         # beats 1-2: function run, beats 3-4: quality rhythm
PREVIEW_BEATS = 2        # per chord in the quick full-progression preview
SONG_LOOPS = 4           # times each progression repeats as the exercise
FADE_BEATS = 1.5         # chord/arpeggio fade out over their last beats
AMPLITUDE = 0.5
CLICK_VOLUME = 0.2
DRUM_VOLUME = 0.4
CUE_VOLUME = 0.3         # the tonic->root function plucks

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# intervals in semitones from the root, and the rhythm code that answers for
# the quality: four eighth-note slots over two beats, x = tick, . = rest.
# Simplest patterns on the most common qualities.
QUALITIES = {
    "maj":  {"intervals": (0, 4, 7), "rhythm": "x..."},
    "min":  {"intervals": (0, 3, 7), "rhythm": "x.x."},
    "dom7": {"intervals": (0, 4, 7, 10), "rhythm": "xx.."},
    "maj7": {"intervals": (0, 4, 7, 11), "rhythm": "x..x"},
    "min7": {"intervals": (0, 3, 7, 10), "rhythm": "x.xx"},
    "m7b5": {"intervals": (0, 3, 6, 10), "rhythm": "xxx."},
    "dim7": {"intervals": (0, 3, 6, 9), "rhythm": "xxxx"},
}

MINOR_QUALITIES = {"min", "min7", "m7b5", "dim7"}
DEGREE_NAMES = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                6: "bV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# chord-symbol suffixes normalized to the qualities above
SUFFIX_MAP = {
    "": "maj", "maj": "maj", "M": "maj", "6": "maj", "add9": "maj",
    "sus2": "maj", "sus4": "maj", "5": "maj",
    "m": "min", "min": "min", "-": "min", "m6": "min", "madd9": "min",
    "maj7": "maj7", "M7": "maj7", "maj9": "maj7", "6/9": "maj7",
    "m7": "min7", "min7": "min7", "-7": "min7", "m9": "min7", "m11": "min7",
    "7": "dom7", "9": "dom7", "13": "dom7", "7sus4": "dom7",
    "7b9": "dom7", "7#9": "dom7", "7b5": "dom7", "7#5": "dom7", "7alt": "dom7",
    "m7b5": "m7b5", "ø": "m7b5", "ø7": "m7b5",
    "dim": "dim7", "dim7": "dim7", "o": "dim7", "o7": "dim7",
}


def midi_to_freq(midi_note):
    return 440.0 * 2 ** ((midi_note - 69) / 12)


def parse_root(text):
    """'F#...' -> (pitch class, rest of string). Raises ValueError."""
    m = re.match(r"^([A-G])([#b]?)(.*)$", text.strip())
    if not m:
        raise ValueError(f"can't parse note name in {text!r}")
    letter, accidental, rest = m.groups()
    return (PITCH_CLASS[letter] + {"#": 1, "b": -1}.get(accidental, 0)) % 12, rest


QUALITY_WORDS = {"maj": "major", "min": "minor", "maj7": "major 7",
                 "min7": "minor 7", "dom7": "dominant 7",
                 "m7b5": "half-diminished (m7b5)", "dim7": "diminished 7"}


def parse_chord(symbol, tonic_pc=None, minor_key=False):
    """'F#m7' -> (label, freqs, rhythm, answer_text, cue_freqs).

    answer_text spells out the full answer for the terminal. cue_freqs are
    the pitches of the function cue: a scale run stepping down from the
    chord's root to the tonic, so its note count is the scale degree
    (None when no key is known).
    """
    root_pc, suffix = parse_root(symbol.split("/")[0])  # slash chords: upper chord
    if suffix not in SUFFIX_MAP:
        raise ValueError(f"unknown chord suffix {suffix!r} in {symbol!r}")
    quality = SUFFIX_MAP[suffix]

    root_midi = 48 + root_pc  # roots from C3 up to B3
    freqs = [midi_to_freq(root_midi + i) for i in QUALITIES[quality]["intervals"]]
    pretty = {"maj": "", "min": "m", "min7": "m7", "dom7": "7"}.get(quality, quality)
    label = f"{NOTE_NAMES[root_pc]}{pretty}"
    rhythm = QUALITIES[quality]["rhythm"]

    cue_freqs = None
    if tonic_pc is not None:
        degree = (root_pc - tonic_pc) % 12
        numeral = DEGREE_NAMES[degree]
        if quality in MINOR_QUALITIES:
            numeral = numeral.lower()
        # the run: from the chord root, step through the key's scale to the
        # nearest tonic — down from the 4th and below, up from the 5th and
        # above. Descending count = degree; ascending count = 9 - degree.
        scale = MINOR_SCALE if minor_key else MAJOR_SCALE
        if degree >= 7:
            run = [degree] + [s for s in sorted(scale) if s > degree] + [12]
        else:
            run = [degree] + [s for s in sorted(scale, reverse=True) if s < degree]
        cue_freqs = [midi_to_freq(60 + tonic_pc + pc) for pc in run]
        tonic = NOTE_NAMES[tonic_pc]
        answer_text = f"{label}, the {numeral} of {tonic}  [{rhythm}]"
    else:
        answer_text = f"{label} ({QUALITY_WORDS[quality]})  [{rhythm}]"
    return label, freqs, rhythm, answer_text, cue_freqs


BEAT_SECONDS = 60.0 / BPM
ROUND_BEATS = CHORD_BEATS + ARP_BEATS + GUESS_BEATS + ANSWER_BEATS
ROUND_SECONDS = BEAT_SECONDS * ROUND_BEATS


def envelope(i, n_samples):
    """Soft attack, sustain, then a cosine fade over the last FADE_BEATS beats."""
    attack = int(SAMPLE_RATE * 0.05)
    fade = min(int(SAMPLE_RATE * BEAT_SECONDS * FADE_BEATS), n_samples)
    if i < attack:
        return i / attack
    remaining = n_samples - i
    if remaining < fade:
        return 0.5 - 0.5 * math.cos(math.pi * remaining / fade)
    return 1.0


def tone(t, f):
    # fundamental plus a couple of quiet harmonics so it sounds less like a test tone
    return (math.sin(2 * math.pi * f * t)
            + 0.3 * math.sin(2 * math.pi * 2 * f * t)
            + 0.1 * math.sin(2 * math.pi * 3 * f * t))


def synth_tick():
    """The quality-rhythm tick: the same sound as the metronome click,
    played louder (DRUM_VOLUME vs CLICK_VOLUME), so the code reads as
    accents on the pulse rather than a separate instrument."""
    n = int(SAMPLE_RATE * 0.03)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        decay = 1.0 - i / n
        out.append(decay * decay * math.sin(2 * math.pi * 1800.0 * t))
    peak = max(abs(v) for v in out)
    return [v / peak for v in out]


TICK_SAMPLES = synth_tick()


def add_pluck(samples, start, freq, seconds=0.55):
    """A short plucked note mixed in at sample offset `start`."""
    attack = int(SAMPLE_RATE * 0.01)
    n = min(int(SAMPLE_RATE * seconds), len(samples) - start)
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, i / attack) * math.exp(-5 * t)
        samples[start + i] += CUE_VOLUME * tone(t, freq) * env / 1.4


def add_clicks(samples, n_beats):
    """Soft metronome tick on every beat, mixed into `samples`."""
    click_len = int(SAMPLE_RATE * 0.03)
    for beat in range(n_beats):
        start = int(SAMPLE_RATE * BEAT_SECONDS * beat)
        for i in range(min(click_len, len(samples) - start)):
            t = i / SAMPLE_RATE
            decay = 1.0 - i / click_len
            samples[start + i] += CLICK_VOLUME * decay * decay * math.sin(2 * math.pi * 1800.0 * t)


def pack_frames(samples):
    ints = [max(-32767, min(32767, int(v * 32767))) for v in samples]
    return struct.pack(f"<{len(ints)}h", *ints)


def synth_preview(freqs):
    """One preview chord: PREVIEW_BEATS of block chord with a quick fade, plus clicks."""
    n_total = int(SAMPLE_RATE * BEAT_SECONDS * PREVIEW_BEATS)
    scale = AMPLITUDE / (len(freqs) * 1.4)
    attack = int(SAMPLE_RATE * 0.03)
    fade = int(SAMPLE_RATE * BEAT_SECONDS * 0.5)
    samples = [0.0] * n_total
    for i in range(n_total):
        t = i / SAMPLE_RATE
        env = min(1.0, i / attack)
        remaining = n_total - i
        if remaining < fade:
            env *= 0.5 - 0.5 * math.cos(math.pi * remaining / fade)
        samples[i] = sum(tone(t, f) for f in freqs) * scale * env
    add_clicks(samples, PREVIEW_BEATS)
    return pack_frames(samples)


def synth_round(freqs, rhythm, cue_freqs):
    """Packed 16-bit frames for one round: chord, arpeggio, clicks, then the answer."""
    n_total = int(SAMPLE_RATE * BEAT_SECONDS * ROUND_BEATS)
    n_chord = int(SAMPLE_RATE * BEAT_SECONDS * CHORD_BEATS)
    n_arp = int(SAMPLE_RATE * BEAT_SECONDS * ARP_BEATS)
    scale = AMPLITUDE / (len(freqs) * 1.4)
    samples = [0.0] * n_total

    # block chord over the first CHORD_BEATS beats
    for i in range(n_chord):
        t = i / SAMPLE_RATE
        samples[i] = sum(tone(t, f) for f in freqs) * scale * envelope(i, n_chord)

    # arpeggio: one note per beat, each ringing until the section ends
    # (triads get the root an octave up as a 4th note to fill the section)
    arp_notes = list(freqs) + ([freqs[0] * 2] if len(freqs) < ARP_BEATS else [])
    for k, f in enumerate(arp_notes):
        note_start = n_chord + int(SAMPLE_RATE * BEAT_SECONDS * k)
        note_len = n_chord + n_arp - note_start
        for i in range(note_len):
            t = i / SAMPLE_RATE
            samples[note_start + i] += tone(t, f) * scale * envelope(i, note_len)

    add_clicks(samples, ROUND_BEATS)

    answer_beat = CHORD_BEATS + ARP_BEATS + GUESS_BEATS
    if cue_freqs is not None:
        # answer beats 1-2 — function: the scale run from the chord root,
        # spread over two beats so the final home note lands on beat 3
        start = int(SAMPLE_RATE * BEAT_SECONDS * answer_beat)
        n = len(cue_freqs)
        spacing = 2 * BEAT_SECONDS / max(1, n - 1)
        for k, f in enumerate(cue_freqs):
            add_pluck(samples, start + int(SAMPLE_RATE * spacing * k), f, seconds=0.4)

    # answer beats 3-4 — quality: the rhythm code, x = tick per eighth note
    rhythm_start = int(SAMPLE_RATE * BEAT_SECONDS * (answer_beat + 2))
    for slot, ch in enumerate(rhythm):
        if ch != "x":
            continue
        start = rhythm_start + int(SAMPLE_RATE * BEAT_SECONDS * 0.5 * slot)
        for i in range(min(len(TICK_SAMPLES), n_total - start)):
            samples[start + i] += DRUM_VOLUME * TICK_SAMPLES[i]

    return pack_frames(samples)


def load_songs():
    """{genre: [(title, key_name, [round tuples]), ...]} from progressions/*.jsonl."""
    prog_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progressions")
    songs = {}
    if not os.path.isdir(prog_dir):
        return songs
    for fn in sorted(os.listdir(prog_dir)):
        if not fn.endswith(".jsonl"):
            continue
        genre = fn[:-len(".jsonl")]
        with open(os.path.join(prog_dir, fn)) as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    song = json.loads(line)
                    tonic_pc, minor_key = None, False
                    if song.get("key"):
                        tonic_pc, rest = parse_root(song["key"])
                        minor_key = rest.strip().lower().startswith("m")
                    chords = [parse_chord(c, tonic_pc, minor_key) for c in song["chords"]]
                except (ValueError, KeyError) as e:
                    print(f"Skipping {fn}:{line_no}: {e}")
                    continue
                songs.setdefault(genre, []).append(
                    (song["title"], song.get("key", "?"), chords))
    return songs


def preview_event(chord, announce=None):
    label, freqs, _, _, _ = chord
    return {"kind": "preview", "label": label, "freqs": freqs,
            "beats": PREVIEW_BEATS, "announce": announce}


def round_event(chord, announce=None):
    label, freqs, rhythm, answer_text, cue_freqs = chord
    return {"kind": "round", "label": label, "freqs": freqs, "rhythm": rhythm,
            "answer": answer_text, "cue": cue_freqs,
            "beats": ROUND_BEATS, "announce": announce}


def random_events(total_seconds):
    """Plain random exercise rounds, no songs, no previews."""
    events, t = [], 0.0
    while t < total_seconds:
        quality = random.choice(list(QUALITIES))
        pretty = {"maj": "", "min": "m", "dom7": "7"}.get(quality, quality)
        chord = parse_chord(f"{NOTE_NAMES[random.randrange(12)]}{pretty}")
        events.append(round_event(chord))
        t += ROUND_SECONDS
    return events


def song_events(songs, genre, total_seconds):
    """Chain random songs: each gets a quick full preview, then SONG_LOOPS
    passes through its progression as exercise rounds."""
    pool = [(g, s) for g, lst in songs.items() for s in lst
            if genre is None or g == genre]
    events, t = [], 0.0
    while t < total_seconds:
        g, (title, key, chords) = random.choice(pool)
        block = [preview_event(c, announce=f"♪ {title} [{g}] — key of {key} (preview...)"
                               if i == 0 else None)
                 for i, c in enumerate(chords)]
        for loop in range(SONG_LOOPS):
            block += [round_event(c, announce=f"— loop {loop + 1}/{SONG_LOOPS} —"
                                  if i == 0 else None)
                      for i, c in enumerate(chords)]
        for ev in block:
            events.append(ev)
            t += ev["beats"] * BEAT_SECONDS
            if t >= total_seconds:
                break
    return events


def build_session_wav(path, events):
    """Render each unique segment once, then stitch the sequence into one file."""
    cache = {}
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for n, ev in enumerate(events, 1):
            key = (ev["kind"], ev["label"], ev.get("answer"))
            if key not in cache:
                if ev["kind"] == "preview":
                    cache[key] = synth_preview(ev["freqs"])
                else:
                    cache[key] = synth_round(ev["freqs"], ev["rhythm"], ev["cue"])
            wf.writeframes(cache[key])
            print(f"\rRendering... {n}/{len(events)} segments "
                  f"({len(cache)} unique)", end="", flush=True)
    print()


def main():
    minutes, genre = 60.0, None
    for arg in sys.argv[1:]:
        try:
            minutes = float(arg)
        except ValueError:
            genre = arg

    songs = load_songs()
    if genre == "random" or not songs:
        events = random_events(minutes * 60)
        source = "random chords"
    elif genre is not None and genre not in songs:
        print(f"Unknown genre {genre!r}. Available: {', '.join(sorted(songs))}, random")
        return
    else:
        events = song_events(songs, genre, minutes * 60)
        source = f"genre: {genre}" if genre else f"all genres ({', '.join(sorted(songs))})"
    n_rounds = sum(1 for ev in events if ev["kind"] == "round")

    answer_offset = BEAT_SECONDS * (CHORD_BEATS + ARP_BEATS + GUESS_BEATS)
    tmpdir = tempfile.mkdtemp(prefix="chords_")
    wav_path = os.path.join(tmpdir, "session.wav")
    player = None
    start = time.time()
    count = 0

    try:
        build_session_wav(wav_path, events)

        print(f"""
{n_rounds} exercise chords at {BPM} BPM ({source}) — Ctrl+C to stop.

Each song: a quick preview of the whole progression ({PREVIEW_BEATS} beats
per chord, to plant the key in your ear), then the progression loops
{SONG_LOOPS}x as the exercise. Each exercise round: 4 beats chord, 4 beats
arpeggio, 2 beats to guess, then the ANSWER plays on the last 4 beats:

  answer beats 1-2 — WHERE in the key: a scale run from the chord's root
      to the nearest home note (tonic): DOWN for degrees I-IV, UP for V
      and above. COUNT THE NOTES:
      going down: 1 note (just home) = I | 2 = ii | 3 = iii | 4 = IV
      going up:   4 notes = V | 3 = vi | 2 = vii

  answer beats 3-4 — WHAT type of chord: a tick rhythm in eighth notes
      (x = tick, . = silent), simplest patterns for the most common:
      x... = major | x.x. = minor | xx.. = dom 7 | x..x = maj 7
      x.xx = min 7 | xxx. = m7b5  | xxxx = dim 7
""")

        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()

        target = 0.0  # absolute schedule position (no drift)
        for ev in events:
            if player.poll() is not None:
                break
            if ev["announce"]:
                print(f"\n{ev['announce']}")
            if ev["kind"] == "round":
                count += 1
                print(f"Chord #{count} ... ", end="", flush=True)
                time.sleep(max(0.0, target + answer_offset - (time.time() - start)))
                print(ev["answer"])
            target += ev["beats"] * BEAT_SECONDS
            time.sleep(max(0.0, target - (time.time() - start)))
        player.wait()
    except KeyboardInterrupt:
        if player is not None:
            player.terminate()
        print("\nStopped early.")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
        os.rmdir(tmpdir)

    elapsed = (time.time() - start) / 60
    print(f"Done — {count} chords over {elapsed:.0f} minutes.")


if __name__ == "__main__":
    main()
