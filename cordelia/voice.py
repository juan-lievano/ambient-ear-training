"""Voice banks: one WAV per label, made on demand.

A bank is a directory of `<label>.wav` at the renderer's sample rate. The
spoken bank makes its files with macOS `say` ("four", "flat seven"). The
sung bank makes its files from the solfege samples in solfege_samples/,
one syllable pitched to one MIDI note per label ("mi@64"), so a melody's
answer can be sung back in the key it was asked in. The pitching keeps the
singer's speed and vowel (see psola.py).
"""

import array
import math
import subprocess
import wave
from pathlib import Path

from . import psola
from .synth import SAMPLE_RATE


class VoiceBank:
    def __init__(self, name, data_dir):
        self.name = name
        self.dir = Path(data_dir) / "voices" / name
        self._cache = {}

    def path(self, label):
        return self.dir / (label.replace("#", "sharp") + ".wav")

    def ensure(self, texts):
        """Make sure every label in `texts` (label -> words to say) has a file."""
        self.dir.mkdir(parents=True, exist_ok=True)
        for label, text in texts.items():
            out = self.path(label)
            if out.exists():
                continue
            cmd = ["say", "-o", str(out), f"--data-format=LEI16@{SAMPLE_RATE}"]
            if self.name != "default":
                cmd += ["-v", self.name]
            try:
                subprocess.run(cmd + [text], check=True, capture_output=True, text=True)
            except FileNotFoundError:
                raise SystemExit("`say` not found: the spoken voice needs macOS.")
            except subprocess.CalledProcessError as e:
                raise SystemExit(f"say failed for voice {self.name!r}: {e.stderr.strip()}\n"
                                 "`say -v ?` lists the voices installed.")

    def load(self, label):
        """Samples in [-1, 1], peak-normalised, leading silence trimmed."""
        if label in self._cache:
            return self._cache[label]
        with wave.open(str(self.path(label))) as w:
            if w.getframerate() != SAMPLE_RATE or w.getsampwidth() != 2:
                raise SystemExit(f"{self.path(label)}: need 16-bit at {SAMPLE_RATE} Hz")
            raw = w.readframes(w.getnframes())
            ch = w.getnchannels()
        import array
        pcm = array.array("h")
        pcm.frombytes(raw)
        if ch > 1:
            pcm = pcm[::ch]
        peak = max(1, max(abs(v) for v in pcm))
        gate = 0.01 * peak
        start = next((i for i, v in enumerate(pcm) if abs(v) > gate), 0)
        samples = [v / peak for v in pcm[start:]]
        self._cache[label] = samples
        return samples

    def seconds(self, label):
        return len(self.load(label)) / SAMPLE_RATE


# --- the sung bank -------------------------------------------------------------

SAMPLES_DIRECTORY = Path(__file__).resolve().parent.parent / "solfege_samples"

# The singers that recorded every semitone with Sotorrio's syllables, so a
# minor scale can be sung too: which folder, the MIDI note of their lowest
# do, and where the vowel lands in a sample (the median over the voice's
# files of the first half-peak level, from play_solfege_samples.py).
SINGERS = {
    "jennifer": dict(folder="jennifer/notes", low=48, high=83, lead=0.135),
    "chengu": dict(folder="chengu/notes", low=36, high=71, lead=0.130),
}
SOTORRIO = ("do", "ga", "re", "nu", "mi", "fa", "jur", "so", "ki", "la", "pe", "ti")
SYLLABLE = {"1": "do", "2": "re", "3": "mi", "4": "fa", "5": "so", "6": "la", "7": "ti", "8": "do",
            "b3": "nu", "b6": "ki", "b7": "pe"}
SOURCE_RATE = 44100
MAX_SHIFT = 6                # semitones a sample is pitched by, at most: the nearest octave
GATE = 0.01                  # of the peak: the leading silence is cut here
VOWEL = 0.5                  # of the peak: where the vowel is taken to land


def sung_label(label, midi):
    """The clip label for scale-degree label `label` sung on `midi`."""
    return f"{SYLLABLE[label]}@{midi}"


class SungBank(VoiceBank):
    """Labels are `<syllable>@<midi>`; each file is the singer's nearest
    sample of that syllable, pitched to the note with its timbre kept."""

    def __init__(self, singer, data_dir):
        if singer not in SINGERS:
            raise SystemExit(f"singer {singer!r}: choose from {', '.join(SINGERS)}")
        super().__init__(f"sung-{singer}", data_dir)
        self.singer = singer
        self.spec = SINGERS[singer]

    def ensure(self, labels):
        self.dir.mkdir(parents=True, exist_ok=True)
        for label in labels:
            out = self.path(label)
            if not out.exists():
                self._make(label, out)

    def lead(self, label):
        """Seconds into the clip where the vowel lands: put this on the beat."""
        samples = self.load(label)
        peak = max(abs(v) for v in samples)
        return next((i for i, v in enumerate(samples) if abs(v) >= VOWEL * peak), 0) / SAMPLE_RATE

    def _source(self, syllable, midi):
        """The singer's sample of `syllable` nearest to `midi`."""
        spec = self.spec
        candidates = [m for m in range(spec["low"], spec["high"] + 1) if SOTORRIO[m % 12] == syllable]
        if not candidates:
            raise SystemExit(f"{self.singer} has no sample for {syllable!r}")
        source = min(candidates, key=lambda m: abs(m - midi))
        if abs(source - midi) > MAX_SHIFT:
            raise SystemExit(f"{self.singer} has no {syllable!r} within {MAX_SHIFT} semitones of MIDI {midi}")
        path = SAMPLES_DIRECTORY / spec["folder"] / f"note{source:03d}-{syllable}.wav"
        if not path.exists():
            raise SystemExit(f"sample not found: {path}\nThe samples are not part of the repo: copy the "
                             "note folders of https://github.com/wcgbg/solfege-samples into solfege_samples/")
        return source, path

    def _make(self, label, out):
        syllable, midi = label.split("@")
        midi = int(midi)
        source, path = self._source(syllable, midi)
        with wave.open(str(path)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SOURCE_RATE):
                raise SystemExit(f"{path}: need 16-bit mono at {SOURCE_RATE} Hz")
            src = array.array("h")
            src.frombytes(w.readframes(w.getnframes()))
        shifted = psola.shift([v / 32768.0 for v in src], midi - source, SOURCE_RATE)
        # halve the rate for the renderer, through a small triangle filter
        # so what lies above the new Nyquist does not fold down
        assert SOURCE_RATE == 2 * SAMPLE_RATE
        pcm = [0.25 * shifted[2 * i - 1] + 0.5 * shifted[2 * i] + 0.25 * shifted[2 * i + 1]
               for i in range(1, len(shifted) // 2 - 1)]
        peak = max(1e-9, max(abs(v) for v in pcm))
        start = next((i for i, v in enumerate(pcm) if abs(v) > GATE * peak), 0)
        scale = 0.9 * 32767 / peak
        pcm = array.array("h", (int(v * scale) for v in pcm[start:]))
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
