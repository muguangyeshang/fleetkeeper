# -*- coding: utf-8 -*-
"""Append-only timeline: three sweeps must remain three rows, not one snapshot."""
import os
import time

import pytest

from fleetkeeper import history as H


def _db(tmp_path):
    return str(tmp_path / "runtime" / "history.sqlite3")


def test_three_sweeps_survive_as_a_timeline(tmp_path):
    db = _db(tmp_path)
    for i in range(3):
        H.append(db, {"kind": "sweep", "level": "warning",
                      "issues": ["gw(:8080) down #{}".format(i)],
                      "healed": [], "net_ok": True, "network_down": False,
                      "message": "sweep {}".format(i)})
    rows = H.query(db, since=None, limit=50)
    assert len(rows) == 3
    assert H.count(db) == 3
    # newest first
    assert rows[0]["message"] == "sweep 2"
    assert rows[2]["message"] == "sweep 0"
    assert rows[0]["issues"] == ["gw(:8080) down #2"]
    assert rows[0]["net_ok"] == 1


def test_alert_events_and_filters(tmp_path):
    db = _db(tmp_path)
    H.append(db, {"kind": "alert", "level": "P0", "title": "network-gate",
                  "message": "WAN unreachable", "network_down": True})
    H.append(db, {"kind": "sweep", "level": "info", "service": "gw", "port": 8080,
                  "message": "healthy"})
    assert len(H.query(db, kind="alert")) == 1
    assert H.query(db, kind="alert")[0]["network_down"] == 1
    assert H.query(db, service="gw")[0]["port"] == 8080
    assert H.query(db, service="nope") == []


def test_service_filter_matches_a_member_of_a_multi_service_row(tmp_path):
    db = _db(tmp_path)
    H.append(db, {"kind": "sweep", "service": "GW, LLM", "message": "two down"})
    assert len(H.query(db, service="LLM")) == 1
    assert len(H.query(db, service="GW")) == 1
    assert H.query(db, service="Nope") == []


def test_missing_db_is_not_an_error(tmp_path):
    assert H.query(_db(tmp_path)) == []
    assert H.count(_db(tmp_path)) == 0


def test_parse_since():
    now = 1_000_000.0
    assert H.parse_since("24h", now) == now - 86400
    assert H.parse_since("30m", now) == now - 1800
    assert H.parse_since("7d", now) == now - 7 * 86400
    assert H.parse_since("90s", now) == now - 90
    assert H.parse_since("2", now) == now - 7200  # bare number = hours
    assert H.parse_since("all", now) is None
    assert H.parse_since(None, now) == now - 86400
    with pytest.raises(ValueError):
        H.parse_since("yesterday", now)


def test_since_window_excludes_old_rows(tmp_path):
    db = _db(tmp_path)
    now = time.time()
    H.append(db, {"kind": "sweep", "ts": now - 3 * 86400, "message": "old"})
    H.append(db, {"kind": "sweep", "ts": now, "message": "new"})
    rows = H.query(db, since=H.parse_since("24h", now))
    assert [r["message"] for r in rows] == ["new"]


def test_retention_prunes_expired_rows(tmp_path):
    db = _db(tmp_path)
    now = time.time()
    H.append(db, {"kind": "sweep", "ts": now - 40 * 86400, "message": "ancient"})
    H.append(db, {"kind": "sweep", "ts": now, "message": "fresh",
                  "__unused": "ignored column"})
    rows = H.query(db, since=None)
    assert [r["message"] for r in rows] == ["fresh"]


def test_sweep_event_levels():
    assert H.sweep_event({"issues": [], "healed": [], "alerts": []})["level"] == "info"
    assert H.sweep_event({"issues": ["down"], "healed": [], "alerts": []})["level"] == "warning"
    assert H.sweep_event({"issues": ["down"], "healed": [], "alerts": [],
                          "network_down": True})["level"] == "P0"


def test_db_path_resolves_against_config_dir(tmp_path):
    p = H.db_path_for(str(tmp_path), {"history_file": "data/events.db"})
    assert os.path.isabs(p)
    assert p.endswith(os.path.join("data", "events.db"))
    assert str(tmp_path) in p
