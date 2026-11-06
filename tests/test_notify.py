# -*- coding: utf-8 -*-
"""Outbound alert channels: real local HTTP receiver, real subprocess."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fleetkeeper import notify


@pytest.fixture()
def receiver():
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append({"path": self.path,
                        "content_type": self.headers.get("Content-Type"),
                        "body": self.rfile.read(n)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:{}/hook".format(srv.server_address[1]), got
    srv.shutdown()
    srv.server_close()
    t.join(timeout=2)


def test_no_notify_config_is_a_noop(tmp_path):
    # Existing installs must not change behaviour just because the module exists.
    assert notify.emit(str(tmp_path), {}, {"title": "abnormal", "message": "x"}) == {}


def test_webhook_posts_redacted_json(tmp_path, receiver):
    url, got = receiver
    wcfg = {"notify": {"channels": ["webhook"], "webhook_url": url,
                       "webhook_timeout": 2.0}}
    event = {"title": "abnormal(x2)", "level": "warning",
             "message": "no process listening; api_key=supersecret123456",
             "issues": ["gw(:8080) no process listening"],
             "network_down": False}
    res = notify.emit(str(tmp_path), wcfg, event)
    assert res["webhook"].startswith("ok HTTP 200")
    assert len(got) == 1
    assert got[0]["content_type"].startswith("application/json")
    payload = json.loads(got[0]["body"].decode("utf-8"))
    assert payload["title"] == "abnormal(x2)"
    assert payload["level"] == "warning"
    assert payload["issues"] == ["gw(:8080) no process listening"]
    assert payload["source"] == "fleetkeeper"
    assert "ts" in payload and "iso" in payload
    assert "supersecret123456" not in got[0]["body"].decode("utf-8")
    assert "[REDACTED]" in payload["message"]


def test_command_channel_really_runs_and_receives_stdin(tmp_path):
    import sys
    out = tmp_path / "hook-out.json"
    script = tmp_path / "hook.py"
    script.write_text(
        "import sys, json\n"
        "data = json.load(sys.stdin)\n"
        "open({!r}, 'w', encoding='utf-8').write(json.dumps(data))\n".format(str(out)),
        encoding="utf-8")
    wcfg = {"notify": {"channels": ["command"],
                       "command": [sys.executable, str(script)],
                       "command_timeout": 10.0}}
    res = notify.emit(str(tmp_path), wcfg, {"title": "recovered", "level": "info",
                                            "message": "all fleet ports healthy"})
    assert res["command"] == "ok exit 0"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["title"] == "recovered"
    assert data["source"] == "fleetkeeper"


def test_legacy_alert_command_alias_is_live(tmp_path):
    """The field existed in example configs but had zero source references.
    It must now actually drive the command channel."""
    import sys
    out = tmp_path / "legacy.json"
    script = tmp_path / "legacy.py"
    script.write_text(
        "import sys, json\n"
        "json.dump(json.load(sys.stdin), open({!r}, 'w', encoding='utf-8'))\n".format(str(out)),
        encoding="utf-8")
    wcfg = {"alert_command": [sys.executable, str(script)]}
    res = notify.emit(str(tmp_path), wcfg, {"title": "abnormal", "message": "down"})
    assert res["command"] == "ok exit 0"
    assert json.loads(out.read_text(encoding="utf-8"))["title"] == "abnormal"


def test_webhook_failure_is_isolated_and_never_echoes_url_token(tmp_path):
    dead = "http://127.0.0.1:9/hook?token=SECRET_TOKEN_123456"
    wcfg = {"notify": {"channels": ["webhook"], "webhook_url": dead,
                       "webhook_timeout": 0.3}}
    res = notify.emit(str(tmp_path), wcfg, {"title": "abnormal", "message": "down"})
    assert res["webhook"].startswith("error ")
    assert "SECRET_TOKEN_123456" not in res["webhook"]


def test_command_failure_is_isolated(tmp_path):
    wcfg = {"notify": {"channels": ["command"], "command": ["fk-no-such-binary"],
                       "command_timeout": 2.0}}
    res = notify.emit(str(tmp_path), wcfg, {"title": "abnormal", "message": "down"})
    assert res["command"].startswith("error ")


def test_file_channel_does_not_duplicate_the_log_line(tmp_path):
    wcfg = {"notify": {"channels": ["file"], "webhook_url": None}}
    res = notify.emit(str(tmp_path), wcfg, {"title": "abnormal", "message": "down"})
    assert res == {"file": "written by alert_log"}
    assert not list(tmp_path.glob("*.log"))


def test_parse_alert_line():
    ev = notify.parse_alert_line("2026-11-06T16:00:00\tnetwork-gate\tWAN unreachable")
    assert ev["title"] == "network-gate"
    assert ev["message"] == "WAN unreachable"
    assert notify.parse_alert_line("bare")["title"] == "alert"


def test_payload_redacts_nested_structures():
    p = notify.build_payload({"nested": {"api_key": "abcdef123456"},
                              "items": ["Bearer AAAAAAAAAAAAAAAA", 1]})
    assert "[REDACTED]" in p["nested"]["api_key"]
    assert "[REDACTED]" in p["items"][0]
