# -*- coding: utf-8 -*-
"""Alert fan-out: file, webhook, command.

Design constraints:
  * pure standard library (the package's zero-dependency promise)
  * a broken notification channel must NEVER break the watchdog sweep
  * the file channel stays in watchdog.alert_log (single writer, one line per
    alert); this module only adds the two *outbound* channels
  * payloads pass through redact.redact() before leaving the host -- alert
    messages can contain config fragments, and a webhook URL often embeds a
    token, so even errors are redacted before being returned

Config (inside "watchdog"):
    "alert_command": "python notify-hook.py"     # legacy top-level alias
    "notify": {
        "channels": ["webhook", "command"],     # default when omitted
        "webhook_url": "http://127.0.0.1:9100/hook",
        "webhook_timeout": 3.0,
        "command": ["python", "notify-hook.py"],  # list or string
        "command_timeout": 10.0
    }

The command receives the alert as a single JSON object on stdin. Passing it
on stdin (instead of interpolating into a shell string) avoids quoting bugs
and keeps secrets out of the process list.
"""
import json
import os
import re
import shlex
import subprocess
import time

from . import redact

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_ERROR_CHARS = 200
# Webhook URLs frequently carry a token in the query string. urllib exceptions
# sometimes embed the whole URL, so strip it before redaction / logging.
_URL_RE = re.compile(r"https?://[^\s\"')]+", re.I)


# A structured payload carries keys and values separately, so the text-based
# redactor never sees "api_key=...". Mask by FIELD NAME as well as by content.
_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_ -]?key|apikey|access[_ -]?token|auth[_ -]?token|token|"
    r"secret|password|passwd|pwd|authorization|credential|"
    r"private[_ -]?key|webhook[_ -]?url|cookie)")


def _clean(value, key_hint=None):
    """Recursively redact before a value crosses a process/network boundary."""
    if key_hint and _SENSITIVE_KEY.search(str(key_hint)):
        return redact.PLACEHOLDER
    if isinstance(value, str):
        return redact.redact(value)
    if isinstance(value, dict):
        return {str(k): _clean(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def build_payload(event):
    """Add stable envelope fields. Callers can override any of them."""
    payload = {str(k): _clean(v, k) for k, v in (event or {}).items()}
    payload.setdefault("source", "fleetkeeper")
    payload.setdefault("ts", time.time())
    payload.setdefault("iso", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return payload


def _short_error(exc):
    # An urllib exception can embed the full webhook URL (which may carry a
    # token in its query string); strip the URL, redact, then truncate.
    msg = _URL_RE.sub("[URL]", "{}: {}".format(type(exc).__name__, exc))
    return redact.redact(msg)[:MAX_ERROR_CHARS]


def _post_webhook(url, payload, timeout):
    import urllib.request
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "fleetkeeper/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Drain a bounded amount: some receivers return a body, and leaving it
        # unread can keep the socket alive longer than the timeout budget.
        resp.read(4096)
        return "ok HTTP {}".format(resp.getcode())


def _run_command(command, payload, timeout, cwd):
    if isinstance(command, str):
        # posix=False on Windows keeps backslashes in paths intact; shlex's
        # default posix mode would eat "C:\hooks\notify.py" into "C:hooks...".
        argv = shlex.split(command, posix=(os.name != "nt"))
    else:
        argv = [str(x) for x in command]
    if not argv:
        raise ValueError("empty notify command")
    kwargs = {
        "input": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "cwd": cwd,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW  # no console flash on Task Scheduler
    proc = subprocess.run(argv, **kwargs)
    if proc.returncode != 0:
        err = redact.redact((proc.stderr or b"").decode("utf-8", errors="replace")[:MAX_ERROR_CHARS])
        return "exit {}: {}".format(proc.returncode, err.strip())
    return "ok exit 0"


def emit(cfg_base, wcfg, event):
    """Fan out one alert. Returns {channel: result}. Never raises.

    With no notify config this returns {} and does nothing -- i.e. adding
    this module does not change the behaviour of existing installations.
    """
    wcfg = wcfg or {}
    conf = dict(wcfg.get("notify") or {})
    channels = conf.get("channels") or ["webhook", "command"]
    webhook = conf.get("webhook_url") or wcfg.get("alert_webhook")
    command = conf.get("command") or wcfg.get("alert_command")
    payload = build_payload(event)
    results = {}

    if "webhook" in channels and webhook:
        try:
            results["webhook"] = _post_webhook(
                str(webhook), payload, float(conf.get("webhook_timeout", 3.0)))
        except Exception as e:
            results["webhook"] = "error " + _short_error(e)

    if "command" in channels and command:
        try:
            results["command"] = _run_command(
                command, payload, float(conf.get("command_timeout", 10.0)),
                cfg_base or os.getcwd())
        except Exception as e:
            results["command"] = "error " + _short_error(e)

    # "file" is deliberately not re-written here: watchdog.alert_log already
    # owns that file. Listing it in channels is accepted for discoverability.
    if "file" in channels:
        results["file"] = "written by alert_log"
    return results


def parse_alert_line(line):
    """Inverse of watchdog.alert_log's 'ts\\ttitle\\tmessage' format."""
    parts = (line or "").split("\t", 2)
    if len(parts) == 3:
        return {"title": parts[1], "message": parts[2], "logged_at": parts[0]}
    return {"title": "alert", "message": line or "", "logged_at": None}
