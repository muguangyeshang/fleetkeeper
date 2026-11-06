# -*- coding: utf-8 -*-
"""Acceptance tests for the console's management layer.

Covers the endpoints added when the dashboard grew past a status page:
aggregate metrics, per-service actions, log/heartbeat access, resident watch,
config export and notification tests. Runs over real loopback HTTP.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

from fleetkeeper import manage, safeio, template, ui


def _req(base, path, payload=None):
    req = None
    if payload is not None:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req or base + path, timeout=30)
        return r.status, dict(r.headers), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


def _json(body):
    return json.loads(body)


@pytest.fixture
def console(tmp_path):
    cfg = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(cfg), template.render(template.default_config(port=59501)))
    server = ui.make_server(str(cfg), 0)
    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_port), str(cfg), server
    finally:
        loop = getattr(server, "fk_watch", None)
        if loop:
            loop.stop()
        server.shutdown()
        server.server_close()
        t.join(timeout=3)


def _two_services(cfg_path):
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    cfg["registry"]["services"] = {
        "alpha": {"name": "Alpha", "port": 59502,
                  "health": {"path": "/", "method": "GET", "timeout": 1},
                  "canary": None, "recovery": None},
        "beta": {"name": "Beta", "port": 59503,
                 "health": {"path": "/", "method": "GET", "timeout": 1},
                 "canary": None, "recovery": None},
    }
    safeio.atomic_write_text(
        cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


# ---- page -----------------------------------------------------------------

def test_page_has_the_full_dashboard(console):
    base, _, _ = console
    code, _, body = _req(base, "/")
    assert code == 200
    for control in ("kpi-total", "kpi-healthy", "kpi-business", "kpi-events",
                    "btn-watch", "watch-interval", "watch-state",
                    "btn-notify-test", "btn-export", "editor", "edit-canary",
                    "alert-list", "alert-count", "tl-window", "tl-kind",
                    "tl-service", "tl-limit", "btn-tl-reload",
                    "log-service", "log-tail", "log-raw", "btn-log",
                    "log-output", "config-text", "btn-config", "uptime",
                    "health-hint"):
        assert 'id="' + control + '"' in body, control
    for text in ("业务证据达成", "开启常驻守护", "查看服务日志", "配置全文",
                 "最近发生了什么", "告警记录", "写入心跳", "编辑",
                 "体检这一个", "五级阶梯"):
        assert text in body, text
    assert "<script src" not in body and "<link " not in body
    assert "https://" not in body and "cdn" not in body.lower()


def test_state_carries_the_aggregate_payload(console):
    base, _, _ = console
    code, _, body = _req(base, "/api/state")
    data = _json(body)
    assert code == 200
    ov = data["overview"]
    for key in ("total", "healthy", "attention", "down", "business",
                "heartbeat_ok", "paused", "event_count", "last_event",
                "last_sweep", "alert_rows", "alert_count", "watch", "uptime"):
        assert key in ov, key
    assert ov["total"] == 1
    assert "watchdog" not in data          # no raw config secrets on the wire
    assert data["service_details"][0]["key"] == "example"
    row = data["services"][0]
    assert row["level_number"] >= 0 and "health_path" in row


# ---- config / overview ----------------------------------------------------

def test_config_route_has_cheap_and_full_views(console):
    base, _, _ = console
    code, _, body = _req(base, "/api/config")
    data = _json(body)
    assert code == 200 and data["is_placeholder"] is True
    assert data["services"][0]["key"] == "example"

    code, _, body = _req(base, "/api/config?view=full")
    text = _json(body)["text"]
    assert code == 200 and "Example Service" in text
    assert "registry" in text and "history_file" in text

    code, headers, body = _req(base, "/api/config?view=full&download=1")
    assert code == 200
    assert "attachment" in headers.get("Content-Disposition", "")
    assert "Example Service" in body


def test_overview_route_aggregates(console):
    base, _, _ = console
    code, _, body = _req(base, "/api/overview")
    ov = _json(body)
    assert code == 200
    assert ov["total"] == 1 and ov["event_count"] >= 0
    assert isinstance(ov["alert_rows"], list)


def test_alerts_route_is_bounded(console):
    base, _, _ = console
    assert _req(base, "/api/alerts")[0] == 200
    assert _json(_req(base, "/api/alerts?limit=5")[2])["rows"] == []
    assert _req(base, "/api/alerts?limit=abc")[0] == 400


# ---- per-service actions --------------------------------------------------

def test_service_action_pause_resume_delete(console):
    base, cfg_path, _ = console
    _two_services(cfg_path)

    code, _, body = _req(base, "/api/service/action",
                         {"key": "alpha", "action": "pause"})
    assert code == 200 and "已暂停" in _json(body)["message"]
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    assert "alpha" not in cfg["registry"]["services"]
    assert "alpha" in cfg["registry"]["paused"]
    assert _json(_req(base, "/api/state")[2])["service_count"] == 1

    code, _, body = _req(base, "/api/service/action",
                         {"key": "alpha", "action": "resume"})
    assert code == 200 and "已恢复" in _json(body)["message"]
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    assert "alpha" in cfg["registry"]["services"]
    assert cfg["registry"]["paused"] == {}

    code, _, body = _req(base, "/api/service/action",
                         {"key": "beta", "action": "delete"})
    assert code == 200 and "已删除" in _json(body)["message"]
    assert "beta" not in json.loads(open(cfg_path, encoding="utf-8").read())[
        "registry"]["services"]

    assert _req(base, "/api/service/action",
                {"key": "alpha", "action": "restart"})[0] == 400
    assert _req(base, "/api/service/action",
                {"key": "ghost", "action": "delete"})[0] == 400


def test_service_update_whitelists_fields(console):
    base, cfg_path, _ = console
    code, _, body = _req(base, "/api/service/update", {
        "key": "example",
        "patch": {"name": "网关", "port": 59510,
                  "health_path": "/healthz", "canary_path": "/v1/models",
                  "heartbeat_period": 30, "log_file": "svc.log"}})
    assert code == 200 and "已更新" in _json(body)["message"]
    svc = json.loads(open(cfg_path, encoding="utf-8").read())[
        "registry"]["services"]["example"]
    assert svc["name"] == "网关" and svc["port"] == 59510
    assert svc["health"]["path"] == "/healthz"
    assert svc["canary"]["path"] == "/v1/models"
    assert svc["canary"]["type"] == "http"
    assert svc["heartbeat_period"] == 30
    assert svc["logs"]["stderr"] == "svc.log"
    assert "recovery" not in svc or svc["recovery"] is None

    for bad in ({"health_path": "http://example.com/x"},
                {"canary_path": "/../etc/passwd"},
                {"health_path": "C:/Windows/win.ini"},
                {"heartbeat_period": 0},
                {"log_file": "../../secret.txt"},
                {"port": 70000},
                {"name": ""}):
        assert _req(base, "/api/service/update",
                    {"key": "example", "patch": bad})[0] == 400, bad
    assert _req(base, "/api/service/update",
                {"key": "example", "patch": {"recovery": "calc.exe"}})[0] == 400
    assert _req(base, "/api/service/update", {"key": "example"})[0] == 400


def test_check_and_heartbeat_routes(console):
    base, cfg_path, _ = console
    code, _, body = _req(base, "/api/check", {"key": "example"})
    assert code == 200 and _json(body)["row"]["key"] == "example"
    assert _req(base, "/api/check", {"key": "nope"})[0] == 400

    code, _, body = _req(base, "/api/heartbeat",
                         {"key": "example", "ok": True, "processed": 7})
    assert code == 200 and "心跳" in _json(body)["message"]
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    hb_dir = os.path.join(os.path.dirname(cfg_path), "runtime", "heartbeat")
    hb = os.path.join(hb_dir, "example.json")
    assert os.path.isfile(hb)
    assert json.loads(open(hb, encoding="utf-8").read())["processed"] == 7
    assert _req(base, "/api/heartbeat", {"key": "example",
                                         "processed": "abc"})[0] == 400


def test_logs_route_redacts_by_default(console):
    base, cfg_path, _ = console
    assert _req(base, "/api/logs")[0] == 400
    assert _json(_req(base, "/api/logs?service=example")[2])["error"]

    secret = "sk-" + "B" * 24
    _req(base, "/api/service/update",
         {"key": "example", "patch": {"log_file": "svc.log"}})
    with open(os.path.join(os.path.dirname(cfg_path), "svc.log"),
              "w", encoding="utf-8") as f:
        f.write("line one\ntoken=" + secret + "\nline three\n")

    code, _, body = _req(base, "/api/logs?service=example&tail=10")
    data = _json(body)
    assert code == 200 and secret not in body
    assert "[REDACTED]" in data["lines"][1]
    assert data["redacted"] >= 1
    assert len(data["lines"]) == 3

    code, _, body = _req(base, "/api/logs?service=example&raw=1")
    assert code == 200 and secret in body
    assert _req(base, "/api/logs?service=example&tail=abc")[0] == 400


# ---- resident watch -------------------------------------------------------

def test_watch_loop_start_status_stop(console):
    base, _, _ = console
    assert _json(_req(base, "/api/watch")[2])["running"] is False
    code, _, body = _req(base, "/api/watch",
                         {"action": "start", "interval": 5})
    assert code == 200 and "已启动" in _json(body)["message"]
    status = _json(_req(base, "/api/watch")[2])
    assert status["running"] is True and status["interval"] == 5
    code, _, body = _req(base, "/api/watch", {"action": "stop"})
    assert code == 200 and "已停止" in _json(body)["message"]
    assert _req(base, "/api/watch", {"action": "restart"})[0] == 400
    assert _req(base, "/api/watch", {"action": "start", "interval": 1})[0] == 400
    assert _req(base, "/api/watch", {"action": "start",
                                     "interval": "abc"})[0] == 400


def test_notify_test_without_channels_explains(console):
    base, _, _ = console
    code, _, body = _req(base, "/api/notify-test", {})
    data = _json(body)
    assert code == 200 and data["configured"] is False
    assert "webhook_url" in data["message"]


# ---- manage module internals ---------------------------------------------

def test_evaluate_rows_is_concurrent_and_keeps_config_order(monkeypatch, tmp_path):
    """Six slow probes must cost one probe, not six."""
    cfg = template.default_config(port=59520)
    cfg["registry"]["services"] = {
        "s{}".format(i): {"name": "S{}".format(i), "port": 59520 + i,
                          "health": {"path": "/", "method": "GET",
                                     "timeout": 1},
                          "canary": None, "recovery": None}
        for i in range(6)
    }
    path = str(tmp_path / "fleet.json")
    safeio.atomic_write_text(path, json.dumps(cfg, ensure_ascii=False))

    real = manage._one_row

    def slow(key, svc, cfg_arg, cfg_path, hb_dir):
        time.sleep(0.20)
        return real(key, svc, cfg_arg, cfg_path, hb_dir)

    monkeypatch.setattr(manage, "_one_row", slow)
    t0 = time.time()
    rows = manage.evaluate_rows(cfg, path, max_workers=8)
    elapsed = time.time() - t0
    assert len(rows) == 6
    assert [r["key"] for r in rows] == ["s0", "s1", "s2", "s3", "s4", "s5"]
    assert elapsed < 0.9, elapsed          # serial would be >= 1.2s


def test_export_config_masks_secrets(tmp_path):
    cfg = template.default_config(port=59530)
    cfg["watchdog"]["notify"]["webhook_url"] = (
        "http://127.0.0.1/hook?token=sk-" + "C" * 24)
    text = manage.export_config(cfg)
    assert "sk-" + "C" * 24 not in text
    assert "registry" in text


def test_service_details_marks_paused(tmp_path):
    path = str(tmp_path / "fleet.json")
    cfg = template.default_config(port=59531)
    cfg["registry"]["services"]["second"] = {
        "name": "Second", "port": 59532,
        "health": {"path": "/", "method": "GET", "timeout": 1},
        "canary": None, "recovery": None}
    safeio.atomic_write_text(path, json.dumps(cfg, ensure_ascii=False))
    assert manage.set_paused(path, "second", True).startswith("已暂停")
    rows = manage.service_details(manage.load(path), path)
    paused = [r for r in rows if r["paused"]]
    assert len(paused) == 1 and paused[0]["key"] == "second"
    assert manage.set_paused(path, "second", False).startswith("已恢复")
    assert not [r for r in manage.service_details(manage.load(path), path)
                if r["paused"]]
