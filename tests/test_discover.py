# -*- coding: utf-8 -*-
"""Discovery is exercised against real loopback HTTP servers, not mocks of the
probe function: the fingerprints must work on actual status lines and bodies.

The listener map is injected so the tests never enumerate the host's ports.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fleetkeeper import discover, evidence, proc, safeio, template, ui


def _handler_for(kind):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if kind == "ollama" and self.path == "/api/tags":
                payload = b'{"models":[{"name":"qwen2.5:7b"}]}'
            elif kind == "ollama" and self.path == "/":
                payload = b"Ollama is running"
            elif kind in ("openai", "openai2") and self.path == "/v1/models":
                payload = b'{"object":"list","data":[{"id":"local-model"}]}'
            elif kind == "comfyui" and self.path == "/system_stats":
                payload = b'{"system":{"comfyui_version":"0.3"}}'
            elif kind == "webui" and self.path == "/":
                payload = b"<html><title>Open WebUI</title></html>"
            elif kind == "gradio" and self.path == "/":
                payload = b"<html><script src='/gradio.js'></script></html>"
            elif kind == "broken" and self.path == "/":
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            elif self.path == "/":
                payload = b"<html><body>a plain dev server</body></html>"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json"
                             if payload.startswith(b"{") else "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return Handler


@pytest.fixture
def console(tmp_path):
    """A private loopback console. test_ui.py's fixture is module-local, so
    discovery tests need their own server instance."""
    cfg = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(cfg), template.render(template.default_config(port=59231)))
    server = ui.make_server(str(cfg), 0)
    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_port), str(cfg)
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=3)


@pytest.fixture(scope="module")
def services():
    kinds = ("ollama", "openai", "openai2", "comfyui", "webui", "gradio",
             "broken", "unknown")
    servers = {}
    for kind in kinds:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(kind))
        threading.Thread(target=srv.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        servers[kind] = srv
    ports = {k: srv.server_port for k, srv in servers.items()}
    try:
        yield ports
    finally:
        for srv in servers.values():
            srv.shutdown()
            srv.server_close()


@pytest.fixture
def scan(services):
    def run(*kinds):
        return discover.scan(
            ports=[services[k] for k in kinds], listener_map={},
            max_ports=20, timeout=0.6)
    return run


def test_scan_fingerprints_ollama_openai_and_comfyui(scan, services):
    res = scan("ollama", "openai", "comfyui")
    kinds = {c["kind"]: c for c in res["candidates"]}
    assert {"ollama", "openai-compatible", "comfyui"} <= set(kinds)
    assert res["source"] == "explicit"
    assert res["scanned_ports"] == [services[k] for k in
                                    ("ollama", "openai", "comfyui")]
    ollama = kinds["ollama"]
    assert ollama["confidence"] == "high" and ollama["recommended"] is True
    assert ollama["health_path"] == "/"
    assert ollama["canary"]["path"] == "/api/tags"
    # health and canary MUST differ or canary_is_protocol_only pins it at L3
    assert not evidence.canary_is_protocol_only({
        "port": ollama["port"], "health": {"path": ollama["health_path"],
                                           "method": "GET"},
        "canary": ollama["canary"]})
    assert evidence.check_canary(ollama["canary"], timeout=1.0,
                                 default_port=ollama["port"])["passed"] is True
    assert evidence.check_canary(kinds["openai-compatible"]["canary"],
                                 timeout=1.0,
                                 default_port=kinds["openai-compatible"]["port"])["passed"] is True
    assert evidence.check_canary(kinds["comfyui"]["canary"], timeout=1.0,
                                 default_port=kinds["comfyui"]["port"])["passed"] is True
    assert res["elapsed_ms"] >= 0


def test_scan_labels_webui_gradio_and_plain_http(scan):
    res = scan("webui", "gradio", "unknown", "broken")
    by_port = {c["port"]: c for c in res["candidates"]}
    assert any(c["kind"] == "open-webui" and c["confidence"] == "medium"
               for c in by_port.values())
    assert any(c["kind"] == "gradio" for c in by_port.values())
    # A 500 still answers HTTP, so it is listed as an unrecognised service,
    # but never as a product fingerprint.
    assert any(c["kind"] == "http" and c["confidence"] == "low"
               for c in by_port.values())
    assert all(c["recommended"] is False for c in by_port.values()
               if c["confidence"] == "low")


def test_duplicate_products_get_numbered_names(scan):
    res = scan("openai", "openai2")
    names = [c["name"] for c in res["candidates"]]
    assert sorted(names) == ["OpenAI 兼容接口 1", "OpenAI 兼容接口 2"]
    assert all(c["recommended"] for c in res["candidates"])


def test_candidate_names_never_repeat_the_port(scan):
    res = scan("ollama", "openai", "comfyui", "webui", "unknown")
    assert res["candidates"]
    assert all(":{}".format(c["port"]) not in c["name"]
               for c in res["candidates"])


def test_probe_results_never_leak_response_bodies(scan):
    res = scan("ollama")
    c = res["candidates"][0]
    assert "qwen2.5:7b" not in json.dumps(res, ensure_ascii=False)
    assert "raw" not in c and "snippet" not in c
    assert "models 列表" in c["evidence"]


def test_rule_matches_requires_2xx():
    ok = {"ok": True, "status": 200, "raw": b'{"models":[]}'}
    assert discover._rule_matches(ok, {"json_array_key": "models"})
    assert not discover._rule_matches(
        {"ok": True, "status": 500, "raw": b'{"models":[]}'},
        {"json_array_key": "models"})
    assert discover._rule_matches(
        {"ok": True, "status": 404, "raw": b""}, {"any_status": True})
    assert not discover._rule_matches({"ok": False}, {"any_status": True})


def test_process_hint_upgrades_a_generic_http_service(monkeypatch):
    monkeypatch.setattr(
        proc, "proc_info",
        lambda pid: {"pid": pid, "name": "ollama.exe", "alive": True,
                     "cmdline": "ollama serve", "exe": None})
    hint = discover._process_hint(11434, {11434: {4242}})
    assert hint["kind"] == "ollama" and hint["process"] == "ollama.exe"
    results = {"/": {"ok": True, "status": 200, "raw": b"hello"}}
    c = discover._candidate(11434, results, hint)
    assert c["kind"] == "ollama" and c["confidence"] == "high"
    assert "ollama.exe" in c["evidence"]


def test_scan_survives_a_dead_port():
    res = discover.scan(ports=[1], listener_map={}, timeout=0.2)
    assert res["candidates"] == []
    assert res["http_ports"] == []


def _config(tmp_path, port=None):
    path = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(path), template.render(template.default_config(
            port=port or template.DEFAULT_PORT)))
    return str(path)


def test_apply_auto_adds_only_confident_and_drops_placeholder(tmp_path, scan):
    cfg = _config(tmp_path)
    res = scan("ollama", "openai", "comfyui", "unknown")
    out = discover.apply_config(cfg, res["candidates"], auto=True)
    assert out["auto_applied"] == 3
    assert out["placeholder_removed"] is True
    data = json.load(open(cfg, encoding="utf-8"))
    svcs = data["registry"]["services"]
    assert len(svcs) == 3
    assert "example" not in svcs
    assert all(s["health"]["path"] == "/" for s in svcs.values())
    assert all(s["canary"] for s in svcs.values())
    # Nothing from the response body, no command, no webhook, no path injection
    assert "qwen2.5" not in open(cfg, encoding="utf-8").read()
    assert all(s.get("recovery") is None for s in svcs.values())


def test_apply_is_idempotent_and_never_overwrites_a_saved_service(tmp_path, scan):
    cfg = _config(tmp_path)
    res = scan("ollama")
    first = discover.apply_config(cfg, res["candidates"], auto=True)
    assert first["auto_applied"] == 1
    data = json.load(open(cfg, encoding="utf-8"))
    key, svc = next(iter(data["registry"]["services"].items()))
    svc["name"] = "My renamed service"
    safeio.atomic_write_text(cfg, json.dumps(data, ensure_ascii=False, indent=2))
    second = discover.apply_config(cfg, res["candidates"], auto=True)
    assert second["auto_applied"] == 0
    assert len(second["skipped"]) == 1
    data = json.load(open(cfg, encoding="utf-8"))
    assert data["registry"]["services"][key]["name"] == "My renamed service"


def test_apply_with_ids_can_add_a_low_confidence_service(tmp_path, scan):
    cfg = _config(tmp_path)
    res = scan("unknown")
    low = res["candidates"][0]
    out = discover.apply_config(cfg, res["candidates"], ids=[low["id"]])
    assert out["auto_applied"] == 1
    data = json.load(open(cfg, encoding="utf-8"))
    assert "example" not in data["registry"]["services"]
    assert low["port"] in [s["port"] for s in data["registry"]["services"].values()]


def test_apply_creates_a_missing_config(tmp_path, scan):
    cfg = str(tmp_path / "nested" / "fleet.json")
    res = scan("ollama")
    out = discover.apply_config(cfg, res["candidates"], auto=True)
    assert out["auto_applied"] == 1 and os.path.isfile(cfg)


@pytest.mark.parametrize("bad", [
    {"id": "../evil", "kind": "ollama", "port": 8080, "health_path": "/"},
    {"id": "ollama-8080", "kind": "ollama", "port": 99999,
     "health_path": "/"},
    {"id": "ollama-8080", "kind": "shell", "port": 8080, "health_path": "/"},
    {"id": "ollama-8080", "kind": "ollama", "port": 8080,
     "health_path": "http://example.com/x"},
    {"id": "ollama-8080", "kind": "ollama", "port": 8080,
     "health_path": "/", "canary": {"type": "command", "path": "/x"}},
])
def test_apply_rejects_forged_candidates(tmp_path, bad):
    cfg = _config(tmp_path)
    with pytest.raises(ValueError):
        discover.apply_config(cfg, [bad], ids=[bad["id"]])


def test_cli_discover_json_and_apply(tmp_path, services):
    cfg = _config(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath("src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    ports = ",".join(str(services[k]) for k in ("ollama", "openai", "unknown"))
    cmd = [sys.executable, "-m", "fleetkeeper.cli", "--config", cfg,
           "discover", "--ports", ports, "--timeout", "0.6", "--json", "--apply"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)          # exactly ONE JSON document
    assert payload["apply"]["auto_applied"] == 2
    assert len(payload["candidates"]) >= 2
    data = json.load(open(cfg, encoding="utf-8"))
    assert len(data["registry"]["services"]) == 2


def test_cli_init_discover(tmp_path, services):
    cfg = str(tmp_path / "init.json")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath("src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run(
        [sys.executable, "-m", "fleetkeeper.cli", "--config", cfg, "init",
         "--discover", "--discover-timeout", "0.6"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=90)
    assert p.returncode == 0, p.stderr
    assert "autodiscovery:" in p.stdout
    assert os.path.isfile(cfg)


def _fake_scan(services):
    return {
        "scanned_ports": [services["ollama"]],
        "http_ports": [services["ollama"]],
        "elapsed_ms": 1, "source": "test", "auto_applied": 0,
        "recommended": ["ollama-{}".format(services["ollama"])],
        "candidates": [{
            "id": "ollama-{}".format(services["ollama"]),
            "kind": "ollama", "name": "Ollama :{}".format(services["ollama"]),
            "port": services["ollama"], "pid": None, "process": None,
            "confidence": "high", "evidence": "GET /api/tags 返回 models 列表",
            "health_path": "/",
            "canary": {"type": "http", "path": "/api/tags",
                       "expect": {"status": 200, "json_path": "models",
                                  "min_items": 1}},
            "status": 200, "recommended": True,
        }],
    }


def test_ui_discover_get_is_read_only(console, monkeypatch, services):
    base, cfg = console
    monkeypatch.setattr(ui.discover, "scan", lambda **kw: _fake_scan(services))
    before = open(cfg, encoding="utf-8").read()
    code, _, body = _get(base, "/api/discover")
    assert code == 200
    assert json.loads(body)["candidates"][0]["kind"] == "ollama"
    assert open(cfg, encoding="utf-8").read() == before


def _get(base, path):
    import urllib.request
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")


def test_ui_discover_post_auto_writes_the_cached_candidate(
        console, monkeypatch, services):
    base, cfg = console
    monkeypatch.setattr(ui.discover, "scan", lambda **kw: _fake_scan(services))
    _get(base, "/api/discover")                 # populate the server cache
    code, _, body = _req_ui(base, "/api/discover", {"auto": True})
    assert code == 200
    data = json.loads(body)
    assert data["auto_applied"] == 1
    assert "已自动识别并加入" in data["message"]
    svcs = json.load(open(cfg, encoding="utf-8"))["registry"]["services"]
    assert len(svcs) == 1 and "example" not in svcs
    assert services["ollama"] in [s["port"] for s in svcs.values()]


def test_ui_discover_post_rejects_bad_ids(console, monkeypatch, services):
    base, cfg = console
    monkeypatch.setattr(ui.discover, "scan", lambda **kw: _fake_scan(services))
    code, _, body = _req_ui(base, "/api/discover", {"ids": "ollama-1"})
    assert code == 400 and "ids 必须是数组" in body
    assert "example" in json.load(open(cfg, encoding="utf-8"))["registry"]["services"]


def test_ui_discover_post_ignores_a_forged_service_object(
        console, monkeypatch, services):
    base, cfg = console
    monkeypatch.setattr(ui.discover, "scan", lambda **kw: _fake_scan(services))
    code, _, body = _req_ui(base, "/api/discover", {
        "ids": [],
        "service": {"name": "evil", "port": 1,
                    "health": {"path": "http://example.com"},
                    "canary": {"type": "command", "path": "/x"}}})
    assert code == 200
    assert json.loads(body)["auto_applied"] == 0
    assert "example" in json.load(open(cfg, encoding="utf-8"))["registry"]["services"]


def _req_ui(base, path, payload):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        base + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, "", r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode("utf-8")


def test_scan_marks_a_port_that_is_already_configured(services):
    """The console must not offer to add a port that fleet.json already has:
    doing so grew the registry with duplicates on every rescan."""
    port = services["ollama"]
    res = discover.scan(ports=[port], listener_map={}, existing_ports=[port])
    cand = res["candidates"][0]
    assert cand["configured"] is True
    assert cand["recommended"] is False          # never pre-checked


def test_scan_leaves_a_new_port_recommended(services):
    port = services["ollama"]
    res = discover.scan(ports=[port], listener_map={}, existing_ports=[1, 2])
    cand = res["candidates"][0]
    assert cand["configured"] is False
    assert cand["recommended"] is True


def test_ui_discover_get_passes_the_configured_ports(console, monkeypatch):
    base, cfg = console
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"scanned_ports": [], "http_ports": [], "candidates": [],
                "recommended": [], "elapsed_ms": 0, "source": "test",
                "auto_applied": 0}

    monkeypatch.setattr(ui.discover, "scan", fake)
    _get(base, "/api/discover")
    want = [s["port"] for s in
            json.load(open(cfg, encoding="utf-8"))["registry"]["services"].values()]
    assert seen["existing_ports"] == want and want


def test_ui_discover_post_passes_the_configured_ports(
        console, monkeypatch, services):
    base, cfg = console
    seen = []

    def fake(**kw):
        seen.append(kw)
        return _fake_scan(services)

    monkeypatch.setattr(ui.discover, "scan", fake)
    code, _, _ = _req_ui(base, "/api/discover", {"rescan": True, "ids": []})
    assert code == 200
    want = [s["port"] for s in
            json.load(open(cfg, encoding="utf-8"))["registry"]["services"].values()]
    assert seen and seen[-1]["existing_ports"] == want and want
