# -*- coding: utf-8 -*-
"""Regression tests for the v0.1.1 review fixes.

Each test here maps to a defect found when the README example was actually
executed, so the examples cannot silently rot again.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fleetkeeper import cli, evidence as E, proc


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _JsonHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = json.dumps({"data": [{"id": "m1"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


# -- defect 1: README/example canary had no port -> ladder pinned at L3 ----

def test_canary_uses_service_port_when_omitted():
    _JsonHandler.hits = 0
    srv = _serve(_JsonHandler)
    port = srv.server_address[1]
    try:
        canary = {"type": "http", "path": "/v1/models",
                  "expect": {"status": 200, "json_path": "data", "min_items": 1}}
        r = E.check_canary(canary, default_port=port)
        assert r["passed"] is True, r
        assert "data 命中 1 项" in r["detail"]
        assert _JsonHandler.hits == 1, "json_path must reuse the probe response"
    finally:
        srv.shutdown()


def test_canary_without_any_port_fails_loudly():
    r = E.check_canary({"type": "http", "path": "/"})
    assert r["passed"] is False
    assert "都没写端口" in r["detail"]


def test_readme_example_reaches_l4(monkeypatch):
    _JsonHandler.hits = 0
    srv = _serve(_JsonHandler)
    port = srv.server_address[1]
    try:
        monkeypatch.setattr(
            E.proc, "find_listener",
            lambda p: [{"pid": 4242, "cmdline": "python fake", "alive": True}])
        # Verbatim README.md config block: canary declares no "port".
        svc = {"name": "LLM Gateway", "port": port,
               "health": {"path": "/", "method": "GET", "timeout": 5},
               "canary": {"type": "http", "path": "/v1/models",
                          "expect": {"status": 200, "json_path": "data",
                                     "min_items": 1}}}
        r = E.evaluate_service(svc)
        assert r["level_name"] == "L4_BUSINESS", r
        # one health probe + one canary request; json_path no longer re-fetches
        assert _JsonHandler.hits == 2
    finally:
        srv.shutdown()


# -- defect 2: unbounded body read ------------------------------------------

def test_probe_read_is_bounded():
    class Big(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"x" * 8192
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *a):
            pass

    srv = _serve(Big)
    port = srv.server_address[1]
    try:
        r = E.http_probe(port, "/", max_bytes=1024)
        assert r["ok"] is True
        assert r["bytes"] == 1024 and len(r["raw"]) == 1024
        c = E.check_canary(
            {"type": "http", "path": "/", "expect": {"json_path": "data"}},
            default_port=port, max_bytes=64)
        assert c["passed"] is False
        assert "解析失败" in c["detail"]
    finally:
        srv.shutdown()


# -- defect 3: defaults were decoration, config path was global state -------

def test_registry_defaults_are_merged():
    cfg = {"registry": {
        "defaults": {"heartbeat_period": 77, "health": {"path": "/x"}},
        "services": {"a": {"port": 1},
                     "b": {"port": 2, "heartbeat_period": 9}}}}
    reg = cli._registry(cfg)
    assert reg["a"]["heartbeat_period"] == 77
    assert reg["a"]["health"]["path"] == "/x"
    assert reg["b"]["heartbeat_period"] == 9       # service key wins
    assert reg["b"]["health"]["path"] == "/x"      # inherited


def test_config_path_does_not_leak_between_runs(tmp_path):
    d1, d2 = tmp_path / "one", tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    cfg = {"registry": {"services": {"gw": {"name": "GW", "port": 1}}}}
    p1, p2 = d1 / "fleet.json", d2 / "fleet.json"
    p1.write_text(json.dumps(cfg), encoding="utf-8")
    p2.write_text(json.dumps(cfg), encoding="utf-8")
    assert cli.main(["--config", str(p1), "heartbeat", "gw"]) == 0
    assert cli.main(["--config", str(p2), "heartbeat", "gw"]) == 0
    assert (d1 / "runtime" / "heartbeat" / "gw.json").exists()
    assert (d2 / "runtime" / "heartbeat" / "gw.json").exists()


# -- defect 4: POSIX netstat PID parsing ------------------------------------

def test_netstat_parse_windows():
    raw = ("  Proto  Local Address     Foreign Address   State      PID\n"
           "  TCP    0.0.0.0:8080     0.0.0.0:0        LISTENING  1234\n"
           "  TCP    [::]:8080        [::]:0           LISTENING  1234")
    assert proc._parse_netstat(raw, True) == {8080: {1234}}


def test_netstat_parse_linux_pid_with_name():
    raw = ("Proto Recv-Q Send-Q Local Address   Foreign Address  State   PID/Program name\n"
           "tcp  0      0      0.0.0.0:8080   0.0.0.0:*      LISTEN  4321/python3")
    assert proc._parse_netstat(raw, False) == {8080: {4321}}


def test_netstat_parse_macos_no_pid():
    raw = "tcp4  0  0  *.80  *.*  LISTEN"
    assert proc._parse_netstat(raw, False) == {80: {-1}}


def test_unknown_pid_does_not_collapse_ladder(monkeypatch):
    _JsonHandler.hits = 0
    srv = _serve(_JsonHandler)
    port = srv.server_address[1]
    try:
        monkeypatch.setattr(
            E.proc, "find_listener",
            lambda p: [{"pid": -1, "cmdline": "", "alive": False}])
        r = E.evaluate_service({"name": "x", "port": port,
                                "health": {"path": "/"}})
        assert r["level"] >= 2, r
        assert r["pids"] == []
        assert any("拿不到 PID" in e for e in r["evidence"])
    finally:
        srv.shutdown()
