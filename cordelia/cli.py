"""cordelia: ear training that writes down every answer. Bare `cordelia` runs `degrees`. See README.md."""

import argparse
import json
import os
import random
import tempfile

from . import review, scorer, stats, store
from .collector import collect
from .modes import MODES, degrees
from .synth import render
from .voice import VoiceBank


def main(argv=None):
    p = argparse.ArgumentParser(prog="cordelia", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("degrees", help="scale degrees over a drone, the answer spoken after each")
    d.add_argument("--minutes", type=float, default=degrees.DEFAULTS["minutes"])
    d.add_argument("--key", help="tonic: C D E F G A B with # or b (default: random, not the last session's)")
    d.add_argument("--scale", default=degrees.DEFAULTS["scale"], help=" | ".join(degrees.SCALES))
    d.add_argument("--bpm", type=float, default=degrees.DEFAULTS["bpm"])
    d.add_argument("--voice", default=degrees.DEFAULTS["voice"], help="a `say -v ?` voice; default = system voice")
    d.add_argument("--tag", help="tag this and every later session (sticky); 'none' clears")
    d.add_argument("--keep-wav", action="store_true", help="keep the session's WAV in data/sessions/")
    d.add_argument("--midi", metavar="FILE", help="also write the score as a MIDI file")
    d.add_argument("--render", metavar="FILE", help="write the WAV and key file to FILE and stop; nothing is recorded")
    d.add_argument("--seed", type=int, help="draw the same questions again")

    r = sub.add_parser("review", help="replay what a session missed, each with its answer")
    r.add_argument("session", nargs="?", type=int, help="session id (default: the last one)")
    r.add_argument("--midi", metavar="FILE")
    r.add_argument("--render", metavar="FILE", help="write the review WAV to FILE instead of playing it")

    s = sub.add_parser("stats", help="the long view: by week, per degree, confusions")
    s.add_argument("--mode", default="degrees")
    s.add_argument("--scale")
    s.add_argument("--key")
    s.add_argument("--tag")
    s.add_argument("--last", type=int, default=stats.DEFAULT_LAST, help="questions the per-degree tables cover")

    sub.add_parser("rescore", help="rebuild every session's answers from its key presses")
    sub.add_parser("tags", help="show the sticky tags")
    sub.add_parser("data", help="where the database lives")

    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "degrees")          # a bare `cordelia` is a 15-minute degrees session
    a = p.parse_args(argv)
    if a.cmd == "degrees":
        return cmd_degrees(a)
    if a.cmd == "review":
        return review.run(a.session, a.render, a.midi)
    if a.cmd == "stats":
        return stats.report(store.connect(), a.mode, a.scale, a.key, a.tag, a.last)
    if a.cmd == "rescore":
        return cmd_rescore()
    if a.cmd == "tags":
        print(", ".join(tags(store.connect())) or "no tags")
    if a.cmd == "data":
        print(store.data_dir())


def tags(con, flag=None):
    if flag is not None:
        store.set_setting(con, "tags", json.dumps([] if flag == "none" else [t.strip() for t in flag.split(",")]))
    return json.loads(store.setting(con, "tags", "[]"))


def cmd_degrees(a):
    con = store.connect()
    last = store.session(con, store.last_session_id(con) or 0)
    pc = degrees.parse_key(a.key or degrees.random_key(random.Random(a.seed),
                                                       last and last["config"].get("key")))
    if pc is None:
        raise SystemExit(f"--key {a.key!r} is not a note name")
    scale = degrees.parse_scale(a.scale)
    if scale is None:
        raise SystemExit(f"--scale {a.scale!r}: choose from {', '.join(degrees.SCALES)}")
    if not degrees.MIN_BPM <= a.bpm <= degrees.MAX_BPM:
        raise SystemExit(f"--bpm must be between {degrees.MIN_BPM} and {degrees.MAX_BPM}")
    if a.minutes <= 0:
        raise SystemExit("--minutes must be positive")
    config = dict(minutes=a.minutes, key=degrees.NOTE_NAMES[pc], scale=scale, bpm=a.bpm, voice=a.voice)

    bank = VoiceBank(a.voice, store.data_dir())
    bank.ensure(degrees.spoken(scale))
    score, items, keymap = degrees.build_session(config, bank, random.Random(a.seed))
    config["keymap"] = keymap
    config["seed"] = a.seed

    if a.render:
        wav = a.render
        key_path = os.path.splitext(wav)[0] + ".key.jsonl"
        write_key(key_path, config, items)
        if a.midi:
            score.to_midi(a.midi)
        render(score, wav, bank, progress)
        print(f"\r\x1b[K{len(items)} questions, {score.seconds / 60:.1f} min: {wav} and {key_path}")
        return

    tg = tags(con, a.tag)
    sid = store.new_session(con, degrees.NAME, config, tg)
    sdir = store.sessions_dir()
    score.to_json(sdir / f"{sid}.score.json")
    write_key(sdir / f"{sid}.key.jsonl", config, items)
    if a.midi:
        score.to_midi(a.midi)
    tmpdir = None
    if a.keep_wav:
        wav = str(sdir / f"{sid}.wav")
    else:
        tmpdir = tempfile.mkdtemp(prefix="cordelia_")
        wav = os.path.join(tmpdir, "session.wav")
    try:
        render(score, wav, bank, progress)
        store.add_items(con, sid, items)
        print(f"\r\x1b[Ksession {sid} · {degrees.describe(config)} · {len(items)} questions, "
              f"{score.seconds / 60:.0f} min" + (f" · tags: {', '.join(tg)}" if tg else ""))
        print("keys 1-7 answer, space passes, Esc stops.\n")
        ask_ms = [round(it["ask_s"] * 1000) for it in items]
        keystrokes, outcome, stopped_at = collect(
            wav, ask_ms, status=lambda cur, n, k: f"question {cur}/{n}   [{k or ' '}]")
        store.add_keystrokes(con, sid, keystrokes)
        store.replace_answers(con, sid, scorer.score(store.items(con, sid), keystrokes, keymap))
        store.finish_session(con, sid, outcome, stopped_at)
        print(f"saved session {sid} ({outcome}, {stopped_at / 60000:.0f} min).  "
              f"cordelia review {sid} · cordelia stats")
    except BaseException:
        store.finish_session(con, sid, "aborted", None)
        raise
    finally:
        if tmpdir:
            if os.path.exists(wav):
                os.remove(wav)
            os.rmdir(tmpdir)


def cmd_rescore():
    con = store.connect()
    n = 0
    for (sid,) in con.execute("SELECT id FROM sessions WHERE outcome IN ('completed','stopped')").fetchall():
        sess = store.session(con, sid)
        rows = scorer.score(store.items(con, sid), store.keystrokes(con, sid), sess["config"]["keymap"])
        store.replace_answers(con, sid, rows)
        n += 1
    print(f"rescored {n} sessions")


def write_key(path, config, items):
    with open(path, "w") as f:
        f.write(json.dumps(dict(mode=degrees.NAME, config=config)) + "\n")
        for it in items:
            f.write(json.dumps(it) + "\n")


def progress(p):
    print(f"\rrendering {p:4.0%}", end="", flush=True)
