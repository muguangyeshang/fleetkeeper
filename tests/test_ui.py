# -*- coding: utf-8 -*-
"""The browser console is tested over real loopback HTTP, not by calling its
handler methods directly: routing, headers, JSON encoding and shutdown all
have to work the way a browser actually sees them."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from fleetkeeper import cli, safeio, template, ui


def _req(base, path, payload=None):
    url = base + path
    req = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req or url, timeout=30)
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")


@pytest.fixture
def console(tmp_path):
    cfg = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(cfg), template.render(template.default_config(port=59201)))
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


def test_root_page_is_chinese_and_fully_offline(console):
    base, _ = console
    code, ctype, body = _req(base, "/")
    assert code == 200
    assert "text/html" in ctype
    assert "服务守护面板" in body
    assert "立即体检" in body
    assert "添加 / 修改服务" in body
    assert "<script src" not in body
    assert "<link " not in body
    assert "cdn" not in body.lower()
    assert "https://" not in body


def test_state_payload_matches_cli_ladder(console):
    base, cfg_path = console
    code, _, body = _req(base, "/api/state")
    data = json.loads(body)
    assert code == 200
    assert data["config_path"].endswith("fleet.json")
    assert data["service_count"] == 1
    row = data["services"][0]
    assert row["name"] == "Example Service"
    assert row["port"] == 59201
    assert row["level"].startswith("L")
    assert isinstance(row["issues"], list)
    assert isinstance(data["history"], list)
    # The config is never returned wholesale: webhook URLs / commands stay off
    # the wire even if the user configured them.
    assert "watchdog" not in data


def test_first_save_replaces_the_placeholder(console):
    base, cfg_path = console
    code, _, body = _req(base, "/api/service", {"name": "网关", "port": 59202})
    assert code == 200
    assert json.loads(body)["ok"] is True
    assert "已保存" in json.loads(body)["message"]

    on_disk = json.loads(open(cfg_path, encoding="utf-8").read())
    services = on_disk["registry"]["services"]
    assert list(services) == ["example"]       # replaced, not duplicated
    assert services["example"]["name"] == "网关"
    assert services["example"]["port"] == 59202

    _, _, body = _req(base, "/api/config")
    listed = json.loads(body)["services"]
    assert len(listed) == 1
    assert listed[0]["name"] == "网关"

    _, _, body = _req(base, "/api/state")
    assert json.loads(body)["services"][0]["name"] == "网关"


def test_save_rejects_bad_ports_and_names(console):
    base, _ = console
    for payload in ({"name": "x", "port": 0},
                    {"name": "x", "port": 70000},
                    {"name": "", "port": 1234},
                    {"name": "x", "port": "not-a-number"}):
        code, _, body = _req(base, "/api/service", payload)
        assert code == 400
        assert "error" in json.loads(body)


def test_root_page_offers_a_first_run_scenario_picker(console):
    base, _ = console
    code, _, body = _req(base, "/")
    assert code == 200
    assert "第一次用，先选一个场景" in body
    assert 'id="preset-select"' in body
    assert "套用场景" in body
    # A red card on first run is explained, not left looking like a crash.
    assert "示例占位服务" in body
    assert "<script src" not in body


def test_state_reports_placeholder_and_presets(console):
    base, _ = console
    _, _, body = _req(base, "/api/state")
    data = json.loads(body)
    assert data["is_placeholder"] is True
    assert [p["name"] for p in data["presets"]] == ["empty", "single", "local-ai"]
    _, _, body = _req(base, "/api/presets")
    assert json.loads(body)["presets"] == data["presets"]


def test_preset_route_replaces_the_placeholder(console):
    base, cfg_path = console
    code, _, body = _req(base, "/api/preset", {"name": "local-ai", "force": True})
    assert code == 200
    assert "3 个服务" in json.loads(body)["message"]

    _, _, body = _req(base, "/api/state")
    data = json.loads(body)
    assert data["service_count"] == 3
    assert data["is_placeholder"] is False
    assert [s["name"] for s in data["services"]] == [
        "Ollama", "AI Gateway", "Queue Worker"]

    on_disk = json.loads(open(cfg_path, encoding="utf-8").read())
    assert set(on_disk["registry"]["services"]) == {"ollama", "gateway", "worker"}
    assert on_disk["registry"]["services"]["ollama"]["canary"]["path"] == "/api/tags"


def test_preset_route_refuses_to_clobber_saved_services(console):
    base, _ = console
    _req(base, "/api/service", {"name": "我的网关", "port": 59202})
    code, _, body = _req(base, "/api/preset", {"name": "local-ai"})
    assert code == 400
    assert "覆盖" in json.loads(body)["error"]
    _, _, body = _req(base, "/api/state")
    assert json.loads(body)["services"][0]["name"] == "我的网关"
    # An explicit force (the browser's confirm()) is the only way through.
    code, _, _ = _req(base, "/api/preset", {"name": "local-ai", "force": True})
    assert code == 200


def test_preset_route_rejects_unknown_and_unhashable_names(console):
    base, _ = console
    for bad in ("../../etc/passwd", {"a": 1}, [1, 2]):
        code, _, body = _req(base, "/api/preset", {"name": bad, "force": True})
        assert code == 400
        assert "没有这个场景" in json.loads(body)["error"]


def test_history_config_and_report_routes(console):
    base, _ = console
    code, _, body = _req(base, "/api/history?last=24h")
    assert code == 200
    assert json.loads(body)["rows"] == []

    code, _, body = _req(base, "/api/history?last=not-a-window")
    assert code == 400

    code, _, body = _req(base, "/api/config")
    assert code == 200 and "services" in json.loads(body)

    code, ctype, body = _req(base, "/report")
    assert code == 200 and "text/html" in ctype
    assert body.lstrip().lower().startswith("<!doctype html>")

    code, _, body = _req(base, "/no-such-route")
    assert code == 404


def test_sweep_button_records_a_timeline_event(console):
    base, _ = console
    code, _, body = _req(base, "/api/sweep", {})
    summary = json.loads(body)
    assert code == 200
    for key in ("issues", "healed", "alerts", "network_down"):
        assert key in summary

    code, _, body = _req(base, "/api/history?last=all")
    rows = json.loads(body)["rows"]
    assert code == 200
    assert len(rows) >= 1
    assert rows[0]["kind"] in ("sweep", "alert")


def test_stop_route_shuts_the_server_down(tmp_path):
    cfg = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(cfg), template.render(template.default_config(port=59203)))
    server = ui.make_server(str(cfg), 0)
    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = "http://127.0.0.1:{}".format(server.server_port)
    code, _, body = _req(base, "/api/stop", {})
    assert code == 200 and json.loads(body)["ok"] is True
    t.join(timeout=3)
    assert not t.is_alive()
    server.server_close()


def test_ensure_config_creates_runnable_file_on_first_run(tmp_path):
    cfg = tmp_path / "nested" / "fleet.json"
    assert ui.ensure_config(str(cfg)) is True
    assert cfg.exists()
    parsed = json.loads(cfg.read_text(encoding="utf-8"))
    assert "example" in parsed["registry"]["services"]
    assert ui.ensure_config(str(cfg)) is False


def test_serve_autocreates_config_and_opens_browser(tmp_path, monkeypatch):
    cfg = tmp_path / "fleet.json"
    opened = []

    class FakeServer(object):
        server_port = 51234

        def serve_forever(self, poll_interval=0.5):
            return None

        def server_close(self):
            return None

    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(ui, "make_server", lambda path, port=0: FakeServer())
    assert ui.serve(str(cfg)) == 0
    assert cfg.exists()                       # first run needs no init command
    assert opened == ["http://127.0.0.1:51234/"]

    opened[:] = []
    monkeypatch.setenv("FLEETKEEPER_NO_BROWSER", "1")
    assert ui.serve(str(cfg)) == 0
    assert opened == []                       # headless switch works


def test_no_arguments_opens_the_console(tmp_path, monkeypatch):
    seen = {}

    def fake_serve(path, port=0, open_browser=True):
        seen.update(path=path, port=port, open_browser=open_browser)
        return 0

    monkeypatch.setattr(ui, "serve", fake_serve)
    assert cli.main([]) == 0
    assert seen["port"] == 0
    assert seen["open_browser"] is True
    assert seen["path"].endswith("fleet.json")


def test_start_no_browser_flag(tmp_path, monkeypatch):
    seen = {}

    def fake_serve(path, port=0, open_browser=True):
        seen.update(port=port, open_browser=open_browser)
        return 0

    monkeypatch.setattr(ui, "serve", fake_serve)
    cfg = str(tmp_path / "fleet.json")
    assert cli.main(["start", "--config", cfg, "--port", "8765",
                     "--no-browser"]) == 0
    assert seen == {"port": 8765, "open_browser": False}
