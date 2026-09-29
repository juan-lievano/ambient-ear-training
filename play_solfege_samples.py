#!/usr/bin/env python3
"""Sound check for the sung solfege samples in solfege_samples/.

Usage:
    python3 play_solfege_samples.py [--voice NAME] [--output FILE]

    python3 play_solfege_samples.py                    # every voice, ~3.5 min
    python3 play_solfege_samples.py --voice jennifer
    python3 play_solfege_samples.py --output check.wav # keep the render

Each voice sings the same short programme in C, its samples untouched
apart from where they are cut:

    the major scale up and down at 60, 120 and 240 BPM
    arpeggios on I, IV, V and I at 120 BPM
    the chromatic scale up at 120 BPM, for the voices that have one

The samples are sung the way a singer places a syllable: the consonant
comes early and the vowel lands at a steady point a little way in. So each
sample is laid with its vowel on the beat and its consonant ahead of it,
and the note before is held until that vowel arrives, fading out underneath
it — the notes overlap instead of being cut apart. The last note of a
phrase is left to ring.

The samples are fixed-do (every "do" is a C) and are not in this repo:
they are the note folders of https://github.com/wcgbg/solfege-samples,
copied into solfege_samples/.

The whole check is rendered as one WAV up front, then played with macOS's
afplay. Standard library only. The terminal prints each phrase as it
sounds.
"""

import array
import math
import os
import subprocess
import sys
import tempfile
import time
import wave

SAMPLE_RATE = 44100      # what the samples were recorded at
SAMPLES_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "solfege_samples")

SCALE_BPMS = (60, 120, 240)
ARPEGGIO_BPM = 120
CHROMATIC_BPM = 120

RELEASE_SECONDS = 0.12   # how long a note takes to fade out under the next
OVERLAP_SECONDS = 0.06   # how far into the next vowel that fade reaches
FADE_IN_SECONDS = 0.005
LEVEL = 0.8              # headroom for where two notes overlap
RING_SECONDS = 1.5       # the last note of a phrase rings this long at most
RING_RELEASE_SECONDS = 0.3
PHRASE_GAP_SECONDS = 0.6
VOICE_GAP_SECONDS = 1.2

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# What each voice calls the twelve semitones above do. Daisy has the usual
# raised syllables; Chengu and Jennifer sing Sotorrio's, one fixed syllable
# per semitone; Katy only recorded the major scale.
RAISED = ("do", "di", "re", "ri", "mi", "fa", "fi", "so", "si", "la", "li", "ti")
SOTORRIO = ("do", "ga", "re", "nu", "mi", "fa", "jur", "so", "ki", "la", "pe", "ti")

# tonic is the MIDI note of the do the programme is built on; lead is how far
# into a sample the vowel lands, in seconds — the median over the voice's
# samples of where the level first reaches half its peak.
VOICES = {
    "daisy": dict(folder="daisy/notes", tonic=60, lead=0.040,
                  syllables=RAISED, chromatic=True,
                  about="synthesized, female"),
    "jennifer": dict(folder="jennifer/notes", tonic=60, lead=0.135,
                     syllables=SOTORRIO, chromatic=True,
                     about="recorded, female"),
    "chengu": dict(folder="chengu/notes", tonic=48, lead=0.130,
                   syllables=SOTORRIO, chromatic=True,
                   about="recorded, male"),
    "katy": dict(folder="katy/notes-align-100ms", tonic=60, lead=0.105,
                 syllables=RAISED, chromatic=False,
                 about="recorded, female, one octave only"),
}

# Phrases are semitones above the tonic. Everything stays inside one octave,
# because that is all Katy has; V is turned over (re so ti) to fit.
SCALE = (0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2, 0)
ARPEGGIOS = (("I", (0, 4, 7, 12, 7, 4, 0)),
             ("IV", (5, 9, 12, 9, 5)),
             ("V", (2, 7, 11, 7, 2)),
             ("I", (0, 4, 7, 12)))
CHROMATIC = tuple(range(13))


def programme(voice):
    """The phrases one voice sings: (title, bpm, semitones) each."""
    phrases = [(f"scale at {bpm} BPM", bpm, SCALE) for bpm in SCALE_BPMS]
    phrases += [(f"arpeggio on {chord}", ARPEGGIO_BPM, notes)
                for chord, notes in ARPEGGIOS]
    if VOICES[voice]["chromatic"]:
        phrases.append((f"chromatic at {CHROMATIC_BPM} BPM", CHROMATIC_BPM,
                        CHROMATIC))
    return phrases


def load_note(path, cache={}):
    """One sample as 16-bit mono, whole, at LEVEL."""
    if path not in cache:
        with wave.open(path, "rb") as wf:
            shape = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
            if shape != (1, 2, SAMPLE_RATE):
                raise ValueError(f"{path} is not 16-bit mono at {SAMPLE_RATE} Hz")
            samples = array.array("h")
            samples.frombytes(wf.readframes(wf.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        cache[path] = array.array("h", (int(x * LEVEL) for x in samples))
    return cache[path]


def plan(voices):
    """Lay the whole check out in time.

    Returns (notes, captions, total_samples): notes are (start, path, length,
    release) in samples, captions are (seconds, text) to print as the audio
    gets there.
    """
    notes, captions = [], []
    overlap = round(OVERLAP_SECONDS * SAMPLE_RATE)
    # the cursor is where the next vowel lands; the first one needs room
    # ahead of it for its consonant
    cursor = round(max(VOICES[v]["lead"] for v in voices) * SAMPLE_RATE)
    for voice in voices:
        spec = VOICES[voice]
        tonic = spec["tonic"]
        lead = round(spec["lead"] * SAMPLE_RATE)
        tonic_name = f"{NOTE_NAMES[tonic % 12]}{tonic // 12 - 1}"
        captions.append(((cursor - lead) / SAMPLE_RATE,
                         f"\n{voice} — {spec['about']}, do on {tonic_name}"))
        for title, bpm, semitones in programme(voice):
            beat = round(SAMPLE_RATE * 60 / bpm)
            sung = [spec["syllables"][s % 12] for s in semitones]
            captions.append(((cursor - lead) / SAMPLE_RATE,
                             f"  {title:<22}{' '.join(sung)}"))
            for i, (semitone, syllable) in enumerate(zip(semitones, sung)):
                path = os.path.join(SAMPLES_DIRECTORY, spec["folder"],
                                    f"note{tonic + semitone:03d}-{syllable}.wav")
                last = i == len(semitones) - 1
                held = max(beat, round(RING_SECONDS * SAMPLE_RATE)) if last else beat
                release = RING_RELEASE_SECONDS if last else RELEASE_SECONDS
                length = min(lead + held + (0 if last else overlap),
                             len(load_note(path)))
                notes.append((cursor - lead, path, length,
                              min(length, round(release * SAMPLE_RATE))))
                cursor += held
            cursor += round(PHRASE_GAP_SECONDS * SAMPLE_RATE)
        cursor += round(VOICE_GAP_SECONDS * SAMPLE_RATE)
    return notes, captions, cursor


def render(path, notes, total_samples):
    """Each note is faded at both ends and mixed over the tail of the last."""
    out = array.array("h", bytes(2 * total_samples))
    fade_in = int(FADE_IN_SECONDS * SAMPLE_RATE)
    written = 0          # nothing has been laid down from here on
    for start, note_path, length, release in notes:
        note = load_note(note_path)[:length]
        for i in range(min(fade_in, length)):
            note[i] = int(note[i] * i / fade_in)
        for i in range(release):
            gain = 0.5 - 0.5 * math.cos(math.pi * i / release)
            note[length - 1 - i] = int(note[length - 1 - i] * gain)
        for i in range(max(0, min(written - start, length))):
            note[i] = max(-32768, min(32767, note[i] + out[start + i]))
        out[start:start + length] = note
        written = max(written, start + length)
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(out.tobytes())


USAGE = f"""Sound check for the solfege samples. Every setting is a flag:

  --voice NAME    which voice to hear:
                  {" | ".join(VOICES)} | all
                  (default all)
  --output FILE   keep the rendered WAV at FILE instead of a temp file
  --help          print this and stop

  python3 play_solfege_samples.py --voice jennifer
"""


def fail(problem):
    """Say what was wrong with the arguments, then list all of them."""
    print(f"{problem}\n")
    print(USAGE, end="")
    return None


def parse_args(argv):
    """Every argument is a named flag. Returns settings, or None to stop."""
    settings = dict(voice="all", output=None)
    verbatim = {"--voice": "voice", "--output": "output"}

    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--help":
            print(USAGE, end="")
            return None
        if flag not in verbatim:
            hint = "every setting is passed as a named flag"
            if flag.startswith("-"):
                hint = "no such flag"
            return fail(f"{flag!r}: {hint}.")
        if not args:
            return fail(f"{flag} needs a value after it.")
        settings[verbatim[flag]] = args.pop(0)

    voice = settings["voice"].strip().lower()
    if voice != "all" and voice not in VOICES:
        return fail(f"--voice {settings['voice']!r} is not a voice I have.")
    settings["voices"] = list(VOICES) if voice == "all" else [voice]
    return settings


def main():
    settings = parse_args(sys.argv[1:])
    if settings is None:
        return 2
    voices, out_path = settings["voices"], settings["output"]

    try:
        notes, captions, total_samples = plan(voices)
    except FileNotFoundError as missing:
        print(f"Sample not found: {missing.filename}\n\n"
              "The samples are not part of this repo. Copy the note folders of\n"
              "https://github.com/wcgbg/solfege-samples (samples/*/notes*) into\n"
              f"{SAMPLES_DIRECTORY}")
        return 1

    tmpdir = None
    if out_path is None:
        tmpdir = tempfile.mkdtemp(prefix="solfege_")
        wav_path = os.path.join(tmpdir, "check.wav")
    else:
        wav_path = out_path

    player = None
    try:
        render(wav_path, notes, total_samples)

        print(f"{', '.join(voices)}: {total_samples / SAMPLE_RATE / 60:.1f} "
              "minutes — Ctrl+C to stop.")
        if out_path is not None:
            print(f"Saved to {wav_path}")

        player = subprocess.Popen(["afplay", wav_path])
        start = time.time()
        for seconds, text in captions:
            if player.poll() is not None:
                break
            time.sleep(max(0.0, seconds - (time.time() - start)))
            print(text, flush=True)
        player.wait()
    except FileNotFoundError:
        print("afplay not found — this plays audio on macOS. "
              f"The check is rendered at {wav_path}")
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


if __name__ == "__main__":
    sys.exit(main())
