# Notes

## Solfege voices (2026-09-29)

Heard all four voices through `play_solfege_samples.py`.

- **Jennifer is the one I like best** — use her by default for anything
  sung.
- Daisy sounds horrid: a synthesizer voice, robotic, and every note is 22
  cents flat.

About Jennifer's samples (`solfege_samples/jennifer/notes/`):

- C3 to B5 (MIDI 48-83), one 1.75 s file per semitone.
- Fixed-do: every "do" is a C.
- The vowel lands about 135 ms into each file, consonant ahead of it.
- The notes between the major-scale ones use the Sotorrio syllables (ga,
  nu, jur, ki, pe), not di, ri, fi.

## How to join the samples so they sound good (2026-09-29)

This is what `play_solfege_samples.py` does, and it sounds great — reuse
it for anything built on these samples.

- **Don't trim the front of a sample.** The files are already cut well:
  the silence and consonant ahead of the vowel are part of the timing.
  Trimming to where the sound starts made the rhythm limp, because
  consonants differ in length ("s" of so starts at 0 ms, "d" of do at
  about 115 ms) while the vowels all arrive at the same point.
- **Put the vowel on the beat.** Start each file early by the voice's
  vowel offset, so the consonant falls ahead of the beat:

  | Voice    | Vowel offset |
  |----------|--------------|
  | jennifer | 135 ms       |
  | chengu   | 130 ms       |
  | katy     | 105 ms       |
  | daisy    | 40 ms        |

  The offset is the median, over the voice's files, of where the level
  first reaches half its peak.
- **Overlap the notes.** Hold each note until 60 ms past the next vowel,
  fading it out over its last 120 ms (raised-cosine), and mix the two
  where they overlap. Hard cuts at the beat are what sounded choppy.
  With these numbers the level dips about 4 dB where two notes join; with
  a 30 ms overlap it dipped about 9 dB.
- **Let the last note of a phrase ring**: up to 1.5 s, fading over 300 ms.
- **Fade in over 5 ms**, to be safe against clicks.
- **Play everything at 80% level**, for headroom where two notes overlap.
- Nothing else: no pitch correction, time-stretching, equalization or
  loudness matching.

Still open: Katy's recordings last under a second, so at 60 BPM there are
gaps between her notes.
