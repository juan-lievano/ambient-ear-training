"""Match key presses to questions. A pure function of the key file and the
presses, so it can be rerun over every old session when the rule changes.

The press that counts is the last mapped one inside the answer window, from
the question's onset until the answer starts sounding. Presses between the
window's end and the next question are recorded as late and never count.
"""


def score(items, keystrokes, keymap):
    rows = []
    presses = sorted(keystrokes)
    for i, it in enumerate(items):
        nxt = items[i + 1]["ask_ms"] if i + 1 < len(items) else float("inf")
        inside = [(t, keymap[k]) for t, k in presses
                  if it["ask_ms"] <= t < it["window_end_ms"] and k in keymap]
        late = sum(1 for t, k in presses if it["window_end_ms"] <= t < nxt and k in keymap)
        if inside:
            t, answer = inside[-1]
            correct = None if answer == "pass" else int(answer == it["truth"])
            latency = t - it["ask_ms"]
        else:
            answer, correct, latency = None, None, None
        rows.append(dict(idx=it["idx"], answer=answer, correct=correct, latency_ms=latency,
                         n_presses=len(inside), n_late=late))
    return rows
