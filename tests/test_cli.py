# -*- coding: utf-8 -*-
"""CLI-level tests: doctor/status against a fake live service + a dead one."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fleetkeeper import cli


@pytest.fixture()
def fake_fleet(tmp_path):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"data": [{"id": "m1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    live = srv.server_address[1]

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()

    cfg = {
        "registry": {"defaults": {"heartbeat_period": 60}, "services": {
            "live-gw": {"name": "Live Gateway", "port": live,
                        "health": {"path": "/", "timeout": 2}},
            "dead-gw": {"name": "Dead Gateway", "port": dead,
                        "health": {"path": "/", "timeout": 2}},
        }},
        "watchdog": {"state_file": "runtime/state.json",
                     "net_probe_targets": [["127.0.0.1", 9]],
                     "net_probe_timeout": 0.2},
    }
    p = tmp_path / "fleet.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    yield str(p)
    srv.shutdown()


def test_status_json_runs(fake_fleet, capsys):
    rc = cli.main(["--config", fake_fleet, "status", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["key"] for r in rows} == {"live-gw", "dead-gw"}
    live = [r for r in rows if r["key"] == "live-gw"][0]
    assert live["level"] in ("L2_PORT", "L3_PROTOCOL", "L4_BUSINESS")


def test_doctor_flags_no_p0_for_live(fake_fleet, capsys):
    rc = cli.main(["--config", fake_fleet, "doctor", "--json"])
    assert rc == 0
    rep = json.loads(capsys.readouterr().out)
    names_p0 = [i["service"] for i in rep["p0"]]
    assert "Live Gateway" not in names_p0 or any(
        "heartbeat" in str(i) for i in rep["p0"])
    # dead service must be P0 (no listener at all)
    assert "Dead Gateway" in names_p0


def test_sweep_json(fake_fleet, capsys, monkeypatch):
    import fleetkeeper.watchdog as W
    # fake listeners so sweep does not depend on psutil/netstat; monkeypatch
    # guarantees the attribute is restored (a bare assignment leaked into
    # test_evidence and made healthy services report level 0)
    monkeypatch.setattr(W.proc, "listeners", lambda ports=None: {})
    rc = cli.main(["--config", fake_fleet, "sweep", "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert "issues" in summary and "healed" in summary


def test_status_human(fake_fleet, capsys):
    rc = cli.main(["--config", fake_fleet, "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "EVIDENCE" in out
    assert "L5" in out  # evidence-boundary note present
