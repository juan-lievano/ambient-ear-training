# ambient ear training

Two ear-training drills that run in the terminal. Pure Python standard
library — no dependencies, no install. Each one renders the whole session
as a single WAV up front (a few seconds) and plays it with macOS's
`afplay`, so the pulse never breaks between rounds, while the terminal
prints the answers in time with the audio.

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

## Notes

Playback is macOS-only (`afplay`); the rendering is plain Python and works
anywhere, so `-o` gives you a WAV to play however you like.
