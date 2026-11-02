"""Scale degrees over a drone.

Each question: the note sounds for two beats, two beats of silence to
answer in, then the voice says the degree, the note sounds again, and a
beat of rest. The right answer is always given, nothing is ever judged out
loud, and the register varies so that the degree, not the pitch, is what
is being asked.
"""

import math
import random

from ..score import Clip, Note, Score

NAME = "degrees"

PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

SCALES = {
    "major": dict(steps=(0, 2, 4, 5, 7, 9, 11), labels=("1", "2", "3", "4", "5", "6", "7")),
    "natural-minor": dict(steps=(0, 2, 3, 5, 7, 8, 10), labels=("1", "2", "b3", "4", "5", "b6", "b7")),
    "harmonic-minor": dict(steps=(0, 2, 3, 5, 7, 8, 11), labels=("1", "2", "b3", "4", "5", "b6", "7")),
}
SCALE_ALIASES = {"minor": "natural-minor", "aeolian": "natural-minor",
                 "ionian": "major", "harmonic": "harmonic-minor"}

SPOKEN = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
          "b3": "flat three", "b6": "flat six", "b7": "flat seven"}

# Degree 1 is the easy one, so it turns up less often.
DEGREE_WEIGHTS = {1: 1, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3, 7: 3}
# Questions reach half an octave past the middle register either way.
OCTAVES = (-1, 0, 1)
REGISTER_DEGREES = {-1: (5, 6, 7), 0: (1, 2, 3, 4, 5, 6, 7), 1: (1, 2, 3, 4)}
OCTAVE_WEIGHTS = {-1: 2, 0: 3, 1: 2}
OCTAVE_LEVELS = {-1: 1.1, 0: 1.0, 1: 0.9}
OCTAVE_NAMES = {-1: "low", 0: "mid", 1: "high"}

NOTE_BEATS = 2
PAUSE_BEATS = 2
AGAIN_BEATS = 2
REST_BEATS = 1
INTRO_BEATS = 8
OUTRO_SECONDS = 2.0
RING = 0.25                    # a note rings this long past its beats

DEFAULTS = dict(minutes=15.0, key=None, scale="major", bpm=60.0, voice="default")
MIN_BPM, MAX_BPM = 20, 240


def random_key(rng, avoid=None):
    """Any key but the one the last session was in."""
    return rng.choice([k for k in NOTE_NAMES if k != avoid])


def parse_key(text):
    """'G', 'f#', 'Bb' -> pitch class 0-11, or None."""
    key = text.strip().capitalize().replace("s", "#")
    root, accidental = key[:1], key[1:]
    if root not in PITCH_CLASS or accidental not in ("", "#", "b"):
        return None
    return (PITCH_CLASS[root] + {"": 0, "#": 1, "b": -1}[accidental]) % 12


def parse_scale(text):
    name = text.strip().lower().replace("_", "-").replace(" ", "-")
    name = SCALE_ALIASES.get(name, name)
    return name if name in SCALES else None


def keymap(scale):
    labels = SCALES[scale]["labels"]
    km = {str(d): labels[d - 1] for d in range(1, 8)}
    km["space"] = "pass"
    return km


def spoken(scale):
    return {label: SPOKEN[label] for label in SCALES[scale]["labels"]}


def describe(config):
    return f"{NOTE_NAMES[parse_key(config['key'])]} {config['scale'].replace('-', ' ')}, {config['bpm']:g} BPM"


class Builder:
    def __init__(self, config, bank):
        self.config = config
        self.bank = bank
        self.beat = 60.0 / config["bpm"]
        self.scale = SCALES[config["scale"]]
        self.pc = parse_key(config["key"])
        self.score = Score(drone={"midi": 24 + self.pc}, voice=bank.name)

    def midi(self, degree, octave):
        # middle register tonic is octave 4; the drone root is three octaves under it
        return 60 + self.pc + self.scale["steps"][degree - 1] + 12 * octave

    def label(self, degree):
        return self.scale["labels"][degree - 1]

    def phrase(self, t, degree, octave):
        """Question at t. Returns (window_end, next_t)."""
        beat = self.beat
        midi, level = self.midi(degree, octave), OCTAVE_LEVELS[octave]
        self.score.notes.append(Note(t, NOTE_BEATS * beat + RING, midi, level))
        voice_t = t + (NOTE_BEATS + PAUSE_BEATS) * beat
        label = self.label(degree)
        self.score.clips.append(Clip(voice_t, label))
        voice_beats = max(1, math.ceil(self.bank.seconds(label) / beat))
        again = voice_t + voice_beats * beat
        self.score.notes.append(Note(again, AGAIN_BEATS * beat + RING, midi, level))
        return voice_t, again + (AGAIN_BEATS + REST_BEATS) * beat

    def max_phrase(self):
        longest = max(self.bank.seconds(l) for l in self.scale["labels"])
        return (NOTE_BEATS + PAUSE_BEATS + max(1, math.ceil(longest / self.beat))
                + AGAIN_BEATS + REST_BEATS) * self.beat


def pool():
    return [((d, o), DEGREE_WEIGHTS[d] * OCTAVE_WEIGHTS[o]) for o in OCTAVES for d in REGISTER_DEGREES[o]]


def draw(rng, count):
    """(degree, octave) pairs, never the same degree twice in a row."""
    questions = [q for q, _ in pool()]
    weights = [w for _, w in pool()]
    out, previous = [], None
    for _ in range(count):
        degree, octave = rng.choices(questions, weights)[0]
        while degree == previous:
            degree, octave = rng.choices(questions, weights)[0]
        out.append((degree, octave))
        previous = degree
    return out


def build_session(config, bank, rng=None):
    """Score + key items for a fresh session of config['minutes']."""
    rng = rng or random.Random()
    b = Builder(config, bank)
    limit = config["minutes"] * 60
    t = INTRO_BEATS * b.beat
    count = max(1, int((limit - t) // b.max_phrase()))
    items = []
    for idx, (degree, octave) in enumerate(draw(rng, count)):
        window_end, nxt = b.phrase(t, degree, octave)
        items.append(dict(idx=idx, truth=b.label(degree), ask_s=t, window_end_s=window_end,
                          meta=dict(degree=degree, octave=octave, register=OCTAVE_NAMES[octave],
                                    midi=b.midi(degree, octave))))
        t = nxt
    b.score.seconds = t + OUTRO_SECONDS
    return b.score, items, keymap(config["scale"])


def build_review(config, metas, bank):
    """The same phrase for each (degree, octave) in `metas`, nothing asked."""
    b = Builder(config, bank)
    t = INTRO_BEATS * b.beat / 2
    for m in metas:
        _, t = b.phrase(t, m["degree"], m["octave"])
    b.score.seconds = t + OUTRO_SECONDS
    return b.score
