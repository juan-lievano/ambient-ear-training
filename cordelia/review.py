"""Replay what a session got wrong or left unanswered, each question with
its answer, exactly as it sounded. Nothing is asked and nothing is shown
beyond how many there are.
"""

import os
import tempfile

from . import store
from .collector import collect
from .modes import MODES
from .synth import render


def missed(con, sid):
    sql = """SELECT i.idx, i.truth, i.meta FROM items i
             LEFT JOIN answers a ON a.session_id = i.session_id AND a.idx = i.idx
             JOIN sessions s ON s.id = i.session_id
             WHERE i.session_id = ? AND coalesce(a.correct, 0) = 0
               AND i.window_end_ms <= coalesce(s.stopped_at_ms, 1 << 62)
             ORDER BY i.idx"""
    import json
    return [json.loads(meta) for _, _, meta in con.execute(sql, (sid,))]


def run(sid=None, render_to=None, midi=None):
    con = store.connect()
    sid = sid or store.last_session_id(con)
    if sid is None:
        raise SystemExit("no finished session to review")
    sess = store.session(con, sid)
    if sess is None:
        raise SystemExit(f"no session {sid}")
    mode = MODES[sess["mode"]]
    metas = missed(con, sid)
    if not metas:
        print(f"session {sid}: nothing to review")
        return
    bank = mode.bank(sess["config"], store.data_dir())
    score = mode.build_review(sess["config"], metas, bank)
    if midi:
        score.to_midi(midi)
    tmpdir = None
    wav = render_to
    if wav is None:
        tmpdir = tempfile.mkdtemp(prefix="cordelia_")
        wav = os.path.join(tmpdir, "review.wav")
    print(f"review of session {sid} ({mode.describe(sess['config'])}): {score.seconds / 60:.1f} minutes")
    render(score, wav, bank, progress=lambda p: print(f"\rrendering {p:4.0%}", end="", flush=True))
    print("\r\x1b[K", end="")
    if render_to:
        print(f"saved {render_to}")
        return
    try:
        collect(wav, [], status=lambda *_: "playing · Esc to stop")
    finally:
        if tmpdir:
            os.remove(wav)
            os.rmdir(tmpdir)
