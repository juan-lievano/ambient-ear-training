"""A voice bank: one WAV per label, made on demand with macOS `say`.

The bank is a directory of `<label>.wav` at the renderer's sample rate. Any
directory with the right file names is a bank, so a sung or sampled voice
can replace the spoken one without touching the rest.
"""

import subprocess
import wave
from pathlib import Path

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
