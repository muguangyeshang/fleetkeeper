# -*- coding: utf-8 -*-
"""Local browser console for people who never open a terminal.

`fleetkeeper start` (or double-clicking the one-file exe) does three things
without any question being asked:

  1. creates a runnable fleet.json next to the program if none exists
  2. serves a single self-contained Chinese page on 127.0.0.1 only
  3. opens the default browser at that page

The page replaces every CLI command with a button. It is pure standard library:
http.server + urllib + webbrowser, no CDN, no external assets, no javascript
framework. Only the loopback interface is bound; the console is not reachable
from the network. All payloads crossing the HTTP boundary are redacted.
"""
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import cli as cli_mod
from . import discover, history, manage, redact, safeio, template, watchdog
from . import report as report_mod

MAX_BODY_BYTES = 65536
POLL_SECONDS = 5
PLACEHOLDER_KEY = "example"
PLACEHOLDER_NAME = "Example Service"

# The complete self-contained HTML page lives in page.py.
# Re-exported here so `from fleetkeeper.ui import PAGE` keeps working.
from .page import PAGE  # noqa: E402


def ensure_config(path):
    """Create a runnable config on first run. Returns True if it was created.

    A wheel install ships no config/ directory. Without this, a new user sees
    a file-not-found error before ever reaching a working screen.
    """
    path = os.path.abspath(path)
    if os.path.exists(path):
        return False
    safeio.atomic_write_text(path, template.render(template.default_config()))
    return True


def default_config_path():
    """A frozen exe must write next to itself, not into the launcher's CWD.

    Double-clicking an exe from Explorer usually gives CWD = C:\\Windows or
    the desktop. Writing there produces a config the user can never find.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                            "fleet.json")
    return os.path.abspath("config/fleet.json")


def _load(path):
    with open(os.path.abspath(path), "r", encoding="utf-8") as f:
        return json.load(f)


def _services_summary(path):
    """Same ladder as the CLI, collected concurrently in manage.py.

    A serial loop made an 11-service dashboard take longer than the browser's
    first paint, which is why the page looked empty on open.
    """
    cfg = _load(path)
    return cfg, manage.evaluate_rows(cfg, path)


def _configured_ports(path):
    """Ports already saved in fleet.json. A broken config returns [] on
    purpose: discovery must still work when the config cannot be parsed."""
    try:
        cfg = _load(path)
        out = []
        for svc in cli_mod._registry(cfg).values():
            try:
                out.append(int(svc.get("port")))
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        return []


def _history_rows(cfg, path, limit=12):
    db = history.db_path_for(os.path.dirname(os.path.abspath(path)),
                             cfg.get("watchdog") or {})
    return history.query(db, since=history.parse_since("7d"), limit=limit)


def _is_placeholder(cfg):
    """True while the config is still the untouched first-run template."""
    svcs = (cfg.get("registry") or {}).get("services") or {}
    return (list(svcs.keys()) == [PLACEHOLDER_KEY]
            and (svcs.get(PLACEHOLDER_KEY) or {}).get("name") == PLACEHOLDER_NAME)


def apply_preset(path, name, force=False):
    """Replace the service list with a named scenario.

    Refusing to overwrite by default is the important part: a user who already
    saved their own ports must not lose them to one click. The console sends
    force=True only after a browser-side confirm().
    """
    cfg = _load(path)
    if not force and not _is_placeholder(cfg):
        raise ValueError("配置里已经有服务了，套用场景会覆盖当前列表")
    # A JSON object/array here would blow up on the dict lookup with TypeError;
    # normalise it so every bad input becomes the same clean 400.
    name = str(name or "")
    try:
        new_cfg = template.preset_config(name)
    except ValueError:
        raise ValueError("没有这个场景")
    safeio.atomic_write_text(os.path.abspath(path), template.render(new_cfg))
    count = len(new_cfg["registry"]["services"])
    return "已套用场景 {}，共 {} 个服务。端口按你自己的实际情况改。".format(name, count)


def state(path, server=None):
    """One JSON payload for the whole page. No secrets are included.

    `overview` carries the KPI strip, recent alerts and the resident-watch
    status. It deliberately does not carry the raw `watchdog` block: webhook
    URLs and recovery commands never cross the HTTP boundary.
    """
    cfg, rows = _services_summary(path)
    started = getattr(server, "fk_started", None) if server else None
    loop = getattr(server, "fk_watch", None) if server else None
    watch = loop.status() if loop else {}
    ov = manage.overview(cfg, path, rows, started=started, watch=watch)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_path": os.path.abspath(path),
        "services": rows,
        "history": _history_rows(cfg, path),
        "service_count": len(rows),
        "is_placeholder": _is_placeholder(cfg),
        "presets": template.preset_list(),
        "overview": ov,
        "service_details": manage.service_details(cfg, path),
    }


def doctor_report(path):
    """Same P0 / uncovered classification as `fleetkeeper doctor`."""
    cfg, rows = _services_summary(path)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "services": [], "p0": [], "uncovered": []}
    for r in rows:
        item = {"service": r["name"], "port": r["port"], "level": r["level"],
                "issues": r["issues"],
                "heartbeat": {"status": r["heartbeat"], "detail": ""},
                "recovery": None}
        report["services"].append(item)
        if "L0" in r["level"] or "L1" in r["level"] or r["heartbeat"] in ("STALE", "FAILING"):
            report["p0"].append(item)
        elif r["heartbeat"] in ("NO_HEARTBEAT", "ALIVE_ONLY", "PROTOCOL_ONLY"):
            report["uncovered"].append(item)
    return report


def save_service(path, name, port):
    """Add or update one service. Returns a short Chinese status message.

    Only name+port are accepted from the browser: no arbitrary command, path
    or webhook can be injected through the UI. Writes are atomic so a sweep
    reading the file never sees a half-written JSON document.
    """
    name = str(name or "").strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("端口必须是 1 到 65535 之间的数字")
    if not name or len(name) > 64:
        raise ValueError("服务名称要填 1 到 64 个字符")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须是 1 到 65535 之间的数字")

    cfg = _load(path)
    reg = cfg.setdefault("registry", {})
    svcs = reg.setdefault("services", {})

    # The generated template contains one placeholder. Replacing it on the
    # first save keeps the page from showing a permanent red "Example Service"
    # card that the user never created and cannot get rid of.
    only_placeholder = (list(svcs.keys()) == [PLACEHOLDER_KEY]
                        and svcs[PLACEHOLDER_KEY].get("name") == PLACEHOLDER_NAME)

    target_key = None
    for key, svc in svcs.items():
        if svc.get("name") == name or key == name or svc.get("port") == port:
            target_key = key
            break
    if target_key is None:
        if only_placeholder:
            target_key = PLACEHOLDER_KEY
            svcs[target_key] = {}
        else:
            target_key = "svc{}".format(port)

    item = svcs.get(target_key) or {}
    item.update({
        "name": name,
        "port": port,
        "health": item.get("health") or {"path": "/", "method": "GET", "timeout": 3},
        "canary": item.get("canary"),
        "recovery": item.get("recovery"),
    })
    svcs[target_key] = item
    safeio.atomic_write_text(os.path.abspath(path),
                             json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return "已保存：{} :{}".format(name, port)


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "fleetkeeper-console"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Serving every asset to stderr makes the console window unreadable.
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8",
              headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _html(self, text):
        self._send(200, text, "text/html; charset=utf-8")

    def _error(self, exc, code=500):
        # Never echo a stack trace or an absolute path from an exception.
        self._json({"error": redact.redact(str(exc))[:200]}, code)

    @property
    def cfg_path(self):
        return self.server.fk_config

    def _one_service(self, key):
        cfg = _load(self.cfg_path)
        return {"row": manage.evaluate_one(cfg, self.cfg_path, key),
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if route == "/":
                return self._html(PAGE)
            if route == "/api/ping":
                # Deliberately touches no config, no port probes and no
                # network. The page uses it to separate "the exe exited"
                # from "state() itself raised an exception".
                started = getattr(self.server, "fk_started", time.time())
                return self._json({"ok": True, "pid": os.getpid(),
                                   "uptime": round(time.time() - started, 1)})
            if route == "/report":
                return self._html(report_mod.render(doctor_report(self.cfg_path)))
            if route == "/api/state":
                return self._json(state(self.cfg_path, self.server))
            if route == "/api/overview":
                cfg = _load(self.cfg_path)
                rows = manage.evaluate_rows(cfg, self.cfg_path)
                loop = getattr(self.server, "fk_watch", None)
                return self._json(manage.overview(
                    cfg, self.cfg_path, rows,
                    started=getattr(self.server, "fk_started", None),
                    watch=loop.status() if loop else {}))
            if route == "/api/watch":
                loop = getattr(self.server, "fk_watch", None)
                return self._json(loop.status() if loop else {"running": False})
            if route == "/api/alerts":
                q = parse_qs(parsed.query)
                try:
                    limit = max(1, min(manage.MAX_ALERT_LINES,
                                       int((q.get("limit") or ["20"])[0])))
                except (TypeError, ValueError):
                    return self._json({"error": "limit 必须是数字"}, 400)
                return self._json({"rows": manage.alerts(
                    _load(self.cfg_path), self.cfg_path, limit=limit)})
            if route == "/api/logs":
                q = parse_qs(parsed.query)
                key = (q.get("service") or [""])[0]
                if not key:
                    return self._json({"error": "要指定 service"}, 400)
                try:
                    tail = int((q.get("tail") or ["40"])[0])
                except (TypeError, ValueError):
                    return self._json({"error": "tail 必须是数字"}, 400)
                return self._json(manage.read_log(
                    self.cfg_path, key, tail=tail,
                    raw=(q.get("raw") or ["0"])[0] in ("1", "true", "yes")))
            if route == "/api/check":
                q = parse_qs(parsed.query)
                return self._json(self._one_service((q.get("key") or [""])[0]))
            if route == "/api/history":
                q = parse_qs(parsed.query)
                cfg = _load(self.cfg_path)
                db = history.db_path_for(
                    os.path.dirname(os.path.abspath(self.cfg_path)),
                    cfg.get("watchdog") or {})
                try:
                    since = history.parse_since((q.get("last") or ["24h"])[0])
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                rows = history.query(db, since=since,
                                     service=(q.get("service") or [None])[0],
                                     kind=(q.get("kind") or [None])[0],
                                     limit=min(200, int((q.get("limit") or ["50"])[0])))
                return self._json({"rows": rows, "count": history.count(db)})
            if route == "/api/config":
                cfg = _load(self.cfg_path)
                q = parse_qs(parsed.query)
                if (q.get("view") or [""])[0] == "full":
                    text = manage.export_config(cfg)
                    if (q.get("download") or [""])[0] in ("1", "true", "yes"):
                        return self._send(
                            200, text, "application/json; charset=utf-8",
                            {"Content-Disposition":
                             'attachment; filename="fleet.json"'})
                    return self._json({"text": text})
                # The cheap read used by the page on first paint: no probes,
                # no history query, no evidence collection.
                return self._json({
                    "config_path": os.path.abspath(self.cfg_path),
                    "is_placeholder": _is_placeholder(cfg),
                    "services": [
                        {"key": k, "name": v.get("name"), "port": v.get("port")}
                        for k, v in cli_mod._registry(cfg).items()],
                    "paused": manage.service_details(cfg, self.cfg_path)})
            if route == "/api/presets":
                return self._json({"presets": template.preset_list()})
            if route == "/api/discover":
                # Scanning is read-only. Cache the candidates server-side so
                # the browser only ever sends ids back, never a service object.
                q = parse_qs(parsed.query)
                try:
                    timeout = float((q.get("timeout") or [discover.DEFAULT_TIMEOUT])[0])
                except (TypeError, ValueError):
                    timeout = discover.DEFAULT_TIMEOUT
                res = discover.scan(
                    exclude_ports={self.server.server_port}, timeout=timeout,
                    existing_ports=_configured_ports(self.cfg_path))
                self.server.fk_discovery = res
                return self._json(res)
            return self._json({"error": "not found"}, 404)
        except Exception as exc:
            return self._error(exc)

    def do_POST(self):
        route = urlparse(self.path).path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            # Drain the bytes before answering. Replying while the client is
            # still writing resets the socket on Windows, and the browser
            # reports "Failed to fetch" instead of the real 413.
            try:
                self.rfile.read(length)
            except OSError:
                pass
            return self._json({"error": "请求体过大"}, 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "请求体不是合法 JSON"}, 400)
        # A JSON array/string parses fine but has no .get(); without this
        # check every handler below would blow up with a 500 instead of a
        # clean 400 for a malformed request.
        if not isinstance(body, dict):
            return self._json({"error": "请求体必须是 JSON 对象"}, 400)
        try:
            if route == "/api/sweep":
                return self._json(watchdog.run(self.cfg_path))
            if route == "/api/service":
                msg = save_service(self.cfg_path, body.get("name"), body.get("port"))
                return self._json({"ok": True, "message": msg})
            if route == "/api/service/action":
                action = str(body.get("action") or "")
                key = body.get("key")
                if action == "delete":
                    msg = manage.delete_service(self.cfg_path, key)
                elif action == "pause":
                    msg = manage.set_paused(self.cfg_path, key, True)
                elif action == "resume":
                    msg = manage.set_paused(self.cfg_path, key, False)
                else:
                    return self._json({"error": "未知操作"}, 400)
                return self._json({"ok": True, "message": msg})
            if route == "/api/service/update":
                msg = manage.update_service(
                    self.cfg_path, body.get("key"), body.get("patch"))
                return self._json({"ok": True, "message": msg})
            if route == "/api/heartbeat":
                msg = manage.write_heartbeat(
                    self.cfg_path, body.get("key"),
                    processed=body.get("processed"), ok=bool(body.get("ok", True)))
                return self._json({"ok": True, "message": msg})
            if route == "/api/check":
                return self._json(self._one_service(body.get("key")))
            if route == "/api/watch":
                loop = getattr(self.server, "fk_watch", None)
                if loop is None:
                    return self._json({"error": "常驻守护不可用"}, 400)
                action = str(body.get("action") or "")
                if action == "stop":
                    msg = loop.stop()
                elif action == "start":
                    msg = loop.start(body.get("interval", 60))
                else:
                    return self._json({"error": "未知操作"}, 400)
                return self._json({"ok": True, "message": msg,
                                   "watch": loop.status()})
            if route == "/api/notify-test":
                return self._json(manage.notify_test(self.cfg_path))
            if route == "/api/preset":
                # Only a whitelisted preset name crosses this boundary; no raw
                # config, path or command is accepted from the browser.
                msg = apply_preset(self.cfg_path, body.get("name"),
                                   force=bool(body.get("force")))
                return self._json({"ok": True, "message": msg})
            if route == "/api/discover":
                # The browser sends ids only. Service dicts come from the
                # server-side scan cache, so a crafted request cannot inject
                # an arbitrary health path, canary URL or command.
                cached = getattr(self.server, "fk_discovery", None) or {}
                candidates = cached.get("candidates") or []
                if body.get("rescan") or not candidates:
                    res = discover.scan(
                        exclude_ports={self.server.server_port},
                        existing_ports=_configured_ports(self.cfg_path))
                    self.server.fk_discovery = res
                    candidates = res["candidates"]
                ids = body.get("ids")
                if ids is not None and not isinstance(ids, list):
                    return self._json({"error": "ids 必须是数组"}, 400)
                if ids is not None and len(ids) > 100:
                    return self._json({"error": "一次最多加入 100 个服务"}, 400)
                out = discover.apply_config(
                    self.cfg_path, candidates, ids=ids,
                    auto=bool(body.get("auto")))
                out["candidates"] = candidates
                return self._json(out)
            if route == "/api/stop":
                loop = getattr(self.server, "fk_watch", None)
                if loop is not None:
                    loop.stop()
                # shutdown() must not be called from the serving thread itself.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json({"ok": True, "message": "stopping"})
            return self._json({"error": "not found"}, 404)
        except (ValueError, KeyError) as exc:
            return self._json({"error": redact.redact(str(exc))[:200]}, 400)
        except Exception as exc:
            return self._error(exc)


class ConsoleServer(ThreadingHTTPServer):
    """HTTP server that refuses to share its port.

    Python's HTTPServer sets SO_REUSEADDR, which on Windows lets a *second*
    process bind the exact same 127.0.0.1:port. Acceptance caught two live
    listeners on one port: the browser then talked to the older instance and
    the freshly started panel looked broken. Bind exclusively instead.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        ThreadingHTTPServer.server_bind(self)


def make_server(config_path, port=0):
    """Bind loopback only. port=0 lets the OS pick a free port."""
    path = os.path.abspath(config_path)
    server = ConsoleServer(("127.0.0.1", port), ConsoleHandler)
    server.daemon_threads = True
    server.fk_config = path
    server.fk_started = time.time()
    # Last scan result. POST /api/discover resolves the browser's ids against
    # this list instead of trusting a client-supplied service definition.
    server.fk_discovery = {}
    # Opt-in resident sweep loop. Constructing it starts no thread; the page
    # has to press "开启常驻守护" first.
    server.fk_watch = manage.WatchLoop(path)
    return server


def serve(config_path, port=0, open_browser=True):
    """Blocking entry point. Returns 0 on a clean stop."""
    path = os.path.abspath(config_path)
    created = ensure_config(path)
    try:
        server = make_server(path, port)
    except OSError as exc:
        # 10048 = WSAEADDRINUSE, 10013 = WSAEACCES (reserved/excluded port).
        code = getattr(exc, "winerror", None) or exc.errno
        if code in (10048, 10013, 48, 98, 13, 1):
            print("端口 {} 已经被别的程序占用，面板没有启动。".format(port),
                  flush=True)
            print("换一个端口再试，例如：fleetkeeper start --port {}".format(
                int(port) + 1 if port else 8799), flush=True)
            return 2
        raise
    url = "http://127.0.0.1:{}/".format(server.server_port)
    # flush=True: a frozen one-file exe writes to a pipe/console with block
    # buffering; without it the URL only appears after the process exits.
    if created:
        print("第一次使用，已自动生成配置文件：{}".format(path), flush=True)
    print("服务守护面板：{}".format(url), flush=True)
    print("页面已在浏览器中打开。按 Ctrl+C 或关掉本窗口即可停止。", flush=True)
    # Double-clicking opens the browser. FLEETKEEPER_NO_BROWSER is for
    # headless hosts and automated smoke runs; it does not change any other
    # behavior, and CLI callers can also use `start --no-browser`.
    if open_browser and not os.environ.get("FLEETKEEPER_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            print("浏览器没能自动打开，请手动复制上面的网址到浏览器。", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0
