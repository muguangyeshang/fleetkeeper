# -*- coding: utf-8 -*-
"""Port watchdog with self-heal cooldowns and network gating.

Runs one sweep (designed to be invoked every minute by cron / Task Scheduler):
  1. count LISTENING processes per registered port
     - 0 -> dead (self-heal candidate)
     - >1 -> duplicate instance (alert, never auto-heal)
  2. TCP-connect each single-listener port (catches the zombie that holds
     the port but no longer answers)
  3. heal dead ports by launching the registered recovery script, with a
     PER-PORT cooldown (a global cooldown delays real failures behind
     unrelated ones, and batch-spawns a CPU spike when it expires)
  4. network gate: when the WAN is unreachable, record but NEVER heal --
     a self-heal loop against an outage just spawns zombies and hammers
     upstreams
  5. pause gates: a marker file flips ports into "intentionally offline"
     so a human's choice is not monitored into an incident
Alerts are deduplicated: first alert at threshold, re-alerts at a fixed
interval, one alert on recovery.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

from . import history, notify, proc

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"consecutive_abnormal": 0, "last_alert_count": 0,
                "last_issues": [], "last_run_ts": 0,
                "heal_history": {}, "network_down_since": None}


def save_state(path, state):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    from . import safeio
    safeio.atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def _resolve(p, base):
    """Resolve a possibly-relative config path against the config file's dir."""
    if not p:
        return p
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(base, p))


def net_gate(targets, timeout=3.0):
    """True if any probe target accepts a TCP connection (basic reachability,
    deliberately independent of VPN/DNS state).

    An empty target list means the operator did not enable the network gate.
    It must NOT be read as "WAN is down" -- the old fall-through returned
    False for an empty list and suspended healing forever on a fresh install.
    """
    if not targets:
        return True
    for host, port in targets:
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except OSError:
            continue
    return False


def alert_log(cfg_base, alert_log, title, message):
    line = "{}\t{}\t{}".format(time.strftime("%Y-%m-%dT%H:%M:%S"), title, message)
    try:
        p = _resolve(alert_log, cfg_base)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return line


def _run_core(config_path):
    """One watchdog sweep: detection, gating, healing, alert dedup.

    Kept free of notification/history concerns so a broken outbound channel
    can never influence whether a port gets healed. `run()` wraps this.
    """
    cfg_path = os.path.abspath(config_path)
    cfg_base = os.path.dirname(cfg_path)
    cfg = load_config(cfg_path)
    wcfg = cfg.get("watchdog") or {}
    registry = ((cfg.get("registry") or {}).get("services")) or {}

    ports = {}
    recovery = {}
    names = {}
    for key, svc in registry.items():
        port = svc.get("port")
        if port is None:
            continue
        port = int(port)
        ports[port] = key
        names[port] = svc.get("name") or key
        rec = svc.get("recovery")
        if rec:
            recovery[port] = _resolve(rec, cfg_base)

    state_file = _resolve(wcfg.get("state_file") or "runtime/watchdog-state.json", cfg_base)
    state = load_state(state_file)
    state["last_run_ts"] = time.time()

    threshold = int(wcfg.get("threshold", 2))
    re_alert_interval = int(wcfg.get("re_alert_interval", 15))
    heal_cooldown = int(wcfg.get("heal_cooldown_seconds", 180))
    # An explicit empty list is the operator disabling the gate (the generated
    # template ships with []). Only a MISSING key falls back to the built-in
    # target; `or` here would silently turn "off" into a public-IP probe on
    # every sweep, and a WAN outage would then suspend healing on a fresh host.
    raw_targets = wcfg.get("net_probe_targets")
    if raw_targets is None:
        raw_targets = [["223.5.5.5", 443]]
    net_targets = [tuple(t) for t in raw_targets]
    net_timeout = float(wcfg.get("net_probe_timeout", 3.0))

    # -- pause gates (marker files) ------------------------------------
    gated_ports = set()
    active_gates = []
    for gate in (wcfg.get("pause_gates") or {}).values():
        marker = _resolve(gate.get("marker_file") or "", cfg_base)
        gate_ports = set(int(p) for p in (gate.get("ports") or []))
        if not marker or not gate_ports:
            continue
        if os.path.exists(marker):
            gated_ports |= gate_ports
            active_gates.append(os.path.basename(marker))

    now = time.time()

    # -- network gate ----------------------------------------------------
    net_ok = net_gate(net_targets, net_timeout)
    state["net_gate"] = {"ok": net_ok, "ts": int(now)}
    was_down = bool(state.get("network_down_since"))
    alerts = []
    if not net_ok:
        if not was_down:
            state["network_down_since"] = int(now)
            alerts.append(alert_log(
                cfg_base, wcfg.get("alert_log"), "network-gate",
                "WAN unreachable ({} unreachable); healing suspended".format(net_targets[0][0])))
        listeners_map = proc.listeners(set(ports))
        issues = []
        for port, key in ports.items():
            if port in gated_ports:
                continue
            n = len(listeners_map.get(port, set()))
            if n == 0:
                issues.append("{}(:{}) no listener [network down; no auto-heal]".format(names[port], port))
            elif n > 1:
                issues.append("{}(:{}) {} processes".format(names[port], port, n))
        state["last_issues"] = issues
        save_state(state_file, state)
        return {"issues": issues, "healed": [], "alerts": alerts,
                "network_down": True, "gated_ports": sorted(gated_ports)}

    if was_down:
        dur = int(now - state.pop("network_down_since"))
        state["network_down_recovered_at"] = int(now)
        alerts.append(alert_log(
            cfg_base, wcfg.get("alert_log"), "network-recovered",
            "WAN back after ~{}m; healing re-enabled".format(dur // 60)))

    # -- sweep -----------------------------------------------------------
    listeners_map = proc.listeners(set(ports))
    issues = []
    dead_ports = []
    for port in sorted(ports):
        if port in gated_ports:
            continue  # intentionally offline by operator choice
        n = len(listeners_map.get(port, set()))
        if n == 0:
            issues.append("{}(:{}) no process listening".format(names[port], port))
            dead_ports.append(port)
        elif n > 1:
            issues.append("{}(:{}) {} processes listening (duplicate instance)".format(
                names[port], port, n))
        elif not _tcp_ok(port):
            issues.append("{}(:{}) 1 process but no TCP response (zombie)".format(names[port], port))

    # -- heal with per-port cooldown --------------------------------------
    # JSON round-trip turns int keys into strings ("18009"); normalize back to
    # int or the cooldown lookup heals_history.get(int_port) always misses and
    # the cooldown silently never applies. (Latent bug found in the upstream
    # production watchdog by these tests -- fixed here.)
    heal_history = {}
    for k, v in (state.get("heal_history") or {}).items():
        try:
            heal_history[int(k)] = float(v)
        except (TypeError, ValueError):
            continue
    # Clock-skew guard: a future timestamp makes (now - ts) negative forever,
    # silently disabling healing for that port.
    for p in list(heal_history.keys()):
        if heal_history[p] > now + 60:
            state["clock_skew_detected"] = int(heal_history[p] - now)
            heal_history[p] = 0

    healed = []
    for port in dead_ports:
        cmd = recovery.get(port)
        if not cmd:
            continue
        if (now - heal_history.get(port, 0)) <= heal_cooldown:
            continue  # this port healed too recently; wait
        if os.name == "nt" and cmd.lower().endswith(".py"):
            launcher = _pythonw_or_python()
            argv = [launcher, cmd]
        else:
            argv = [cmd]
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW
                kwargs["env"] = _child_env()
            with open(os.path.splitext(cmd)[0] + ".err.log", "ab") as errf:
                subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=errf, **kwargs)
            healed.append(":{}->{}".format(port, os.path.basename(cmd)))
            heal_history[port] = now
        except Exception:
            pass
    if healed:
        state["last_heal_ts"] = now
        state["last_heal_actions"] = healed
    state["heal_history"] = heal_history

    if not issues:
        if state.get("consecutive_abnormal", 0) >= threshold:
            alerts.append(alert_log(cfg_base, wcfg.get("alert_log"),
                                    "recovered", "all fleet ports healthy again"))
        state["consecutive_abnormal"] = 0
        state["last_issues"] = []
        state["last_heal_ts"] = 0
        state["last_heal_actions"] = []
        # keep heal_history (per-port cooldown must survive green sweeps), but
        # prune entries older than a day so the file cannot grow forever
        state["heal_history"] = {p: t for p, t in heal_history.items() if now - t < 86400}
        save_state(state_file, state)
        return {"issues": [], "healed": healed, "alerts": alerts,
                "network_down": False, "gated_ports": sorted(gated_ports)}

    # -- alert with storm suppression --------------------------------------
    state["consecutive_abnormal"] = state.get("consecutive_abnormal", 0) + 1
    state["last_issues"] = issues
    count = state["consecutive_abnormal"]
    should_alert = (count == threshold) or (
        count > threshold and (count - threshold) % re_alert_interval == 0)
    if should_alert:
        msg = "; ".join(issues)
        if healed:
            msg += "; auto-healed {}".format(", ".join(healed))
        alerts.append(alert_log(cfg_base, wcfg.get("alert_log"),
                                "abnormal(x{})".format(count), msg))
    save_state(state_file, state)
    return {"issues": issues, "healed": healed, "alerts": alerts,
            "network_down": False, "gated_ports": sorted(gated_ports)}


def _tcp_ok(port, timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _pythonw_or_python():
    exe = os.path.abspath(sys.executable)
    pyw = os.path.join(os.path.dirname(exe),
                       "pythonw.exe" if os.name == "nt" else "python")
    return pyw if os.path.exists(pyw) else exe


def _child_env():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(config_path):
    """Public sweep API = core sweep + outbound alerts + timeline.

    Wrapping (rather than editing) the core keeps the safety semantics
    untouched: notification and history happen AFTER healing decisions are
    made, and every failure here is isolated from the healing path.
    """
    summary = _run_core(config_path)
    summary.setdefault("notify", [])
    try:
        cfg_path = os.path.abspath(config_path)
        cfg_base = os.path.dirname(cfg_path)
        cfg = load_config(cfg_path)
        wcfg = cfg.get("watchdog") or {}
        db = history.db_path_for(cfg_base, wcfg)
        try:
            retention = float(wcfg.get("history_retention_days",
                                       history.DEFAULT_RETENTION_DAYS))
        except (TypeError, ValueError):
            retention = history.DEFAULT_RETENTION_DAYS

        # Core issues are human-readable strings like "GW(:8080) no process
        # listening". Map the port back to the registry so the timeline and
        # the webhook payload carry a real service name -- otherwise every row
        # says "fleet" and `--service` can never filter anything.
        by_port = {}
        for key, svc in ((cfg.get("registry") or {}).get("services") or {}).items():
            p = svc.get("port")
            if p is not None:
                by_port[int(p)] = svc.get("name") or key
        affected = []
        for line in (summary.get("issues") or []):
            m = re.search(r"\(:(\d+)\)", line)
            if not m:
                continue
            port = int(m.group(1))
            name = by_port.get(port)
            if name and name not in [n for n, _ in affected]:
                affected.append((name, port))
        svc_label = ", ".join(n for n, _ in affected) or None
        first_port = affected[0][1] if affected else None
        summary["affected_services"] = [
            {"name": n, "port": p} for n, p in affected]

        alerts = summary.get("alerts") or []
        for line in alerts:
            event = notify.parse_alert_line(line)
            event.update({
                "kind": "alert",
                "level": "P0" if summary.get("network_down") else "warning",
                "service": svc_label,
                "port": first_port,
                "issues": summary.get("issues") or [],
                "healed": summary.get("healed") or [],
                "net_ok": not summary.get("network_down"),
                "network_down": bool(summary.get("network_down")),
            })
            channels = notify.emit(cfg_base, wcfg, event)
            summary["notify"].append({"title": event["title"], "channels": channels})
            history.append(db, event, retention_days=retention)
        sweep = history.sweep_event(summary)
        sweep["service"] = svc_label
        sweep["port"] = first_port
        history.append(db, sweep, retention_days=retention)
        summary["history_file"] = db
    except Exception as exc:
        # Never let diagnostics turn a healthy sweep into a failed one. The
        # message is redacted: config paths and URLs can carry credentials.
        from . import redact
        summary["notify_error"] = redact.redact(
            "{}: {}".format(type(exc).__name__, exc))[:200]
    return summary
