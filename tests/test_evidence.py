# -*- coding: utf-8 -*-
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import fleetkeeper.evidence as E


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def test_l0_no_port():
    r = E.evaluate_service({"name": "x"})
    assert r["level"] == 0 and "no port" in r["issues"][0]


def test_l1_dead_port():
    r = E.evaluate_service({"name": "x", "port": 1})
    assert r["level"] <= 1


def test_l2_to_l4_healthy_service():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"data": [{"id": "m1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = _serve(H)
    port = srv.server_address[1]
    try:
        svc = {
            "name": "fake-gw", "port": port,
            "health": {"path": "/health"},
            "canary": {"type": "http", "path": "/v1/models",
                       "expect": {"status": 200, "json_path": "data", "min_items": 1}},
        }
        r = E.evaluate_service(svc)
        # process-liveness depends on psutil/netstat on the box; from L2 up
        # the ladder must hold end to end.
        assert r["level"] >= 3, r
        if r["level"] == 4:
            assert "canary passed" in r["evidence"][-1]
    finally:
        srv.shutdown()


def test_l3_5xx_flags_business_failure():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")

        def log_message(self, *a):
            pass

    srv = _serve(H)
    port = srv.server_address[1]
    try:
        svc = {"name": "sick", "port": port, "health": {"path": "/"}}
        r = E.evaluate_service(svc)
        assert r["level"] == 3
        assert any("5xx" in i for i in r["issues"])
    finally:
        srv.shutdown()


def test_protocol_only_canary_detected():
    svc = {"name": "s", "port": 1234,
           "health": {"path": "/status"},
           "canary": {"type": "http", "path": "/status"}}
    assert E.canary_is_protocol_only(svc) is True
    svc2 = {"name": "s", "port": 1234,
            "health": {"path": "/status"},
            "canary": {"type": "http", "path": "/v1/models"}}
    assert E.canary_is_protocol_only(svc2) is False


def test_canary_expectations():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"items": [1, 2]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = _serve(H)
    port = srv.server_address[1]
    try:
        ok = E.check_canary({"type": "http", "port": port, "path": "/",
                             "expect": {"status": 200, "json_path": "items", "min_items": 1}})
        assert ok["passed"] is True
        bad = E.check_canary({"type": "http", "port": port, "path": "/",
                              "expect": {"status": 200, "json_path": "items", "min_items": 5}})
        assert bad["passed"] is False
    finally:
        srv.shutdown()


class _Gateway(BaseHTTPRequestHandler):
    """Behaves like a real OpenAI-compatible gateway: "/" is 404, the model
    list is served. `broken` makes the canary fail too."""

    broken = False

    def do_GET(self):
        if self.path.startswith("/v1/models") and not self.broken:
            body = json.dumps({"data": [{"id": "m1"}]}).encode()
            code = 200
        else:
            body = b'{"detail": "Not Found"}'
            code = 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _gateway(broken=False):
    _Gateway.broken = broken
    srv = _serve(_Gateway)
    return srv, srv.server_address[1]


def _gw_service(port):
    return {
        "name": "gw", "port": port,
        "health": {"path": "/"},
        "canary": {"type": "http", "path": "/v1/models",
                   "expect": {"status": 200, "json_path": "data", "min_items": 1}},
    }


def test_404_on_root_is_a_note_when_the_business_probe_passed():
    """Real console symptom: eleven healthy L4 cards each carried a red
    'service returns 404' bar, because the health path was "/" on gateways
    that only answer on /v1/models. The ladder said healthy, the card said
    broken. A 4xx the canary has already disproven is advice, not a fault."""
    srv, port = _gateway()
    try:
        r = E.evaluate_service(_gw_service(port))
        if r["level"] < 4:        # no psutil and no netstat PID on this box
            pytest.skip("ladder stopped below L4 on this host")
        assert not [i for i in r["issues"] if "404" in i], r["issues"]
        assert any("404" in n for n in r["notes"]), r["notes"]
    finally:
        srv.shutdown()


def test_404_on_root_stays_an_issue_when_the_canary_also_fails():
    """Same 404, but nothing else proves the service works -- that is a real
    'auth required or path mismatch' and must stay red."""
    srv, port = _gateway(broken=True)
    try:
        r = E.evaluate_service(_gw_service(port))
        assert [i for i in r["issues"] if "404" in i], r["issues"]
        assert not r["notes"] or all("不影响判定" not in n for n in r["notes"])
    finally:
        srv.shutdown()


def test_tcp_type_health_reaches_l3_on_a_real_listener():
    """A MySQL/Redis-style service: health {"type": "tcp"} -- the TCP connect
    is the protocol layer. No HTTP probe runs, so no 404 can brand it broken;
    the ladder honestly stops at L3 (no generic business canary exists)."""
    import socket
    import threading
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)

    def _drain():
        try:
            while True:
                conn, _ = s.accept()
                conn.close()
        except OSError:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    port = s.getsockname()[1]
    try:
        r = E.evaluate_service({"name": "db", "port": port,
                                "health": {"type": "tcp", "timeout": 2}})
        assert r["level"] >= 2, r
        if r["level"] == 3:
            assert any("TCP 握手成功" in e for e in r["evidence"])
            assert not [i for i in r["issues"] if "404" in i or "HTTP" in i]
    finally:
        s.close()


def test_tcp_type_health_reports_a_dead_port():
    r = E.evaluate_service({"name": "db", "port": 1,
                            "health": {"type": "tcp", "timeout": 1}})
    assert r["level"] <= 1
