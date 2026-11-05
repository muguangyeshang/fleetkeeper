# -*- coding: utf-8 -*-
"""Process identification: only by PID + full command line, never by name.

A local AI stack runs many services under the same pythonw.exe / python.exe
image. Killing by image name is indiscriminate slaughter of your own fleet.
This module deliberately provides no kill-by-name interface, and `verify_pid`
refuses PID <= 4 (system reserved) and validates the command line against an
expected substring before any destructive act.
"""
import json
import os
import subprocess
import time

try:
    import psutil  # optional acceleration, never a hard dependency
except Exception:
    psutil = None

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Refused(Exception):
    pass


def _netstat_listeners(ports=None):
    """Parse `netstat -ano` (Windows) or `netstat -tlnp`/plain (POSIX)."""
    if os.name == "nt":
        cmd = ["netstat", "-ano"]
    else:
        cmd = ["netstat", "-tlnp"] if os.path.exists("/proc") else ["netstat", "-an"]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        ).stdout or ""
    except Exception:
        return {}
    out = {}
    for line in raw.splitlines():
        parts = line.split()
        # Windows: TCP  1.2.3.4:8000  0.0.0.0:0  LISTENING  1234
        if os.name == "nt":
            ok = len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING"
            if ok:
                try:
                    port = int(parts[1].rsplit(":", 1)[-1])
                    pid = int(parts[4])
                except ValueError:
                    continue
                if ports is None or port in ports:
                    out.setdefault(port, set()).add(pid)
        else:
            # Linux: tcp 0 0 0.0.0.0:8000 0.0.0.0:* LISTEN <pid/name or ->
            ok = len(parts) >= 4 and parts[0] in ("tcp", "tcp6") and "LISTEN" in parts
            if ok:
                try:
                    port = int(parts[3].rsplit(":", 1)[-1])
                except ValueError:
                    continue
                pid = None
                for tok in parts[6:]:
                    if tok.isdigit():
                        pid = int(tok)
                        break
                if ports is None or port in ports:
                    out.setdefault(port, set()).add(pid if pid else -1)
    return out


def listeners(ports=None):
    """{port: {pid, ...}} for LISTENING sockets. psutil when available,
    netstat fallback that works on both Windows and POSIX."""
    if psutil is not None:
        out = {}
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN and c.laddr:
                    port = c.laddr.port
                    if ports is None or port in ports:
                        if c.pid:
                            out.setdefault(port, set()).add(c.pid)
            return out
        except Exception:
            pass
    return _netstat_listeners(ports)


def proc_info(pid):
    """{pid, name, cmdline, exe, alive}. cmdline stays None when unreadable
    (never guess).

    Fallback lesson (real incident): when psutil is missing, do NOT report
    alive=False for every PID -- that mislabels a healthy fleet as dead.
    Degrade to tasklist (Windows) / ps (POSIX) for liveness at least.
    """
    info = {"pid": pid, "name": None, "cmdline": None, "exe": None, "alive": False}
    if psutil is None:
        return _proc_info_fallback(pid, info)
    try:
        p = psutil.Process(pid)
        info["alive"] = p.is_running()
        info["name"] = p.name()
        info["exe"] = p.exe()
        try:
            info["cmdline"] = " ".join(p.cmdline())
        except Exception:
            info["cmdline"] = None
    except Exception:
        pass
    return info


def _proc_info_fallback(pid, info):
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            ).stdout or ""
            line = out.strip().splitlines()[0] if out.strip() else ""
            if line and "PID" not in line[:20] and '","' in line:
                parts = [c.strip('"') for c in line.split('","')]
                info["name"] = parts[0]
                info["alive"] = True
                info["cmdline"] = "<psutil missing; command line unreadable>"
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=10,
            ).stdout or ""
            name = out.strip()
            if name:
                info["name"] = name
                info["alive"] = True
                info["cmdline"] = "<psutil missing; command line unreadable>"
    except Exception:
        pass
    return info


def find_listener(port):
    """All listeners on one port, with details."""
    pids = listeners({port}).get(port, set())
    return [proc_info(pid) for pid in sorted(pids)]


def verify_pid(pid, expect=None):
    """Fetch a process by PID and validate its command line contains `expect`.

    Refuses PID <= 4 (system reserved) and dead PIDs. With `expect` set, a
    mismatched command line raises Refused -- the guard that makes
    "kill by PID" safe to expose.
    """
    if pid is None or int(pid) <= 4:
        raise Refused("refusing PID {}: system-reserved".format(pid))
    info = proc_info(int(pid))
    if not info["alive"]:
        raise Refused("PID {} does not exist or has exited".format(pid))
    if expect:
        cmd = info.get("cmdline") or ""
        if expect.lower() not in cmd.lower():
            raise Refused(
                "PID {} command line does not contain {!r}; refusing. Actual: {}".format(
                    pid, expect, (cmd[:160] or "<unreadable>")))
    return info


def kill_pid(pid, expect=None, log_path=None):
    """Terminate one PID, optionally validated. Returns the victim's info."""
    info = verify_pid(pid, expect)
    if psutil is not None:
        psutil.Process(int(pid)).terminate()
    elif os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        subprocess.run(["kill", str(int(pid))], capture_output=True)
    if log_path:
        append_ops_log(log_path, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "action": "kill_pid", "pid": int(pid),
            "expect": expect, "name": info.get("name"),
            "cmdline": (info.get("cmdline") or "")[:200],
        })
    return info


def append_ops_log(log_path, record):
    d = os.path.dirname(log_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
