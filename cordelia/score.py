"""A score: what sounds when. Modes write one; the renderer makes a WAV of it.

Three kinds of thing can sound: a note (a pitch, for a duration, on a named
instrument: "tone", or "click" for the metronome tick), a clip (a labelled
sample from the voice bank, e.g. the word "four" or a sung "mi@64"), and a
drone that holds for the whole score. Times are seconds from
sample zero of the rendered file, so a key file written from the same score
lines up exactly with the audio.
"""

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Note:
    t: float
    dur: float
    midi: float
    level: float = 1.0
    voice: str = "tone"


@dataclass
class Clip:
    t: float
    label: str
    level: float = 1.0
    dur: float | None = None         # cut the sample here, with a short fade, when set


@dataclass
class Score:
    seconds: float = 0.0
    notes: list = field(default_factory=list)
    clips: list = field(default_factory=list)
    drone: dict | None = None        # {"midi": 31}: the root the drone sits on
    voice: str = "default"           # the voice bank the clips come from

    def to_json(self, path):
        with open(path, "w") as f:
            json.dump(dict(seconds=self.seconds, drone=self.drone, voice=self.voice,
                           notes=[asdict(n) for n in self.notes],
                           clips=[asdict(c) for c in self.clips]), f, indent=1)

    @classmethod
    def from_json(cls, path):
        with open(path) as f:
            d = json.load(f)
        return cls(seconds=d["seconds"], drone=d.get("drone"), voice=d.get("voice", "default"),
                   notes=[Note(**n) for n in d["notes"]], clips=[Clip(**c) for c in d["clips"]])

    # --- MIDI export: the same score for any soundfont -----------------------

    def to_midi(self, path):
        """A type-0 Standard MIDI File. One channel per instrument, the drone
        as one long note, clips as lyric events carrying their label."""
        channels = {}

        def channel(name):
            return channels.setdefault(name, len(channels))

        events = []     # (tick, order, bytes); order puts note-offs before note-ons
        for n in self.notes:
            ch = channel(n.voice)
            key = max(0, min(127, round(n.midi)))
            vel = max(1, min(127, round(100 * n.level)))
            events.append((_tick(n.t), 1, bytes([0x90 | ch, key, vel])))
            events.append((_tick(n.t + n.dur), 0, bytes([0x80 | ch, key, 0])))
        if self.drone:
            ch = channel("drone")
            key = self.drone["midi"]
            events.append((0, 1, bytes([0x90 | ch, key, 80])))
            events.append((_tick(self.seconds), 0, bytes([0x80 | ch, key, 0])))
        for c in self.clips:
            text = c.label.encode()
            events.append((_tick(c.t), 1, b"\xff\x05" + _vlq(len(text)) + text))
        events.sort(key=lambda e: e[:2])

        track = bytearray(_vlq(0) + b"\xff\x51\x03" + (500000).to_bytes(3, "big"))  # 120 BPM
        last = 0
        for tick, _, data in events:
            track += _vlq(tick - last) + data
            last = tick
        track += _vlq(0) + b"\xff\x2f\x00"
        header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") \
            + (1).to_bytes(2, "big") + TICKS_PER_BEAT.to_bytes(2, "big")
        with open(path, "wb") as f:
            f.write(header + b"MTrk" + len(track).to_bytes(4, "big") + track)


TICKS_PER_BEAT = 480            # at the file's fixed 120 BPM, 960 ticks a second


def _tick(seconds):
    return round(seconds * 2 * TICKS_PER_BEAT)


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))
