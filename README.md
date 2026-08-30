# ambient ear training

Two ear-training drills and one ambient piece, all running in the
terminal. Pure Python standard library — no dependencies, no install.
Each one renders the whole session as a single WAV up front (a few
seconds) and plays it with macOS's `afplay`, so the sound never breaks
between rounds, while the terminal prints along with the audio.

## `play_scale_drone.py` — scale degrees over a drone

```sh
python3 play_scale_drone.py                          # 15 min in G major, 80 BPM
python3 play_scale_drone.py --minutes 30 --key D
python3 play_scale_drone.py --key A --scale natural-minor
python3 play_scale_drone.py --key A --scale harmonic-minor --bpm 60
python3 play_scale_drone.py --output drone.wav       # keep the render
```

Every setting is a named flag: `--minutes`, `--key`, `--scale`, `--bpm`,
`--output`, `--help`. Anything the script can't accept — an unknown flag, a
scale it doesn't have, a tempo out of range — prints what was wrong followed
by the full list of arguments and their defaults, and exits 2.

A drone on the tonic hums under the whole session. The core is the tonic
two octaves below the melody (G2) and its octave (G3); around it the
sub-octave (G1), the fifths (D3/D4) and the upper octaves (G4/D5) fade in
and out on their own very slow LFOs — 35-95 second cycles, random phase
every run — so the pad keeps moving instead of sitting there as one static
chord.

Over that, one random note of the scale per round, at 80 BPM (`--bpm`):

```
beats 1-2   the note — which degree of the scale is it?
beats 3-4   silence  — answer in your head
beats 5-6   the run home
beats 7-8   rest — the tonic rings out over the drone
```

The run has as many notes as it needs, spread evenly over its two beats,
so a four-note answer comes out in eighths and a two-note one in quarters —
the rhythm of the answer is itself a hint at how far from home you were.

`--minutes` is the length of the session and it is honoured whatever the
tempo: `--bpm 50` doesn't make the session longer, it fits fewer, slower
questions into the same minutes.

### Register

Questions aren't confined to one octave. They reach half an octave past
the middle register at each end — in G, `D4` to `C6` — which is degrees
5 6 7 of the octave below, all seven of the middle octave, and degrees
1 2 3 4 of the octave above. The register is not the question: the same
degree can turn up low or high and the answer is the same, so what you
are learning is the degree itself rather than a fixed pitch. The answer
run stays in whichever register the question came from, so a low 4 walks
down to the low tonic.

### The three scales

In **major** every degree walks home by step, down from 1-4 and up from
5-7:

```
1 = 1 | 2 = 2 1 | 3 = 3 2 1 | 4 = 4 3 2 1
5 = 5 6 7 8 | 6 = 6 7 8 | 7 = 7 8
```

Both **minors** follow each degree's actual pull instead of walking the
scale in both directions. ♭6 falls its semitone to 5 and then home, 5
leaps home rather than crawling down through four notes, and the 7th
resolves up to the octave:

```
1 = 1 | 2 = 2 1 | b3 = b3 2 1 | 4 = 4 b3 2 1
5 = 5 1 | b6 = b6 5 1 | b7 = b7 8      (natural minor)
                        7 = 7 8        (harmonic minor)
```

The raised 7th is the only note `natural-minor` and `harmonic-minor`
disagree about, and hearing which one is sounding is most of the point of
having both. Minor isn't one scale in practice — the 7th is variable.
Natural minor's ♭7 is what nearly all the minor-key music you actually
listen to uses melodically; harmonic minor's raised 7 is the leading tone
that gives you a real V–i pull. Spend most of your time in
`natural-minor`, and switch to `harmonic-minor` when you want the leading
tone drilled.

## `play_chords.py` — chord progressions from real songs

Same flag conventions as the drone drill: `--minutes`, `--genre`, `--bpm`,
`--output`, `--help`, and a bad argument prints the full list and exits 2.

```sh
python3 play_chords.py                                # 60 minutes, all genres
python3 play_chords.py --minutes 20 --genre jazz      # jazz standards
python3 play_chords.py --minutes 20 --genre random    # random chords, no songs
python3 play_chords.py --genre rock --bpm 75          # take it slower
```

Genres are the `.jsonl` files in `progressions/` (one song per line:
`{"title": ..., "key": ..., "chords": [...]}`) — add a genre by adding a
file. Each song gets a quick preview of the whole progression to plant the
key, then loops as the exercise. Each round: 4 beats of block chord, 4
beats arpeggiated, 2 beats to guess, then the answer plays as part of the
music:

- **beats 1-2 — where in the key:** a scale run from the chord's root to
  the nearest tonic. Count the notes. Going down: 1 = I, 2 = ii, 3 = iii,
  4 = IV. Going up: 4 = V, 3 = vi, 2 = vii.
- **beats 3-4 — what kind of chord:** a tick rhythm in eighth notes.
  `x...` major, `x.x.` minor, `xx..` dom 7, `x..x` maj 7, `x.xx` min 7,
  `xxx.` m7b5, `xxxx` dim 7.

## `play_ambient_drone.py` — one seventh chord, nothing to answer

```sh
python3 play_ambient_drone.py                          # 30 min, random chord
python3 play_ambient_drone.py --minutes 60
python3 play_ambient_drone.py --key A --chord min7
python3 play_ambient_drone.py --bpm 10                 # slower still
python3 play_ambient_drone.py --output ambient.wav     # keep the render
```

Not a drill: nothing is asked, nothing is printed you'd want to look away
from. One seventh chord — `maj7`, `min7` or `dom7` — on one root, both
drawn at random every run unless `--key` and `--chord` say otherwise.

Underneath is the same kind of drone as the scale drill: the root in
octaves 2 and 3 as the core, with the sub-octave and the fifths breathing
in and out on their own 35-95 second LFOs. That drone is already three
octaves of root and two of fifth, which is why the pad never plays the
root — everything it adds is a part of the chord the drone hasn't got.

Over it the chord tones come and go one at a time:

- one of **3 5 7** is drawn uniformly from the bag and given a random
  octave — 3 to 5 by default (`--lowest-octave`, `--highest-octave`). If
  that exact tone is already sounding it takes one of its free octaves
  instead.
- the **wait until the next tone** is drawn too, 2 to 6 beats, so it
  averages four without ever settling into a period.
- each tone lasts **18 to 36 beats** and spends a third of that fading in,
  a third held at full level, and a third fading out — so a handful are
  always overlapping, each at a different point in its own arc.
- when **3, 5 and 7 are set to stand at full level at the same moment** —
  the whole seventh at its loudest, since the drone holds the root — the
  bag closes: nothing new enters until every one of them has faded away
  and only the drone is left. Merely overlapping doesn't count; they have
  to meet in their middle thirds, which is the one moment you hear the
  chord whole. So the piece fills up to that and empties back out to the
  root, about every four minutes, at a rate nobody chose.

Over 40 simulated hours that comes out at 128 tones an hour and a mean of
**3.56 sounding at once**: bare drone under 4% of the time, one or two
tones 23%, three or four 40%, five or six 30%, and seven or more 3% —
right up to all nine the register has room for. At full level it is much
sparser, a mean of 1.19, which is the point: most of what is sounding at
any moment is on its way up or down. Clear-outs come about every four
minutes. `SPAWN_BEATS` and `TONE_BEATS` in the script are the levers if
you want it thicker or thinner.

Beats here are only a unit of time — nothing is struck, so there's no
pulse to hear. At the default `--bpm 16` a beat is 3.75 seconds, which
puts a tone every 8-23 seconds, each lasting between one minute and two
and a quarter. A session shorter than one tone is just the drone, and the
script says so before it renders.

How loud a tone is comes from its own pitch, not from which octave it was
filed under: amplitude falls as `(lowest / f) ** 1.4`, about 8 dB per
octave, applied within an octave as well as across them — so the 7th of
the chord is always softer than the root under it, and in G minor 7 the
tones run from the low ♭3 at full level through the octave up at 38% to
the ♭7 two octaves up at 8%. The ear hears the top of that range far more
keenly than the bottom, and a fall that steep is what it takes for high
tones to actually sound soft rather than merely measure soft. They also
lose their harmonics on the way up, thinning towards a plain sine, so
nothing up there pushes to the front.

## Notes

Playback is macOS-only (`afplay`); the rendering is plain Python and works
anywhere, so `-o` gives you a WAV to play however you like.
