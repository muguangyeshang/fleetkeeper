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


def test_status_html_to_stdout(fake_fleet, capsys):
    rc = cli.main(["--config", fake_fleet, "status", "--html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in out
    assert "<script" not in out.lower()


def test_doctor_html_writes_a_file(fake_fleet, tmp_path, capsys):
    dest = str(tmp_path / "status.html")
    rc = cli.main(["--config", fake_fleet, "doctor", "--html", dest])
    assert rc == 0
    assert "wrote" in capsys.readouterr().err
    text = (tmp_path / "status.html").read_text(encoding="utf-8")
    assert text.lstrip().lower().startswith("<!doctype html>")
    assert "http://" not in text and "<script" not in text.lower()


def test_json_and_html_are_mutually_exclusive(fake_fleet):
    with pytest.raises(SystemExit):
        cli.main(["--config", fake_fleet, "status", "--json", "--html"])


def test_history_empty_then_populated(fake_fleet, capsys, monkeypatch):
    import fleetkeeper.watchdog as W
    monkeypatch.setattr(W.proc, "listeners", lambda ports=None: {})
    rc = cli.main(["--config", fake_fleet, "history", "--last", "all", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []

    assert cli.main(["--config", fake_fleet, "sweep", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert "history_file" in summary
    assert "notify" in summary

    assert cli.main(["--config", fake_fleet, "history", "--last", "all", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) >= 1
    assert rows[0]["kind"] == "sweep"


def test_history_bad_window_is_a_usage_error(fake_fleet, capsys):
    rc = cli.main(["--config", fake_fleet, "history", "--last", "yesterday"])
    assert rc == 2
    assert "invalid --last" in capsys.readouterr().out


def test_init_creates_a_runnable_config(tmp_path, capsys):
    target = tmp_path / "fleet.json"
    rc = cli.main(["init", "--config", str(target), "--port", "59123"])
    assert rc == 0
    assert "wrote" in capsys.readouterr().out
    cfg = json.loads(target.read_text(encoding="utf-8"))
    assert cfg["registry"]["services"]["example"]["port"] == 59123
    assert cfg["watchdog"]["net_probe_targets"] == []   # gate off by default
    assert cfg["watchdog"]["history_retention_days"] == 30
    # and the generated file is immediately usable by the other commands
    assert cli.main(["status", "--config", str(target), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["port"] == 59123


def test_init_never_clobbers_without_force(tmp_path, capsys):
    target = tmp_path / "fleet.json"
    assert cli.main(["init", "--config", str(target)]) == 0
    before = target.read_text(encoding="utf-8")
    assert cli.main(["init", "--config", str(target)]) == 1
    assert "nothing changed" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == before
    assert cli.main(["init", "--config", str(target), "--force"]) == 0


def test_init_with_local_ai_preset(tmp_path, capsys):
    target = tmp_path / "fleet.json"
    rc = cli.main(["init", "--config", str(target), "--preset", "local-ai"])
    assert rc == 0
    assert "preset: local-ai" in capsys.readouterr().out
    cfg = json.loads(target.read_text(encoding="utf-8"))
    services = cfg["registry"]["services"]
    assert set(services) == {"ollama", "gateway", "worker"}
    assert services["ollama"]["canary"]["path"] == "/api/tags"
    # The generated file must be usable by the other commands immediately.
    assert cli.main(["status", "--config", str(target), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3


def test_list_presets_needs_no_config(capsys):
    assert cli.main(["init", "--list-presets"]) == 0
    out = capsys.readouterr().out
    for name in ("empty", "single", "local-ai"):
        assert name in out
    assert "Ollama" in out
    assert "11434" in out


def test_unknown_preset_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "--preset", "no-such-preset"])
    assert exc.value.code == 2


def test_config_works_before_and_after_subcommand(tmp_path):
    target = tmp_path / "fleet.json"
    assert cli.main(["--config", str(target), "init"]) == 0
    assert cli.main(["--config", str(target), "status", "--json"]) == 0
    assert cli.main(["status", "--config", str(target), "--json"]) == 0
    assert cli.main(["doctor", "--config", str(target), "--json"]) == 0


def test_missing_config_points_at_init_command(tmp_path, capsys):
    rc = cli.main(["--config", str(tmp_path / "nope.json"), "doctor"])
    assert rc == 2
    assert "fleetkeeper init" in capsys.readouterr().out


def test_builtin_template_matches_repo_example_keys():
    import os
    from fleetkeeper import template
    example = os.path.join(os.path.dirname(__file__), "..", "config", "fleet.example.json")
    with open(example, encoding="utf-8") as f:
        shipped = json.load(f)
    builtin = template.default_config()
    assert set(builtin) == set(shipped)
    assert set(builtin["watchdog"]) == set(shipped["watchdog"])
    assert set(builtin["watchdog"]["notify"]) == set(shipped["watchdog"]["notify"])
    assert set(builtin["registry"]["services"]) != set()


def test_doctor_reports_the_environment(fake_fleet, capsys):
    """A user who cannot open the console has a different problem from one
    whose service is down; doctor must name the panel's own state too --
    config path, autostart task, whether a webhook would fire."""
    r = cli.main(["--config", fake_fleet, "doctor", "--json"])
    assert r == 0
    env = json.loads(capsys.readouterr().out)["environment"]
    keys = {e["key"]: e for e in env}
    assert keys["config"]["ok"] is True
    assert keys["autostart"]["detail"]          # status text either way
    assert "Webhook" in keys["notify"]["detail"]
