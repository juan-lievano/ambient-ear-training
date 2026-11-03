"""SQLite record of every session, question and key press.

The database lives in data/ in the repo checkout (gitignored), so whatever
syncs the checkout syncs the data. CORDELIA_DATA_DIR overrides it.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  started_at_ms INTEGER NOT NULL,      -- wall clock, unix milliseconds
  mode TEXT NOT NULL,
  config TEXT NOT NULL,                -- JSON: what defines the session, incl. its keymap
  tags TEXT NOT NULL DEFAULT '[]',     -- JSON array
  outcome TEXT NOT NULL,               -- rendering / completed / stopped / aborted
  stopped_at_ms INTEGER                -- ms into the audio when it ended
);
CREATE TABLE IF NOT EXISTS items (
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  idx INTEGER NOT NULL,
  truth TEXT NOT NULL,                 -- the right answer, as a keymap label
  ask_ms INTEGER NOT NULL,             -- the question sounds (ms from sample zero)
  window_end_ms INTEGER NOT NULL,      -- the answer is given; presses after this are late
  meta TEXT NOT NULL DEFAULT '{}',     -- JSON: mode-specific facts (degree, octave, midi...)
  PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS keystrokes (
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  t_ms INTEGER NOT NULL,               -- ms since playback was started
  key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (   -- derived from items + keystrokes; `rescore` rebuilds it
  session_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  answer TEXT,                         -- keymap label, or NULL for no press ('pass' in sessions before 2026-11-03)
  correct INTEGER,                     -- 1/0, NULL when there was no answer
  latency_ms INTEGER,                  -- from the question's onset to the press that counted
  n_presses INTEGER NOT NULL,          -- mapped presses inside the window
  n_late INTEGER NOT NULL,             -- mapped presses after the window, before the next question
  PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def data_dir():
    env = os.environ.get("CORDELIA_DATA_DIR")
    root = Path(env) if env else Path(__file__).resolve().parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sessions_dir():
    d = data_dir() / "sessions"
    d.mkdir(exist_ok=True)
    return d


def connect():
    con = sqlite3.connect(data_dir() / "cordelia.db")
    con.execute("PRAGMA journal_mode=DELETE")
    con.executescript(SCHEMA)
    return con


def setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(con, key, value):
    con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
    con.commit()


def new_session(con, mode, config, tags):
    cur = con.execute("INSERT INTO sessions (started_at_ms, mode, config, tags, outcome) VALUES (?,?,?,?,?)",
                      (int(time.time() * 1000), mode, json.dumps(config), json.dumps(tags), "rendering"))
    con.commit()
    return cur.lastrowid


def add_items(con, sid, items):
    con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?)",
                    [(sid, it["idx"], it["truth"], round(it["ask_s"] * 1000),
                      round(it["window_end_s"] * 1000), json.dumps(it.get("meta", {}))) for it in items])
    con.commit()


def add_keystrokes(con, sid, keystrokes):
    con.executemany("INSERT INTO keystrokes VALUES (?,?,?)", [(sid, t, k) for t, k in keystrokes])
    con.commit()


def replace_answers(con, sid, rows):
    con.execute("DELETE FROM answers WHERE session_id=?", (sid,))
    con.executemany("INSERT INTO answers VALUES (?,?,?,?,?,?,?)",
                    [(sid, r["idx"], r["answer"], r["correct"], r["latency_ms"],
                      r["n_presses"], r["n_late"]) for r in rows])
    con.commit()


def finish_session(con, sid, outcome, stopped_at_ms):
    con.execute("UPDATE sessions SET outcome=?, stopped_at_ms=? WHERE id=?", (outcome, stopped_at_ms, sid))
    con.commit()


def session(con, sid):
    row = con.execute("SELECT id, started_at_ms, mode, config, tags, outcome, stopped_at_ms "
                      "FROM sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        return None
    return dict(id=row[0], started_at_ms=row[1], mode=row[2], config=json.loads(row[3]),
                tags=json.loads(row[4]), outcome=row[5], stopped_at_ms=row[6])


def last_session_id(con):
    row = con.execute("SELECT max(id) FROM sessions WHERE outcome IN ('completed','stopped')").fetchone()
    return row[0]


def items(con, sid):
    return [dict(idx=r[0], truth=r[1], ask_ms=r[2], window_end_ms=r[3], meta=json.loads(r[4]))
            for r in con.execute("SELECT idx, truth, ask_ms, window_end_ms, meta FROM items "
                                 "WHERE session_id=? ORDER BY idx", (sid,))]


def keystrokes(con, sid):
    return con.execute("SELECT t_ms, key FROM keystrokes WHERE session_id=? ORDER BY t_ms", (sid,)).fetchall()
