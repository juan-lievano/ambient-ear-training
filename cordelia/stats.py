"""The long view over the record. Never a single session: the smallest unit
shown is a week, and a percentage is only printed once there is enough
behind it to mean something.
"""

import json
import statistics
from datetime import datetime, timedelta

MIN_WEEK = 100        # questions a week needs before its right% is shown
MIN_DEGREE = 30       # questions a degree needs before its right% is shown
DEFAULT_LAST = 500    # questions the per-degree tables look back over


def load(con, mode=None, scale=None, key=None, tag=None):
    """One row per question asked in a finished session; mode None is every mode."""
    sql = """SELECT s.id, s.started_at_ms, s.mode, s.config, s.tags, s.stopped_at_ms,
                    i.truth, i.meta, a.answer, a.correct, a.latency_ms
             FROM items i JOIN sessions s ON s.id = i.session_id
             LEFT JOIN answers a ON a.session_id = i.session_id AND a.idx = i.idx
             WHERE (? IS NULL OR s.mode = ?) AND s.outcome IN ('completed', 'stopped')
               AND i.window_end_ms <= coalesce(s.stopped_at_ms, 1 << 62)
             ORDER BY s.started_at_ms, i.idx"""
    rows = []
    for sid, started, smode, config, tags, stopped, truth, meta, answer, correct, latency in con.execute(sql, (mode, mode)):
        cfg, tg = json.loads(config), json.loads(tags)
        if scale and cfg.get("scale") != scale:
            continue
        if key and cfg.get("key", "").lower() != key.lower():
            continue
        if tag and tag not in tg:
            continue
        rows.append(dict(sid=sid, started=started, mode=smode, config=cfg, truth=truth, meta=json.loads(meta),
                         answer=answer, correct=correct, latency=latency, stopped=stopped))
    return rows


def pct(rows, min_n=0):
    n = len(rows)
    if n < min_n or n == 0:
        return "–"
    return f"{100 * sum(1 for r in rows if r['correct'] == 1) / n:.0f}%"


def none_pct(rows):
    if not rows:
        return "–"
    return f"{100 * sum(1 for r in rows if r['answer'] in (None, 'pass')) / len(rows):.0f}%"


def latency(rows):
    xs = [r["latency"] for r in rows if r["correct"] == 1 and r["latency"] is not None]
    return f"{statistics.median(xs) / 1000:.2f}s" if len(xs) >= 5 else "–"


def table(headers, rows):
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(c.ljust(w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join(out)


def monday(ms):
    d = datetime.fromtimestamp(ms / 1000).date()
    return d - timedelta(days=d.weekday())


def report(con, mode="all", scale=None, key=None, tag=None, last=DEFAULT_LAST):
    """One mode's report, or (mode "all", the default) the overview across
    modes followed by every mode's report."""
    if mode == "all":
        report_all(con, tag)
        for m in SECTIONS:
            if load(con, m, scale, key, tag):
                print("\n" + "=" * 72 + "\n")
                report(con, m, scale, key, tag, last)
        return
    if mode not in SECTIONS:
        print(f"no stats for mode {mode!r}: {', '.join(SECTIONS)}, or all")
        return
    rows = load(con, mode, scale, key, tag)
    if not rows:
        print("no sessions recorded yet" + (" with those filters" if (scale or key or tag) else ""))
        return
    sessions = {}
    for r in rows:
        sessions[r["sid"]] = r
    hours = sum((r["stopped"] or 0) for r in sessions.values()) / 3.6e6
    filters = ", ".join(f for f in (scale, key, tag and f"tag {tag}") if f)
    print(f"{mode}{' (' + filters + ')' if filters else ''}: {len(sessions)} sessions, "
          f"{len(rows)} questions, {hours:.1f} hours, since {monday(rows[0]['started'])}\n")

    weeks = {}
    for r in rows:
        weeks.setdefault(monday(r["started"]), []).append(r)
    print(table(["week of", "sessions", "questions", "right", "none", "latency"],
                [[w, len({r['sid'] for r in rs}), len(rs), pct(rs, MIN_WEEK), none_pct(rs), latency(rs)]
                 for w, rs in sorted(weeks.items())]))
    print(f"\nright% is shown once a week has {MIN_WEEK} questions; latency is the median on right answers.\n")
    SECTIONS[mode](rows, rows[-last:])


def report_all(con, tag=None):
    """How much, by week and mode: the one view that crosses the drills."""
    rows = load(con, None, tag=tag)
    if not rows:
        print("no sessions recorded yet")
        return
    weeks = {}
    for r in rows:
        weeks.setdefault((monday(r["started"]), r["mode"]), {})[r["sid"]] = r
    out = []
    for (w, mode), sessions in sorted(weeks.items()):
        minutes = sum((r["stopped"] or 0) for r in sessions.values()) / 6e4
        asked = sum(1 for r in rows if r["mode"] == mode and monday(r["started"]) == w)
        out.append([w, mode, len(sessions), asked, f"{minutes:.0f}"])
    sessions = {r["sid"]: r for r in rows}
    total = sum((r["stopped"] or 0) for r in sessions.values()) / 3.6e6
    print(f"all modes: {len(sessions)} sessions, {total:.1f} hours\n")
    print(table(["week of", "mode", "sessions", "questions", "minutes"], out))


def degrees_sections(rows, recent):
    labels = []
    for r in recent:
        if r["truth"] not in labels:
            labels.append(r["truth"])
    labels.sort(key=lambda l: (int(l.lstrip("b")), l))
    by_truth = {l: [r for r in recent if r["truth"] == l] for l in labels}

    print(f"last {len(recent)} questions, per degree")
    print(table(["degree", "n", "right", "none", "latency", "most often"],
                [[l, len(rs), pct(rs, MIN_DEGREE), none_pct(rs), latency(rs), confusion(rs)]
                 for l, rs in by_truth.items()]))

    print(f"\nconfusion, last {len(recent)} questions (rows asked, columns answered)")
    print(confusion_matrix(labels, by_truth))

    registers = {}
    for r in recent:
        registers.setdefault(r["meta"].get("register", "?"), []).append(r)
    if len(registers) > 1:
        print(f"\nby register, last {len(recent)} questions")
        print(table(["register", "n", "right", "none", "latency"],
                    [[reg, len(rs), pct(rs, MIN_DEGREE), none_pct(rs), latency(rs)]
                     for reg, rs in sorted(registers.items(), key=lambda kv: ["low", "mid", "high"].index(kv[0])
                                           if kv[0] in ("low", "mid", "high") else 9)]))


MIN_MELODY = 10       # melodies a level needs before its right% is shown


def practised(rows, level):
    """(minutes, sessions) spent at a level, over every row given."""
    sessions = {r["sid"]: r for r in rows if r["config"].get("level") == level}
    return f"{sum((r['stopped'] or 0) for r in sessions.values()) / 6e4:.0f}", len(sessions)


def melody_sections(rows, recent):
    """Melodies whole, then note by note: the answer is the degrees typed,
    lined up with the degrees asked, so a note is right on its own."""
    levels = {}
    for r in recent:
        levels.setdefault(r["meta"].get("level", "?"), []).append(r)
    print(f"last {len(recent)} melodies, per level (minutes: every session at that level, ever)")
    print(table(["level", "minutes", "sessions", "n", "whole right", "notes right", "none", "latency"],
                [[lv, *practised(rows, lv), len(rs), pct(rs, MIN_MELODY), notes_pct(rs), none_pct(rs), latency(rs)]
                 for lv, rs in sorted(levels.items(), key=str)]))

    notes = note_rows(recent)
    positions = {}
    for nr in notes:
        positions.setdefault(nr["position"], []).append(nr)
    print(f"\nby position in the melody")
    print(table(["note", "n", "right", "most often"],
                [[pos, len(rs), pct(rs, MIN_DEGREE), confusion(rs)] for pos, rs in sorted(positions.items())]))

    labels = []
    for nr in notes:
        if nr["truth"] not in labels:
            labels.append(nr["truth"])
    labels.sort(key=lambda l: (int(l.lstrip("b")), l))
    by_truth = {l: [nr for nr in notes if nr["truth"] == l] for l in labels}
    print(f"\nper degree, every note of every melody")
    print(table(["degree", "n", "right", "none", "most often"],
                [[l, len(rs), pct(rs, MIN_DEGREE), none_pct(rs), confusion(rs)] for l, rs in by_truth.items()]))
    print(f"\nconfusion, note by note (rows asked, columns answered)")
    print(confusion_matrix(labels, by_truth))


def note_rows(rows):
    """A melody row split into one row per note, its answer lined up by position."""
    out = []
    for r in rows:
        asked = r["truth"].split()
        answered = (r["answer"] or "").split()
        for i, t in enumerate(asked):
            a = answered[i] if i < len(answered) else None
            out.append(dict(position=i + 1, truth=t, answer=a, correct=None if a is None else int(a == t),
                            latency=None))
    return out


def notes_pct(rows):
    notes = note_rows(rows)
    return pct(notes, MIN_MELODY * 2) if notes else "–"


def confusion(rs):
    wrong = [r["answer"] for r in rs if r["correct"] == 0]
    if not wrong:
        return ""
    top = max(set(wrong), key=wrong.count)
    return f"heard as {top} ×{wrong.count(top)}"


def confusion_matrix(labels, by_truth):
    answers = labels + ["none"]
    matrix = []
    for l in labels:
        counts = {a: 0 for a in answers}
        for r in by_truth[l]:
            a = r["answer"] if r["answer"] not in (None, "pass") else "none"   # passes, before 2026-11-03
            counts[a] = counts.get(a, 0) + 1
        matrix.append([l] + [counts[a] or "." for a in answers])
    return table(["asked"] + answers, matrix)


SECTIONS = {"degrees": degrees_sections, "melody": melody_sections}
