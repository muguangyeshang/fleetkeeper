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

# Command lines are needed for one reason: to prove that a PID is still the
# process we meant to touch. Without psutil the Windows path used to give up
# and answer "<psutil missing>", which made every `verify_pid(..., expect=...)`
# refuse -- i.e. the safety check turned into a hard stop. `wmic` is gone from
# current Windows builds, so read them through CIM in ONE call, cached briefly
# so a 12-service pass costs one PowerShell launch rather than twelve.
CMDLINE_TTL = 3.0
_CMDLINE_CACHE = {"at": 0.0, "table": {}}


class Refused(Exception):
    pass


def _port_from(token):
    """0.0.0.0:8000 / [::]:8000 / *.80 (macOS) -> 8000 / 80."""
    tail = token.rsplit(":", 1)[-1]
    if not tail.isdigit():
        tail = tail.rsplit(".", 1)[-1]
    return int(tail)


def _parse_netstat(raw, is_nt, ports=None):
    """Pure parser for `netstat -ano` (Windows) / `-tlnp` (Linux) / `-an`
    (macOS). Split out from subprocess so both line shapes are unit-testable
    without actually running netstat.

    Windows:  TCP    1.2.3.4:8000   0.0.0.0:0   LISTENING   1234
    Linux:    tcp 0 0 0.0.0.0:8000 0.0.0.0:*   LISTEN      1234/python
    macOS:    tcp4 0 0 *.80        *.*          LISTEN      (no PID column)

    A missing PID becomes the sentinel -1: evidence.py treats that as
    "a listener exists but the process is unidentified", never as "dead".
    """
    out = {}
    for line in (raw or "").splitlines():
        parts = line.split()
        if is_nt:
            if not (len(parts) >= 5 and parts[0] in ("TCP", "TCPv6")
                    and parts[3] == "LISTENING"):
                continue
            try:
                port, pid = _port_from(parts[1]), int(parts[4])
            except (ValueError, IndexError):
                continue
        else:
            if not (len(parts) >= 4 and parts[0].startswith("tcp")
                    and "LISTEN" in parts):
                continue
            try:
                port = _port_from(parts[3])
            except ValueError:
                continue
            pid = -1
            for tok in parts[5:]:
                head = tok.split("/", 1)[0]   # "1234/python" -> "1234"
                if head.isdigit():
                    pid = int(head)
                    break
        if ports is None or port in ports:
            out.setdefault(port, set()).add(pid)
    return out


def _netstat_listeners(ports=None):
    """Parse `netstat -ano` (Windows) or `netstat -tlnp`/plain (POSIX)."""
    is_nt = os.name == "nt"
    if is_nt:
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
    return _parse_netstat(raw, is_nt, ports)


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


def proc_info(pid, with_cmdline=None):
    """{pid, name, cmdline, exe, alive}. cmdline stays None when unreadable
    (never guess).

    Fallback lesson (real incident): when psutil is missing, do NOT report
    alive=False for every PID -- that mislabels a healthy fleet as dead.
    Degrade to tasklist (Windows) / ps (POSIX) for liveness at least.

    `with_cmdline=False` skips the CIM round trip on hosts without psutil.
    The status page polls every 5 s and needs liveness, not command lines;
    paying ~1 s of PowerShell per poll for data nobody renders is waste.
    verify_pid() asks for it explicitly, because there it is a safety guard.
    """
    if with_cmdline is None:
        with_cmdline = psutil is not None
    info = {"pid": pid, "name": None, "cmdline": None, "exe": None, "alive": False}
    if psutil is None:
        return _proc_info_fallback(pid, info, with_cmdline)
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


def _cim_cmdlines(force=False):
    """{pid: cmdline} in one call, cached for CMDLINE_TTL seconds.

    CIM returns a table; splitting the PowerShell output by NUL is not
    portable, so each row is printed as "pid<TAB>cmdline" and parsed here.
    Any failure (PowerShell missing, execution policy, timeout) returns {}:
    callers must treat a missing command line as "unproven", never as "ok".
    """
    now = time.time()
    if not force and now - _CMDLINE_CACHE["at"] < CMDLINE_TTL:
        return _CMDLINE_CACHE["table"]
    table = {}
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | "
                  "ForEach-Object { \"{0}`t{1}\" -f $_.ProcessId, $_.CommandLine }")
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            ).stdout or ""
        except Exception:
            out = ""
        for line in out.splitlines():
            pid_s, tab, cmd = line.partition("\t")
            if not tab or not pid_s.strip().isdigit():
                continue
            cmd = cmd.strip()
            if cmd:
                table[int(pid_s)] = cmd[:1024]
    _CMDLINE_CACHE["at"] = now
    _CMDLINE_CACHE["table"] = table
    return table


def _proc_info_fallback(pid, info, with_cmdline=False):
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
                # Keep None, not a placeholder string: verify_pid tests
                # `is None` to refuse, and the ops log should not record a
                # fake command line.
                if with_cmdline:
                    cmd = _cim_cmdlines().get(int(pid))
                    if cmd is None:
                        # Started after the cache was filled (a service that
                        # just came up): one forced refresh, then give up.
                        cmd = _cim_cmdlines(force=True).get(int(pid))
                    info["cmdline"] = cmd
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True, text=True, timeout=10,
            ).stdout or ""
            cmd = out.strip()
            if cmd:
                info["alive"] = True
                info["cmdline"] = cmd[:1024]
                info["name"] = cmd.split()[0].rsplit("/", 1)[-1]
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
    info = proc_info(int(pid), with_cmdline=True)
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
