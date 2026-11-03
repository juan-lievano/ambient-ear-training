"""Match key presses to questions. A pure function of the key file and the
presses, so it can be rerun over every old session when the rule changes.

The presses that count are the last `keys` mapped ones inside the answer
window, from the question's onset until the answer starts sounding;
backspace takes the one before it back. One key is a degree, four a
melody, joined by spaces either way. Presses between the window's end and
the next question are recorded as late and never count.
"""


def typed(presses, keymap):
    """The labels a run of key names spells, backspaces applied."""
    out = []
    for k in presses:
        if k == "backspace":
            if out:
                out.pop()
        elif k in keymap:
            out.append(keymap[k])
    return out


def score(items, keystrokes, keymap, keys=1):
    rows = []
    presses = sorted(keystrokes)
    for i, it in enumerate(items):
        nxt = items[i + 1]["ask_ms"] if i + 1 < len(items) else float("inf")
        inside = [(t, k) for t, k in presses if it["ask_ms"] <= t < it["window_end_ms"]]
        labels = typed([k for _, k in inside], keymap)
        mapped = [(t, k) for t, k in inside if k in keymap]
        late = sum(1 for t, k in presses if it["window_end_ms"] <= t < nxt and k in keymap)
        if labels:
            answer = " ".join(labels[-keys:])
            correct = int(answer == it["truth"])
            latency = mapped[-1][0] - it["ask_ms"]
        else:
            answer, correct, latency = None, None, None
        rows.append(dict(idx=it["idx"], answer=answer, correct=correct, latency_ms=latency,
                         n_presses=len(mapped), n_late=late))
    return rows
