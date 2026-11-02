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


def load(con, mode, scale=None, key=None, tag=None):
    sql = """SELECT s.id, s.started_at_ms, s.config, s.tags, s.stopped_at_ms,
                    i.truth, i.meta, a.answer, a.correct, a.latency_ms
             FROM items i JOIN sessions s ON s.id = i.session_id
             LEFT JOIN answers a ON a.session_id = i.session_id AND a.idx = i.idx
             WHERE s.mode = ? AND s.outcome IN ('completed', 'stopped')
               AND i.ask_ms < coalesce(s.stopped_at_ms, 1 << 62)
             ORDER BY s.started_at_ms, i.idx"""
    rows = []
    for sid, started, config, tags, stopped, truth, meta, answer, correct, latency in con.execute(sql, (mode,)):
        cfg, tg = json.loads(config), json.loads(tags)
        if scale and cfg.get("scale") != scale:
            continue
        if key and cfg.get("key", "").lower() != key.lower():
            continue
        if tag and tag not in tg:
            continue
        rows.append(dict(sid=sid, started=started, config=cfg, truth=truth, meta=json.loads(meta),
                         answer=answer, correct=correct, latency=latency, stopped=stopped))
    return rows


def pct(rows, min_n=0):
    n = len(rows)
    if n < min_n or n == 0:
        return "–"
    return f"{100 * sum(1 for r in rows if r['correct'] == 1) / n:.0f}%"


def pass_pct(rows):
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


def report(con, mode="degrees", scale=None, key=None, tag=None, last=DEFAULT_LAST):
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
    print(table(["week of", "sessions", "questions", "right", "pass", "latency"],
                [[w, len({r['sid'] for r in rs}), len(rs), pct(rs, MIN_WEEK), pass_pct(rs), latency(rs)]
                 for w, rs in sorted(weeks.items())]))
    print(f"\nright% is shown once a week has {MIN_WEEK} questions; latency is the median on right answers.\n")

    recent = rows[-last:]
    labels = []
    for r in recent:
        if r["truth"] not in labels:
            labels.append(r["truth"])
    labels.sort(key=lambda l: (int(l.lstrip("b")), l))
    by_truth = {l: [r for r in recent if r["truth"] == l] for l in labels}

    def confusion(rs):
        wrong = [r["answer"] for r in rs if r["correct"] == 0]
        if not wrong:
            return ""
        top = max(set(wrong), key=wrong.count)
        return f"heard as {top} ×{wrong.count(top)}"

    print(f"last {len(recent)} questions, per degree")
    print(table(["degree", "n", "right", "pass", "latency", "most often"],
                [[l, len(rs), pct(rs, MIN_DEGREE), pass_pct(rs), latency(rs), confusion(rs)]
                 for l, rs in by_truth.items()]))

    answers = labels + ["pass", "none"]
    print(f"\nconfusion, last {len(recent)} questions (rows asked, columns answered)")
    matrix = []
    for l in labels:
        counts = {a: 0 for a in answers}
        for r in by_truth[l]:
            a = r["answer"] if r["answer"] is not None else "none"
            counts[a] = counts.get(a, 0) + 1
        matrix.append([l] + [counts[a] or "." for a in answers])
    print(table(["asked"] + answers, matrix))

    registers = {}
    for r in recent:
        registers.setdefault(r["meta"].get("register", "?"), []).append(r)
    if len(registers) > 1:
        print(f"\nby register, last {len(recent)} questions")
        print(table(["register", "n", "right", "pass", "latency"],
                    [[reg, len(rs), pct(rs, MIN_DEGREE), pass_pct(rs), latency(rs)]
                     for reg, rs in sorted(registers.items(), key=lambda kv: ["low", "mid", "high"].index(kv[0])
                                           if kv[0] in ("low", "mid", "high") else 9)]))
