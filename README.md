# ambient ear training

Two ear-training drills that run in the terminal. Pure Python standard
library — no dependencies, no install. Each one renders the whole session
as a single WAV up front (a few seconds) and plays it with macOS's
`afplay`, so the pulse never breaks between rounds, while the terminal
prints the answers in time with the audio.

## `play_scale_drone.py` — scale degrees over a drone

```sh
python3 play_scale_drone.py            # 15 minutes in G
python3 play_scale_drone.py 30 D       # 30 minutes in D
python3 play_scale_drone.py 15 G -o drone.wav   # keep the render
```

A drone on the tonic hums under the whole session. The core is the tonic
two octaves below the melody (G2) and its octave (G3); around it the
sub-octave (G1), the fifths (D3/D4) and the upper octaves (G4/D5) fade in
and out on their own very slow LFOs — 35-95 second cycles, random phase
every run — so the pad keeps moving instead of sitting there as one static
chord.

Over that, one random note of the major scale per round, at 80 BPM:

```
beats 1-2   the note — which degree of the scale is it?
beats 3-4   silence  — answer in your head
beats 5-6   the run home: degrees 1-4 resolve DOWN, 5-7 resolve UP
            1 = 1 | 2 = 2 1 | 3 = 3 2 1 | 4 = 4 3 2 1
            5 = 5 6 7 8 | 6 = 6 7 8 | 7 = 7 8
beats 7-8   rest — the tonic rings out over the drone
```

The run has as many notes as it needs, spread evenly over its two beats,
so degree 5 answers in four eighth notes and degree 7 in two quarters —
the rhythm of the answer is itself a hint at how far from home you were.

## `play_chords.py` — chord progressions from real songs

```sh
python3 play_chords.py               # 60 minutes, all genres
python3 play_chords.py 20 jazz       # 20 minutes of jazz standards
python3 play_chords.py 20 random     # random chords, no songs
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
