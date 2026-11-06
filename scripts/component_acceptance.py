# -*- coding: utf-8 -*-
"""Component-level acceptance run for every console endpoint and utility.

Uses a private loopback server on a throwaway config, so it can exercise
destructive routes (save / preset / stop) without touching the console the
user currently has open. Prints one PASS/FAIL line per component.

Usage:
    python scripts/component_acceptance.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from fleetkeeper import history, redact, report, safeio, template, ui  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name +
          ((" :: " + str(detail)) if not ok else ""), flush=True)


def req(base, path, payload=None, raw=None, timeout=30):
    url = base + path
    request = None
    if raw is not None:
        request = urllib.request.Request(
            url, data=raw, method="POST",
            headers={"Content-Type": "application/json"})
    elif payload is not None:
        request = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(request or url, timeout=timeout)
        return r.status, r.headers.get("Content-Type", ""), r.read().decode(
            "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(
            "utf-8", "replace")


def jbody(body):
    return json.loads(body)


def run_http(base, cfg, work):
    # ---- read-only routes -------------------------------------------------
    code, ctype, page = req(base, "/")
    check("GET / serves the console", code == 200 and "text/html" in ctype, (code, ctype))
    check("page has every control", all(
        ('id="' + i + '"') in page for i in (
            "btn-check", "btn-sweep", "btn-report", "btn-stop",
            "btn-reconnect", "preset-select", "preset-form", "add",
            "auto", "msg", "cards", "timeline", "conn")), page[:200])
    check("page is fully offline",
          "<script src" not in page and "<link " not in page and
          "https://" not in page and "cdn" not in page.lower())
    check("page explains a dead exe", "重新双击 fleetkeeper.exe" in page and
          "/api/ping" in page)

    code, _, body = req(base, "/api/ping")
    ping = jbody(body) if code == 200 else {}
    check("GET /api/ping liveness", code == 200 and ping.get("ok") is True and
          ping.get("pid", 0) > 0 and "uptime" in ping, (code, body[:120]))

    code, _, body = req(base, "/api/state")
    state = jbody(body) if code == 200 else {}
    check("GET /api/state shape", code == 200 and all(
        k in state for k in ("generated_at", "config_path", "services",
                             "history", "service_count", "is_placeholder",
                             "presets")), (code, body[:160]))
    check("state starts with the placeholder",
          state.get("is_placeholder") is True and state.get("service_count") == 1,
          state.get("service_count"))
    check("state leaks no watchdog block", "watchdog" not in state)

    code, _, body = req(base, "/api/config")
    cfg_view = jbody(body) if code == 200 else {}
    check("GET /api/config", code == 200 and len(cfg_view.get("services", [])) == 1
          and "config_path" in cfg_view, (code, body[:120]))

    code, _, body = req(base, "/api/presets")
    presets = jbody(body).get("presets", []) if code == 200 else []
    check("GET /api/presets lists 3 scenarios",
          [p["name"] for p in presets] == ["empty", "single", "local-ai"],
          [p.get("name") for p in presets])

    code, _, body = req(base, "/api/history?last=24h")
    check("GET /api/history", code == 200 and "rows" in jbody(body)
          and "count" in jbody(body), (code, body[:120]))
    check("history rejects a bad window",
          req(base, "/api/history?last=not-a-window")[0] == 400)
    check("history accepts filters",
          req(base, "/api/history?last=all&limit=5&kind=sweep")[0] == 200)

    code, ctype, body = req(base, "/report")
    check("GET /report is self-contained HTML",
          code == 200 and "text/html" in ctype and
          body.lstrip().lower().startswith("<!doctype html>") and
          "https://" not in body, (code, ctype))
    check("unknown GET route is 404", req(base, "/no-such-route")[0] == 404)

    # ---- write routes -----------------------------------------------------
    code, _, body = req(base, "/api/service", {"name": "网关", "port": 59302})
    check("POST /api/service saves", code == 200 and
          jbody(body).get("ok") is True and "已保存" in jbody(body)["message"],
          (code, body[:140]))
    on_disk = jbody(open(cfg, encoding="utf-8").read())
    svcs = on_disk["registry"]["services"]
    check("first save replaces the placeholder",
          len(svcs) == 1 and list(svcs.values())[0]["name"] == "网关",
          list(svcs.keys()))
    check("saved item gets a health probe",
          list(svcs.values())[0]["health"]["path"] == "/")

    for label, payload in (
            ("port 0", {"name": "x", "port": 0}),
            ("port 70000", {"name": "x", "port": 70000}),
            ("empty name", {"name": "", "port": 1234}),
            ("non-numeric port", {"name": "x", "port": "abc"}),
            ("overlong name", {"name": "n" * 65, "port": 1234})):
        check("service rejects " + label,
              req(base, "/api/service", payload)[0] == 400)
    check("service rejects a non-object JSON body",
          req(base, "/api/service", raw=b"[1,2]")[0] == 400)
    check("service rejects malformed JSON",
          req(base, "/api/service", raw=b"not-json")[0] == 400)
    check("oversized body is 413",
          req(base, "/api/service",
              raw=json.dumps({"name": "x", "port": 1,
                              "pad": "A" * 70000}).encode())[0] == 413)

    code, _, body = req(base, "/api/service", {"name": "网关", "port": 59303})
    svcs = jbody(open(cfg, encoding="utf-8").read())["registry"]["services"]
    check("same-name save updates in place",
          code == 200 and len(svcs) == 1 and
          list(svcs.values())[0]["port"] == 59303, svcs)

    check("preset rejects an unknown name",
          req(base, "/api/preset", {"name": "../../etc/passwd",
                                    "force": True})[0] == 400)
    check("preset rejects an unhashable name",
          req(base, "/api/preset", {"name": {"a": 1}, "force": True})[0] == 400)
    code, _, body = req(base, "/api/preset", {"name": "local-ai"})
    check("preset refuses to clobber saved services", code == 400 and
          "覆盖" in jbody(body).get("error", ""), (code, body[:120]))
    code, _, body = req(base, "/api/preset", {"name": "local-ai",
                                              "force": True})
    check("preset force applies the scenario", code == 200 and
          "3 个服务" in jbody(body).get("message", ""), (code, body[:120]))
    on_disk = jbody(open(cfg, encoding="utf-8").read())
    svcs = on_disk["registry"]["services"]
    check("local-ai has three services", len(svcs) == 3 and
          set(svcs) == {"ollama", "gateway", "worker"}, list(svcs))
    check("Ollama canary is a business path",
          svcs["ollama"]["canary"]["path"] == "/api/tags" and
          svcs["ollama"]["health"]["path"] == "/")

    code, _, body = req(base, "/api/sweep", {})
    sweep = jbody(body) if code == 200 else {}
    check("POST /api/sweep returns the full summary", code == 200 and all(
        k in sweep for k in ("issues", "healed", "alerts", "network_down")),
        (code, body[:160]))
    code, _, body = req(base, "/api/history?last=all")
    rows = jbody(body).get("rows", []) if code == 200 else []
    check("sweep writes a timeline row", len(rows) >= 1 and
          rows[0]["kind"] in ("sweep", "alert"), rows[:1])

    # ---- config-free liveness must survive a broken config ----------------
    backup = os.path.join(work, "fleet.json.bak")
    shutil.copy2(cfg, backup)
    os.remove(cfg)
    check("ping survives a missing config",
          req(base, "/api/ping")[0] == 200)
    check("state fails loudly on a missing config",
          req(base, "/api/state")[0] == 500)
    shutil.copy2(backup, cfg)
    check("state recovers after the config is restored",
          req(base, "/api/state")[0] == 200)

    # ---- shutdown, always last -------------------------------------------
    code, _, body = req(base, "/api/stop", {})
    check("POST /api/stop acknowledges", code == 200 and
          jbody(body).get("ok") is True, (code, body[:120]))


def run_modules(work):
    p = os.path.join(work, "atom.txt")
    safeio.atomic_write_text(p, "hello")
    check("safeio.atomic_write_text", open(p, encoding="utf-8").read() == "hello")
    secret = "sk-" + "A" * 24          # matches the openai_key rule (16+ chars)
    check("redact masks a secret",
          secret not in redact.redact("token=" + secret) and
          redact.scan("token=" + secret) == [("openai_key", 1)])
    check("template renders valid JSON",
          "registry" in jbody(template.render(template.default_config(port=59310))))
    check("all three presets render",
          all("registry" in template.preset_config(n)
              for n in ("empty", "single", "local-ai")))
    html = report.render({"generated_at": "2026-11-06", "services": [],
                          "p0": [], "uncovered": []})
    check("report.render is self-contained",
          html.lstrip().lower().startswith("<!doctype html>") and
          "https://" not in html and "<script src" not in html)
    check("history.db_path_for is under runtime",
          "runtime" in history.db_path_for(work, {}).replace("\\", "/"))


def main():
    work = tempfile.mkdtemp(prefix="fk-components-")
    cfg = os.path.join(work, "fleet.json")
    safeio.atomic_write_text(
        cfg, template.render(template.default_config(port=59301)))
    server = ui.make_server(cfg, 0)
    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = "http://127.0.0.1:{}".format(server.server_port)
    try:
        run_http(base, cfg, work)
        run_modules(work)
        t.join(timeout=5)
        check("stop really terminates the server thread", not t.is_alive())
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)

    passed = sum(1 for _, ok in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("\nTOTAL {}  PASS {}  FAIL {}".format(len(RESULTS), passed, failed),
          flush=True)
    if failed:
        for name, ok in RESULTS:
            if not ok:
                print("  FAILED: " + name)
    print("COMPONENT ACCEPTANCE " + ("PASS" if failed == 0 else "FAIL"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
