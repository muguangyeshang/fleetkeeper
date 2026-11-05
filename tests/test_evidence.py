# -*- coding: utf-8 -*-
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
