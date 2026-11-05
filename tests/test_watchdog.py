# -*- coding: utf-8 -*-
"""Watchdog tests: everything runs against a fake fleet on ephemeral ports,
with patched recovery commands (no real processes spawned)."""
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import fleetkeeper.watchdog as W


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _write_cfg(tmp_path, services, extra_watchdog=None):
    cfg = {
        "registry": {"defaults": {}, "services": services},
        "watchdog": dict({
            "state_file": "runtime/state.json",
            "threshold": 1,
            "re_alert_interval": 100,
            "heal_cooldown_seconds": 30,
            "net_probe_targets": [["127.0.0.1", 9], ["127.0.0.1", 10]],
            "net_probe_timeout": 0.2,
            "alert_log": "runtime/alerts.log",
        }, **(extra_watchdog or {})),
    }
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p), tmp_path


def _make_service(port, name="Fake", recovery=None):
    svc = {"name": name, "port": port,
           "health": {"path": "/", "timeout": 2}}
    if recovery:
        svc["recovery"] = recovery
    return svc


@pytest.fixture()
def net_up(monkeypatch):
    monkeypatch.setattr(W.socket, "create_connection",
                        lambda *a, **k: socket.socket())


def test_healthy_fleet_has_no_issues(tmp_path, net_up, monkeypatch):
    srv = _serve()
    port = srv.server_address[1]
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(port)})
    monkeypatch_listeners(W, {port: {12345}}, monkeypatch)
    monkeypatch_tcp(W, True, monkeypatch)
    summary = W.run(cfg)
    assert summary["issues"] == []
    assert summary["network_down"] is False
    srv.shutdown()


def _spy_popen(monkeypatch):
    """Patch watchdog.subprocess.Popen to a no-op recorder."""
    import fleetkeeper.watchdog as _W
    spawned = []
    monkeypatch.setattr(_W.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    return spawned


def test_dead_port_reported_and_healed(tmp_path, net_up, monkeypatch):
    dead = _free_port()
    rec_script = tmp_path / "gw_watchdog.py"
    rec_script.write_text("# fake", encoding="utf-8")
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(dead, recovery=str(rec_script))})
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    spawned = _spy_popen(monkeypatch)

    s1 = W.run(cfg)
    assert any("no process listening" in i for i in s1["issues"])
    assert any(":{}->".format(dead) in h for h in s1["healed"])
    assert spawned, "recovery command must be spawned"

    # within cooldown: recorded, not healed again
    s2 = W.run(cfg)
    assert s2["healed"] == []


def test_duplicate_instance_never_auto_heals(tmp_path, net_up, monkeypatch):
    port = _free_port()
    rec_script = tmp_path / "dup_watchdog.py"
    rec_script.write_text("# fake", encoding="utf-8")
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(port, recovery=str(rec_script))})
    monkeypatch_listeners(W, {port: {111, 222}}, monkeypatch)
    monkeypatch_tcp(W, True, monkeypatch)
    spawned = _spy_popen(monkeypatch)
    s = W.run(cfg)
    assert any("duplicate instance" in i for i in s["issues"])
    assert s["healed"] == []
    assert spawned == []


def test_network_down_suspends_healing(tmp_path, monkeypatch):
    dead = _free_port()
    rec_script = tmp_path / "n_watchdog.py"
    rec_script.write_text("# fake", encoding="utf-8")
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(dead, recovery=str(rec_script))})

    def refused(*a, **k):
        raise OSError("refused")

    # net gate probes fail...
    monkeypatch.setattr(W.socket, "create_connection", refused)
    s = W.run(cfg)
    assert s["network_down"] is True
    assert s["healed"] == []
    assert any("network down" in i for i in s["issues"])

    # ...then recover: gate lifts, healing resumes (cooldown fresh -> still no spawn)
    monkeypatch.setattr(W.socket, "create_connection",
                        lambda *a, **k: socket.socket())
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    _spy_popen(monkeypatch)
    s2 = W.run(cfg)
    assert s2["network_down"] is False
    assert any("network down" not in i for i in s2["issues"])


def test_pause_gate_marks_intentional_offline(tmp_path, net_up, monkeypatch):
    dead = _free_port()
    marker = tmp_path / "PAUSE_LLM"
    marker.write_text("", encoding="utf-8")
    cfg, tmp = _write_cfg(
        tmp_path,
        {"llm": {"name": "Local LLM", "port": dead}},
        extra_watchdog={"pause_gates": {"llm": {"ports": [dead],
                                                "marker_file": str(marker)}}})
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    s = W.run(cfg)
    assert s["issues"] == []
    assert dead in s["gated_ports"]


def test_alert_storm_suppression(tmp_path, net_up, monkeypatch):
    dead = _free_port()
    cfg, tmp = _write_cfg(
        tmp_path, {"gw": _make_service(dead)},
        extra_watchdog={"threshold": 2, "re_alert_interval": 3})
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    lines = []
    real_alert = W.alert_log

    def spy(base, log, title, msg):
        lines.append(title)
        return real_alert(base, log, title, msg)

    monkeypatch.setattr(W, "alert_log", spy)
    for _ in range(6):
        W.run(cfg)
    # threshold=2 -> first alert on 2nd sweep; re-alert every 3 after
    assert len([t for t in lines if t.startswith("abnormal")]) == 2


def test_clock_skew_guard(tmp_path, net_up, monkeypatch):
    dead = _free_port()
    rec_script = tmp_path / "skew_watchdog.py"
    rec_script.write_text("# fake", encoding="utf-8")
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(dead, recovery=str(rec_script))})
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    _spy_popen(monkeypatch)
    W.run(cfg)
    state = json.load(open(os.path.join(str(tmp), "runtime", "state.json"),
                           encoding="utf-8"))
    # poison with a future timestamp
    state["heal_history"][str(dead)] = time.time() + 100000
    json.dump(state, open(os.path.join(str(tmp), "runtime", "state.json"),
                          "w", encoding="utf-8"))
    s = W.run(cfg)
    assert s["healed"], "future timestamp must not permanently block healing"


def test_recovery_cooldown_survives_green_sweep(tmp_path, net_up, monkeypatch):
    dead = _free_port()
    rec_script = tmp_path / "cd_watchdog.py"
    rec_script.write_text("# fake", encoding="utf-8")
    cfg, tmp = _write_cfg(tmp_path, {"gw": _make_service(dead, recovery=str(rec_script))})
    _spy_popen(monkeypatch)

    # dead now
    monkeypatch_listeners(W, {}, monkeypatch)
    monkeypatch_tcp(W, False, monkeypatch)
    W.run(cfg)
    # fleet green: cooldown table must survive
    monkeypatch_listeners(W, {dead: {999}}, monkeypatch)
    monkeypatch_tcp(W, True, monkeypatch)
    W.run(cfg)
    state = json.load(open(os.path.join(str(tmp), "runtime", "state.json"),
                           encoding="utf-8"))
    assert str(dead) in state["heal_history"]


# -- helpers -----------------------------------------------------------

def monkeypatch_listeners(W, mapping, monkeypatch=None):
    """mapping: {port: set(pid)}. Uses pytest monkeypatch so the patch is
    undone after each test -- a bare attribute assignment leaks into other
    test modules (evidence tests hit the real netstat path otherwise)."""
    def fake(ports=None):
        return {p: set(pids) for p, pids in mapping.items()
                if ports is None or p in ports}
    if monkeypatch is not None:
        monkeypatch.setattr(W.proc, "listeners", fake)
    else:
        W.proc.listeners = fake


def monkeypatch_tcp(W, ok, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(W, "_tcp_ok", lambda port, timeout=3.0: ok)
    else:
        W._tcp_ok = lambda port, timeout=3.0: ok


import time  # noqa: E402  (used by clock-skew test)
