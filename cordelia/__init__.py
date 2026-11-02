"""cordelia: ear-training sessions that record every answer.

A mode writes a score (what sounds when) and a key (what was asked, when,
and what the right answer is). The renderer turns the score into a WAV, the
collector plays it and writes down every key you press with its time, the
scorer matches presses to questions, and stats reads the record. Nothing
reacts while the session plays.
"""
