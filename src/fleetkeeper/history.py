# -*- coding: utf-8 -*-
"""Append-only event timeline (sqlite3, standard library only).

The watchdog's state.json is deliberately a *snapshot*: it holds only the
current sweep. That cannot answer "did port 8080 heal itself at 4am?" -- the
question every operator asks after an incident. This module keeps the stream
of sweeps and alerts without changing the snapshot semantics.

Schema is intentionally flat and JSON-in-a-column for issues/healed: an event
is written once and never updated, so there is nothing to normalize.

Complexity: append is O(log n) on the ts index; the retention DELETE is O(k)
where k = expired rows, and runs on every append (bounded by the index on ts).
"""
import json
import os
import re
import sqlite3
import time

DEFAULT_FILE = "runtime/history.sqlite3"
DEFAULT_RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    iso      TEXT,
    kind     TEXT    NOT NULL,          -- 'sweep' | 'alert'
    level    TEXT,                     -- P0 / warning / info
    service  TEXT,
    port     INTEGER,
    issues   TEXT,                     -- JSON array
    healed   TEXT,                     -- JSON array
    net_ok   INTEGER,
    network_down INTEGER,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(service);
"""

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SINCE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.I)


def db_path_for(cfg_base, wcfg):
    """Resolve the history DB relative to the config file, not the CWD."""
    p = (wcfg or {}).get("history_file") or DEFAULT_FILE
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_base or ".", p))


def parse_since(text, now=None):
    """'24h' / '30m' / '7d' / '90s' / 'all' -> epoch cutoff or None.

    A bare number is read as hours (the most common operator question).
    """
    if text is None:
        return (now or time.time()) - 24 * 3600
    s = str(text).strip().lower()
    if s in ("", "all", "*", "0"):
        return None
    m = _SINCE_RE.match(s)
    if not m:
        raise ValueError("invalid --last value: {!r} (use 24h / 30m / 7d / all)".format(text))
    n = float(m.group(1))
    unit = (m.group(2) or "h").lower()
    return (now or time.time()) - n * _UNITS[unit]


def _connect(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def append(db_path, event, retention_days=DEFAULT_RETENTION_DAYS):
    """Append one event. Returns the row id, or None on failure.

    Failure is swallowed with a returned None: history is diagnostic data,
    and a locked/unwritable DB must never stop a watchdog sweep.
    """
    ev = event or {}
    ts = float(ev.get("ts") or time.time())
    issues = ev.get("issues") or []
    healed = ev.get("healed") or []
    row = (
        ts,
        ev.get("iso") or time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
        str(ev.get("kind") or "sweep"),
        ev.get("level"),
        ev.get("service"),
        ev.get("port"),
        json.dumps(issues, ensure_ascii=False),
        json.dumps(healed, ensure_ascii=False),
        None if ev.get("net_ok") is None else int(bool(ev.get("net_ok"))),
        int(bool(ev.get("network_down"))),
        ev.get("message"),
    )
    try:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO events (ts,iso,kind,level,service,port,issues,healed,"
                "net_ok,network_down,message) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            if retention_days and retention_days > 0:
                cutoff = ts - float(retention_days) * 86400
                conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def query(db_path, since=None, service=None, kind=None, limit=50):
    """Newest-first list of dicts. Missing DB -> [] (never an error)."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = _connect(db_path)
        try:
            sql = "SELECT id,ts,iso,kind,level,service,port,issues,healed,net_ok,network_down,message FROM events WHERE 1=1"
            params = []
            if since is not None:
                sql += " AND ts >= ?"
                params.append(float(since))
            if service:
                # A sweep can involve several services; the `service` column
                # stores them comma-separated, so match a member as well as
                # the whole string.
                sql += " AND (service = ? OR service LIKE ?)"
                params.append(str(service))
                params.append("%" + str(service) + "%")
            if kind:
                sql += " AND kind = ?"
                params.append(str(kind))
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(int(limit))
            cols = ("id", "ts", "iso", "kind", "level", "service", "port",
                    "issues", "healed", "net_ok", "network_down", "message")
            out = []
            for r in conn.execute(sql, params):
                item = dict(zip(cols, r))
                for key in ("issues", "healed"):
                    try:
                        item[key] = json.loads(item[key] or "[]")
                    except (ValueError, TypeError):
                        item[key] = []
                out.append(item)
            return out
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []


def count(db_path):
    if not os.path.exists(db_path):
        return 0
    try:
        conn = _connect(db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return 0


def sweep_event(summary):
    """Turn a watchdog summary into one timeline event."""
    issues = summary.get("issues") or []
    healed = summary.get("healed") or []
    down = bool(summary.get("network_down"))
    if down:
        level = "P0"
    elif issues:
        level = "warning"
    else:
        level = "info"
    return {
        "kind": "sweep",
        "level": level,
        "issues": issues,
        "healed": healed,
        "net_ok": not down,
        "network_down": down,
        "message": "{} issue(s), {} healed, {} alert(s)".format(
            len(issues), len(healed), len(summary.get("alerts") or [])),
    }
