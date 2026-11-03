"""Short melodies over a drone, sung back by a voice.

The session opens with the major scale up to the octave, each step over a
chord, so the key is set before anything is asked. Each question is one
melody of a few quarter notes, played three times with a bar of clicks
between, then a bar of clicks to think, then sung back in solfege on its
own pitches. You type the degrees, one key per note; the last ones typed
before the singing starts are the answer.

The levels are a table: which degrees a melody may use (1-8, 8 the tonic
above), how many notes, how far it may leap, and what share of the moves
go to an adjacent degree. A new level is a new row and nothing else, and
every level's answers are stored the same way (the degrees as a
space-separated string), so the stats read across them.
"""

import random

from ..score import Clip, Note, Score
from ..voice import SungBank, sung_label
from .degrees import HOME_ROW, NOTE_NAMES, SCALES, parse_key, parse_scale, random_key  # noqa: F401

NAME = "melody"

# degrees: what a melody may use; notes: how many; max_leap: the widest move
# in scale steps (None: any); step_share: the chance each move goes to the
# degree just above or below (None: every degree equally likely), the rest
# of the moves going to any other allowed degree, a repeat included. The
# first note is uniform over the degrees.
LEVELS = {
    1: dict(degrees=(1, 2, 3), notes=4, max_leap=None, step_share=None,
            about="four quarter notes on 1 2 3"),
    2: dict(degrees=(1, 2, 3, 4, 5), notes=4, max_leap=None, step_share=0.5,
            about="four quarter notes on 1 to 5, half the moves stepwise"),
}

# A question, in beats: the melody, a bar of clicks, the melody, clicks,
# the melody, a bar of clicks to think, then the answer sung a note a beat,
# and a rest.
HEARINGS = 3
CLICK_BEATS = 4
ANSWER_CLICK_BEATS = 4         # between the last hearing and the answer
REST_BEATS = 2
INTRO_BEATS = 1                # the drone's fade-in, then the cadence, then the questions
CADENCE_REST_BEATS = 2         # after the cadence, before the first question
OUTRO_SECONDS = 2.0            # drone alone after the last answer, fading out
RING = 0.25                    # a melody note rings this long past its beat
CLICK_LEVEL = 0.5
CHORD_LEVEL = 0.4              # each chord tone under the cadence scale
SUNG_LEVEL = 1.3
SUNG_OVERLAP = 0.06            # a sung note holds this far past the next one's vowel
SUNG_RING = 1.5                # the last sung note rings this long at most

# The cadence: the scale 1-8 a beat each, each step over a chord, the last
# held. Chords are (degree, octave) relative to the melody register.
CHORDS = {
    "I": ((1, -1), (3, -1), (5, -1)),
    "IV": ((4, -1), (6, -1), (1, 0)),
    "V": ((5, -1), (7, -1), (2, 0)),
}
CADENCE = {"major": ("I", "V", "I", "IV", "I", "IV", "V", "I")}
CADENCE_LAST_BEATS = 2

DEFAULTS = dict(minutes=5.0, key=None, scale="major", bpm=55.0, level=1, singer="jennifer")


def keymap(scale):
    """1-8 (and the home row for 1-7) to scale-degree labels; 8 is the tonic above."""
    labels = SCALES[scale]["labels"] + ("8",)
    km = {str(d): labels[d - 1] for d in range(1, 9)}
    km.update({HOME_ROW[d - 1]: labels[d - 1] for d in range(1, 8)})
    return km


def describe(config):
    return (f"level {config['level']}, {NOTE_NAMES[parse_key(config['key'])]} "
            f"{config['scale'].replace('-', ' ')}, {config['bpm']:g} BPM")


def tonic_midi(pc):
    """The melody's tonic: the octave that keeps it nearest middle C, which
    also keeps every sung note within six semitones of a sample."""
    return 60 + pc if pc <= 6 else 48 + pc


class Builder:
    def __init__(self, config, bank):
        self.config = config
        self.bank = bank
        self.beat = 60.0 / config["bpm"]
        self.scale = SCALES[config["scale"]]
        self.pc = parse_key(config["key"])
        self.score = Score(drone={"midi": 36 + self.pc}, voice=bank.name)

    def midi(self, degree, octave=0):
        steps = self.scale["steps"]
        return tonic_midi(self.pc) + steps[(degree - 1) % 7] + 12 * (octave + (degree - 1) // 7)

    def label(self, degree):
        return "8" if degree == 8 else self.scale["labels"][degree - 1]

    def labels(self, degrees):
        return " ".join(self.label(d) for d in degrees)

    def sung(self, degree):
        return sung_label(self.label(degree), self.midi(degree))

    def melody(self, t, degrees):
        """The notes a beat each from t. Returns next_t."""
        for d in degrees:
            self.score.notes.append(Note(t, self.beat + RING, self.midi(d)))
            t += self.beat
        return t

    def clicks(self, t, beats=CLICK_BEATS):
        for _ in range(beats):
            self.score.notes.append(Note(t, 0.03, 84, CLICK_LEVEL, voice="click"))
            t += self.beat
        return t

    def sing(self, t, degrees):
        """The answer, a note a beat on its own pitch; each sung note's vowel
        lands on the beat and holds a little under the next. Returns next_t."""
        for i, d in enumerate(degrees):
            label = self.sung(d)
            lead = self.bank.lead(label)
            last = i == len(degrees) - 1
            dur = lead + (SUNG_RING if last else self.beat + SUNG_OVERLAP)
            self.score.clips.append(Clip(t - lead, label, SUNG_LEVEL, dur))
            t += self.beat
        return t

    def phrase(self, t, degrees):
        """Question at t. Returns (window_end, next_t)."""
        for i in range(HEARINGS):
            t = self.melody(t, degrees)
            if i < HEARINGS - 1:
                t = self.clicks(t)
        t = self.clicks(t, ANSWER_CLICK_BEATS)
        window_end = t
        t = self.sing(t, degrees)
        return window_end, t + REST_BEATS * self.beat

    def cadence(self, t):
        """The scale up to the octave over I, V, I, IV..., starting at t. Returns next_t."""
        chords = CADENCE[self.config["scale"]]
        for step, chord in enumerate(chords, 1):
            beats = CADENCE_LAST_BEATS if step == len(chords) else 1
            top = self.midi(step)
            self.score.notes.append(Note(t, beats * self.beat + RING, top))
            for degree, octave in CHORDS[chord]:
                midi = self.midi(degree, octave)
                if midi != top:
                    self.score.notes.append(Note(t, beats * self.beat + RING, midi, CHORD_LEVEL))
            t += beats * self.beat
        return t + CADENCE_REST_BEATS * self.beat

    def phrase_beats(self, notes):
        return HEARINGS * notes + (HEARINGS - 1) * CLICK_BEATS + ANSWER_CLICK_BEATS + notes + REST_BEATS


def next_note(rng, spec, last):
    allowed = [d for d in spec["degrees"] if spec["max_leap"] is None or abs(d - last) <= spec["max_leap"]]
    steps = [d for d in allowed if abs(d - last) == 1]
    share = spec["step_share"]
    if share is not None and steps and rng.random() < share:
        return rng.choice(steps)
    others = [d for d in allowed if d not in steps] if share is not None else allowed
    return rng.choice(others or allowed)


def draw(rng, level, count):
    """`count` melodies for the level, never the same one twice in a row."""
    spec = LEVELS[level]
    out, previous = [], None
    while len(out) < count:
        melody = [rng.choice(spec["degrees"])]
        while len(melody) < spec["notes"]:
            melody.append(next_note(rng, spec, melody[-1]))
        if melody != previous:
            out.append(melody)
            previous = melody
    return out


def sung_labels(config):
    """Every sung clip a session at this config could need."""
    b = Builder(config, _NoBank())
    return [b.sung(d) for d in range(1, 9)]


class _NoBank:
    name = "none"


def bank(config, data_dir):
    """The singer's bank, with every clip the session can ask for."""
    b = SungBank(config["singer"], data_dir)
    b.ensure(sung_labels(config))
    return b


def configure(a, last_config, rng):
    """A session config from the parsed flags, or a SystemExit saying why not."""
    pc = parse_key(a.key or random_key(rng, last_config and last_config.get("key")))
    if pc is None:
        raise SystemExit(f"--key {a.key!r} is not a note name")
    scale = parse_scale(a.scale)
    if scale not in CADENCE:
        raise SystemExit(f"--scale {a.scale!r}: melody mode has a cadence for {', '.join(CADENCE)} only")
    if a.level not in LEVELS:
        raise SystemExit(f"level {a.level}: choose from {', '.join(map(str, LEVELS))}")
    if a.minutes <= 0:
        raise SystemExit("--minutes must be positive")
    return dict(minutes=a.minutes, key=NOTE_NAMES[pc], scale=scale, bpm=a.bpm, level=a.level,
                singer=a.singer, keys=LEVELS[a.level]["notes"])


def hint(config):
    spec = LEVELS[config["level"]]
    return (f"{spec['about']}. Type the {spec['notes']} degrees (1-8, or {HOME_ROW} for 1-7), "
            "backspace corrects; the last ones typed before the singing count. Esc stops.")


def status(current, n, typed, config):
    slots = ["_"] * config["keys"]
    for i, k in enumerate(typed[-config["keys"]:]):
        slots[i] = k
    return f"question {current}/{n}   [{' '.join(slots)}]"


def build_session(config, bank, rng=None):
    """Score + key items for a fresh session of config['minutes']."""
    rng = rng or random.Random()
    b = Builder(config, bank)
    level = config["level"]
    limit = config["minutes"] * 60
    t = b.cadence(INTRO_BEATS * b.beat)
    count = max(1, int((limit - t) // (b.phrase_beats(LEVELS[level]["notes"]) * b.beat)))
    items = []
    for idx, degrees in enumerate(draw(rng, level, count)):
        window_end, nxt = b.phrase(t, degrees)
        items.append(dict(idx=idx, truth=b.labels(degrees), ask_s=t, window_end_s=window_end,
                          meta=dict(level=level, degrees=degrees, midis=[b.midi(d) for d in degrees])))
        t = nxt
    b.score.seconds = t + OUTRO_SECONDS
    return b.score, items, keymap(config["scale"])


def build_review(config, metas, bank):
    """Each melody once, then sung, nothing asked."""
    b = Builder(config, bank)
    t = INTRO_BEATS * b.beat
    for m in metas:
        t = b.melody(t, m["degrees"]) + b.beat
        t = b.sing(t, m["degrees"]) + REST_BEATS * b.beat
    b.score.seconds = t + OUTRO_SECONDS
    return b.score
