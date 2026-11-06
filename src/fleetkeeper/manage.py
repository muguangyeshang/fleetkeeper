# -*- coding: utf-8 -*-
"""Fleet management operations behind the browser console.

`ui.py` owns HTTP routing; this module owns what those routes actually do:
concurrent evidence collection, aggregate metrics, service lifecycle edits,
heartbeat injection, redacted log reads, alert history, notification tests and
an in-process resident watchdog loop.

Every function takes an explicit config path. No module-level mutable fleet
state: two consoles in one process (tests do this) must never share a registry.
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import cli as cli_mod
from . import evidence, heartbeat, history, notify, redact, safeio, watchdog

MAX_WORKERS = 8
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 5
MAX_INTERVAL = 3600
MAX_LOG_LINES = 300
MAX_ALERT_LINES = 50

# Probe paths are local URL paths only. A browser-supplied "path" must never
# become an absolute URL, a UNC path, a scheme or a parent-directory escape.
_LOCAL_PATH = re.compile(r"^/[A-Za-z0-9._~/%?=&+\-]*$")
_RELATIVE_FILE = re.compile(r"^[A-Za-z0-9._\-/\\ ]+$")

LEVEL_ORDER = ("L0_CONFIG", "L1_PROCESS", "L2_TCP", "L3_PROTOCOL", "L4_BUSINESS")


def load(path):
    with open(os.path.abspath(path), "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, cfg):
    # Atomic: a concurrent sweep must never read a half-written JSON document.
    safeio.atomic_write_text(
        os.path.abspath(path),
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def _raw_services(cfg):
    return ((cfg.get("registry") or {}).get("services") or {})


def _paused_services(cfg):
    return ((cfg.get("registry") or {}).get("paused") or {})


def _merged(cfg):
    return cli_mod._registry(cfg)


def _require_key(cfg, key):
    key = str(key or "")
    if key in _raw_services(cfg):
        return key
    svcs = _raw_services(cfg)
    for k, v in svcs.items():
        if (v or {}).get("name") == key:
            return k
    paused = _paused_services(cfg)
    for k, v in paused.items():
        if k == key or (v or {}).get("name") == key:
            raise ValueError("这个服务已暂停，先恢复它再操作")
    raise ValueError("没有这个服务")


def _check_local_path(value, field):
    value = str(value or "").strip()
    if not value.startswith("/") or not _LOCAL_PATH.match(value):
        raise ValueError("{} 只能是以 / 开头的本机路径".format(field))
    if "://" in value or ".." in value or "//" in value:
        raise ValueError("{} 不能是外部网址或跳出路径".format(field))
    return value


def _check_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("端口必须是 1 到 65535 之间的整数")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须是 1 到 65535 之间的整数")
    return port


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------

def _one_row(key, svc, cfg, path, hb_dir):
    """One service's full ladder. Runs in a worker thread.

    `evidence.evaluate_service` performs up to two socket probes with a
    configurable timeout; `heartbeat.evaluate` is a local file read.
    """
    ev = evidence.evaluate_service(svc)
    try:
        period = int(svc.get("heartbeat_period") or 60)
    except (TypeError, ValueError):
        period = 60
    hb = heartbeat.evaluate(key, period, directory=hb_dir)
    health = svc.get("health") or {}
    canary = svc.get("canary") or {}
    logs = svc.get("logs") or {}
    return {
        "key": key,
        "name": svc.get("name") or key,
        "port": svc.get("port"),
        "level": ev["level_name"],
        "level_number": ev.get("level", 0),
        "pids": ev.get("pids", []),
        "heartbeat": hb["status"],
        "hb_age": hb.get("age_seconds"),
        "evidence": ev.get("evidence", []),
        "issues": [redact.redact(str(i)) for i in ev.get("issues", [])],
        "health_path": health.get("path", "/"),
        "canary_path": canary.get("path"),
        "canary_type": canary.get("type"),
        "heartbeat_period": period,
        "has_recovery": bool(svc.get("recovery")),
        "logs": sorted(logs.keys()),
        "paused": False,
    }


def evaluate_one(cfg, path, key):
    """Re-check exactly one service, used by the per-card '体检这一个' button.

    Accepting a name as well as a config key lets the console stay usable
    after a rename without another full refresh.
    """
    svcs = _merged(cfg)
    key = str(key or "")
    if key not in svcs:
        for k, v in svcs.items():
            if (v or {}).get("name") == key:
                key = k
                break
    if key not in svcs:
        raise ValueError("没有这个服务")
    return _one_row(key, svcs[key], cfg, path, cli_mod._hb_dir(cfg, path))


def evaluate_rows(cfg, path, max_workers=MAX_WORKERS):
    """Collect the ladder for every service concurrently.

    Complexity: the serial version cost O(n * probe_latency) and made an
    11-service console take longer than the browser's first paint. With a
    bounded pool the wall time is O(max_probe_latency) for n <= max_workers,
    so adding services does not linearly slow the page. Order is restored to
    config order afterwards so cards never jump between refreshes.
    """
    svcs = _merged(cfg)
    if not svcs:
        return []
    hb_dir = cli_mod._hb_dir(cfg, path)
    keys = list(svcs)
    rows = []
    workers = min(max(1, int(max_workers)), len(keys))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_one_row, k, svcs[k], cfg, path, hb_dir): k
            for k in keys
        }
        collected = {}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                collected[key] = fut.result()
            except Exception as exc:
                # One broken service must not blank the whole dashboard.
                collected[key] = {
                    "key": key, "name": svcs[key].get("name") or key,
                    "port": svcs[key].get("port"), "level": "L0_CONFIG",
                    "level_number": 0, "pids": [], "heartbeat": "ERROR",
                    "hb_age": None, "evidence": [],
                    "issues": [redact.redact(str(exc))[:160]],
                    "health_path": "/", "canary_path": None,
                    "canary_type": None, "heartbeat_period": 60,
                    "has_recovery": False, "logs": [], "paused": False,
                }
    order = {k: i for i, k in enumerate(keys)}
    rows = sorted(collected.values(), key=lambda r: order[r["key"]])
    return rows


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

def alerts(cfg, path, limit=MAX_ALERT_LINES):
    """Recent lines from the alert log, newest last. Never raises."""
    wcfg = cfg.get("watchdog") or {}
    rel = wcfg.get("alert_log") or "runtime/alerts.log"
    base = os.path.dirname(os.path.abspath(path))
    target = rel if os.path.isabs(rel) else os.path.join(base, rel)
    if not os.path.isfile(target):
        return []
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max(1, int(limit)):]
        out = []
        for line in lines:
            item = notify.parse_alert_line(line.rstrip("\r\n"))
            item["message"] = redact.redact(item.get("message") or "")[:300]
            item["title"] = redact.redact(item.get("title") or "alert")[:120]
            out.append(item)
        return out
    except OSError:
        return []


def overview(cfg, path, rows, started=None, watch=None, alert_limit=8):
    """One flat object for the KPI strip. No config secrets are included."""
    levels = [r.get("level_number", 0) for r in rows]
    db = history.db_path_for(os.path.dirname(os.path.abspath(path)),
                             cfg.get("watchdog") or {})
    recent = history.query(db, limit=1)
    sweeps = history.query(db, kind="sweep", limit=1)
    alert_rows = alerts(cfg, path, limit=alert_limit)
    total = len(rows)
    healthy = sum(1 for n in levels if n >= 3)
    attention = sum(1 for n in levels if n == 2)
    down = sum(1 for n in levels if n <= 1)
    business = sum(1 for n in levels if n >= 4)
    hb_ok = sum(1 for r in rows if r.get("heartbeat") in ("OK", "FRESH", "ALIVE"))
    return {
        "total": total,
        "healthy": healthy,
        "attention": attention,
        "down": down,
        "business": business,
        "heartbeat_ok": hb_ok,
        "paused": len(_paused_services(cfg)),
        "event_count": history.count(db),
        "last_event": recent[0] if recent else None,
        "last_sweep": sweeps[0] if sweeps else None,
        "alert_rows": alert_rows,
        "alert_count": len(alert_rows),
        "watch": dict(watch or {}),
        "uptime": round(time.time() - (started if started is not None
                                       else time.time()), 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def service_details(cfg, path):
    """Config-level view of each service: what is actually persisted on disk."""
    out = []
    for key, svc in _raw_services(cfg).items():
        out.append({
            "key": key, "name": svc.get("name"), "port": svc.get("port"),
            "health": dict(svc.get("health") or {}),
            "canary": dict(svc.get("canary") or {}),
            "recovery": bool(svc.get("recovery")),
            "heartbeat_period": svc.get("heartbeat_period"),
            "logs": dict(svc.get("logs") or {}),
            "paused": False,
        })
    for key, svc in _paused_services(cfg).items():
        out.append({
            "key": key, "name": (svc or {}).get("name"),
            "port": (svc or {}).get("port"),
            "health": {}, "canary": {}, "recovery": False,
            "heartbeat_period": None, "logs": {}, "paused": True,
        })
    return out


def export_config(cfg):
    """Full config as text. It stays on loopback, but secret-shaped values are
    still masked so a copied block cannot leak a webhook token by accident."""
    return redact.redact(json.dumps(cfg, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

def delete_service(path, key):
    cfg = load(path)
    k = _require_key(cfg, key)
    name = (_raw_services(cfg)[k] or {}).get("name") or k
    del cfg["registry"]["services"][k]
    write(path, cfg)
    return "已删除服务 {}".format(name)


def set_paused(path, key, paused):
    """Pause = move the entry out of `registry.services` into `registry.paused`.

    The watchdog only iterates `registry.services`, so a paused service is
    ignored by detection, healing and alerts without any new gate logic that
    could drift between the CLI and the console.
    """
    cfg = load(path)
    reg = cfg.setdefault("registry", {})
    active = reg.setdefault("services", {})
    bank = reg.setdefault("paused", {})
    k = str(key or "")
    if paused:
        if k not in active:
            for kk, vv in active.items():
                if (vv or {}).get("name") == k:
                    k = kk
                    break
        if k not in active:
            raise ValueError("没有这个服务")
        bank[k] = active.pop(k)
        name = (bank[k] or {}).get("name") or k
        msg = "已暂停 {}，守护将跳过它".format(name)
    else:
        if k not in bank:
            raise ValueError("这个服务没有暂停记录")
        active[k] = bank.pop(k)
        name = (active[k] or {}).get("name") or k
        msg = "已恢复 {}，守护会重新盯着它".format(name)
    write(path, cfg)
    return msg


def update_service(path, key, patch):
    """Whitelisted field edits only.

    The browser cannot set `recovery` (an arbitrary command), `alert_command`,
    a webhook URL or an absolute log path. Probe paths are local URL paths;
    log paths are relative filenames under the config directory.
    """
    if not isinstance(patch, dict):
        raise ValueError("修改内容必须是对象")
    cfg = load(path)
    k = _require_key(cfg, key)
    item = cfg["registry"]["services"][k]
    changed = []

    if "name" in patch:
        name = str(patch["name"] or "").strip()
        if not name or len(name) > 64:
            raise ValueError("服务名称要填 1 到 64 个字符")
        item["name"] = name
        changed.append("名称")
    if "port" in patch:
        item["port"] = _check_port(patch["port"])
        changed.append("端口")
    if "health_path" in patch:
        health = dict(item.get("health") or {})
        health["path"] = _check_local_path(patch["health_path"], "健康检查路径")
        health.setdefault("method", "GET")
        item["health"] = health
        changed.append("健康检查路径")
    if "health_timeout" in patch:
        try:
            seconds = float(patch["health_timeout"])
        except (TypeError, ValueError):
            raise ValueError("健康检查超时必须是数字")
        if not 0.5 <= seconds <= 30:
            raise ValueError("健康检查超时要在 0.5 到 30 秒之间")
        health = dict(item.get("health") or {})
        health["timeout"] = seconds
        item["health"] = health
        changed.append("检查超时")
    if "canary_path" in patch:
        value = patch["canary_path"]
        canary = dict(item.get("canary") or {})
        if value in (None, "", False):
            item["canary"] = None
            changed.append("业务探针（已清除）")
        else:
            canary["type"] = "http"
            canary["path"] = _check_local_path(value, "业务探针路径")
            item["canary"] = canary
            changed.append("业务探针路径")
    if "heartbeat_period" in patch:
        try:
            period = int(patch["heartbeat_period"])
        except (TypeError, ValueError):
            raise ValueError("心跳周期必须是整数秒")
        if not 5 <= period <= 86400:
            raise ValueError("心跳周期要在 5 到 86400 秒之间")
        item["heartbeat_period"] = period
        changed.append("心跳周期")
    if "log_file" in patch:
        value = str(patch["log_file"] or "").strip()
        logs = dict(item.get("logs") or {})
        if not value:
            logs.pop("stderr", None)
        else:
            if not _RELATIVE_FILE.match(value) or ".." in value or os.path.isabs(value):
                raise ValueError("日志文件只能是配置目录下的相对文件名")
            logs["stderr"] = value
        item["logs"] = logs
        changed.append("日志文件")
    if not changed:
        raise ValueError("没有可修改的字段")
    write(path, cfg)
    return "已更新 {}：{}".format(item.get("name") or k, "、".join(changed))


def write_heartbeat(path, key, processed=None, ok=True):
    """Manual heartbeat injection, for services that cannot call the library."""
    cfg = load(path)
    k = _require_key(cfg, key)
    if processed is not None:
        try:
            processed = int(processed)
        except (TypeError, ValueError):
            raise ValueError("处理条数必须是整数")
    hb_dir = cli_mod._hb_dir(cfg, path)
    heartbeat.write(k, processed=processed, ok=bool(ok), directory=hb_dir)
    return "已给 {} 写入一条心跳（{}）".format(
        (_raw_services(cfg)[k] or {}).get("name") or k,
        "成功" if ok else "失败")


def read_log(path, key, tail=60, raw=False):
    """Tail one service log. Secrets are masked unless raw=True is requested
    explicitly by the operator, matching `fleetkeeper logs --raw`."""
    cfg = load(path)
    k = _require_key(cfg, key)
    svc = _merged(cfg)[k]
    logs = svc.get("logs") or {}
    rel = logs.get("stderr") or logs.get("stdout")
    if not rel:
        return {"name": svc.get("name") or k, "path": None, "lines": [],
                "redacted": 0, "error": "这个服务没有声明日志文件"}
    base = os.path.dirname(os.path.abspath(path))
    target = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))
    if not os.path.isfile(target):
        return {"name": svc.get("name") or k, "path": rel, "lines": [],
                "redacted": 0, "error": "日志文件还不存在"}
    try:
        tail = max(1, min(MAX_LOG_LINES, int(tail)))
        with open(target, "rb") as f:
            data = f.read()[-262144:]       # bound the read: logs can be huge
        lines = data.decode("utf-8", errors="replace").splitlines()[-tail:]
        text = "\n".join(lines)
        hits = redact.scan(text)
        if not raw:
            text = redact.redact(text)
        return {"name": svc.get("name") or k, "path": rel,
                "lines": text.splitlines(), "redacted": len(hits),
                "error": None}
    except OSError as exc:
        return {"name": svc.get("name") or k, "path": rel, "lines": [],
                "redacted": 0, "error": redact.redact(str(exc))[:160]}


def notify_test(path):
    """Send one clearly-labelled test event through configured channels."""
    cfg = load(path)
    wcfg = cfg.get("watchdog") or {}
    conf = wcfg.get("notify") or {}
    webhook = conf.get("webhook_url") or wcfg.get("alert_webhook")
    command = conf.get("command") or wcfg.get("alert_command")
    if not webhook and not command:
        return {"configured": False, "results": {},
                "message": "还没配置通知通道。在 fleet.json 的 watchdog.notify 里填 webhook_url 或 command 后再试。"}
    event = {
        "kind": "notify_test", "level": "INFO", "service": "fleet",
        "title": "FleetKeeper 通知测试",
        "message": "这是一条来自本机面板的测试通知，不代表服务出现故障。",
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    results = notify.emit(os.path.dirname(os.path.abspath(path)), wcfg, event)
    clean = {str(k): redact.redact(str(v))[:200] for k, v in results.items()}
    return {"configured": True, "results": clean,
            "message": "测试通知已发出，逐通道结果：{}".format(
                "、".join("{}={}".format(k, v) for k, v in clean.items()) or "无")}


# ---------------------------------------------------------------------------
# Resident watchdog loop
# ---------------------------------------------------------------------------

class WatchLoop(object):
    """One optional background sweep loop owned by the console process.

    It is opt-in, never started by importing the module, and uses an Event so
    stop takes effect without waiting for the current interval to expire.
    """

    def __init__(self, config_path, on_stop=None):
        self.path = os.path.abspath(config_path)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._interval = DEFAULT_INTERVAL
        self._runs = 0
        self._last_run = None
        self._last_error = None
        self._last_summary = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                summary = watchdog.run(self.path)
                with self._lock:
                    self._runs += 1
                    self._last_run = time.time()
                    self._last_error = None
                    self._last_summary = {
                        "issues": len(summary.get("issues") or []),
                        "healed": len(summary.get("healed") or []),
                        "alerts": len(summary.get("alerts") or []),
                        "network_down": bool(summary.get("network_down")),
                    }
            except Exception as exc:
                with self._lock:
                    self._last_run = time.time()
                    self._last_error = redact.redact(str(exc))[:160]
            self._stop.wait(self._interval_for_wait())

    def _interval_for_wait(self):
        with self._lock:
            return self._interval

    def start(self, interval=DEFAULT_INTERVAL):
        try:
            seconds = int(interval)
        except (TypeError, ValueError):
            raise ValueError("守护间隔必须是整数秒")
        if not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
            raise ValueError("守护间隔要在 {} 到 {} 秒之间".format(
                MIN_INTERVAL, MAX_INTERVAL))
        with self._lock:
            self._interval = seconds
            alive = self._thread is not None and self._thread.is_alive()
            if alive:
                return "常驻守护已在运行，间隔改为 {} 秒".format(seconds)
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop,
                                            name="fleetkeeper-watch", daemon=True)
            self._thread.start()
        return "常驻守护已启动，每 {} 秒检查一次".format(seconds)

    def stop(self):
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
        if not alive:
            return "常驻守护本来就没在运行"
        self._stop.set()
        return "常驻守护已停止，正在跑的这一轮会自然结束"

    def status(self):
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "interval": self._interval,
                "runs": self._runs,
                "last_run": self._last_run,
                "last_error": self._last_error,
                "last_summary": self._last_summary,
            }
