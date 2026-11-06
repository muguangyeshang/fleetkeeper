# -*- coding: utf-8 -*-
"""End-to-end journey for the frozen exe in a clean directory.

Runs the exact user path: double-click (no args, no Python on PATH),
open the generated page, hit the liveness probe, stop the program, then
verify an orphaned tab really gets a refused connection instead of a
silent success. FLEETKEEPER_NO_BROWSER keeps this runnable headless.

Usage:
    python scripts/e2e_exe_console.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "fleetkeeper.exe")
WORK = os.path.join(tempfile.gettempdir(), "fk-e2e-console")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return r.status, r.read().decode("utf-8", "replace")


def post(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode("utf-8", "replace")


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (" :: " + str(detail) if not ok else ""))
    if not ok:
        raise AssertionError(label + " " + str(detail))


def main():
    check("frozen exe exists", os.path.isfile(EXE), EXE)
    os.makedirs(WORK, exist_ok=True)
    target = os.path.join(WORK, "fleetkeeper.exe")
    cfg = os.path.join(WORK, "fleet.json")
    shutil.copy2(EXE, target)
    if os.path.exists(cfg):
        os.remove(cfg)                       # force the first-run path

    env = dict(os.environ)
    env["FLEETKEEPER_NO_BROWSER"] = "1"
    env.pop("PYTHONPATH", None)              # frozen exe must not need source
    proc = subprocess.Popen(
        [target], cwd=WORK, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)

    base = None
    printed = []
    deadline = time.time() + 45
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line:
                printed.append(line.rstrip())
                m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
                if m:
                    base = "http://127.0.0.1:{}/".format(m.group(1))
                    break
            elif proc.poll() is not None:
                break
        check("exe printed a loopback URL", base is not None, printed[-5:])
        check("exe generated fleet.json", os.path.isfile(cfg), cfg)

        code, page = get(base, "/")
        check("GET / is 200", code == 200, code)
        check("page has the reconnect button", 'id="btn-reconnect"' in page)
        check("page has the Chinese recovery text",
              "重新双击 fleetkeeper.exe" in page)
        check("page never prints the raw TypeError",
              "say('读取失败：'" not in page)

        code, ping = get(base, "/api/ping")
        data = json.loads(ping)
        check("GET /api/ping is 200", code == 200, code)
        check("ping reports a live pid", data.get("ok") is True and data.get("pid", 0) > 0, data)

        code, state = get(base, "/api/state")
        check("GET /api/state is 200", code == 200, code)
        check("state carries service rows",
              json.loads(state)["service_count"] >= 1, state[:200])

        code, report = get(base, "/report")
        check("GET /report is 200", code == 200, code)

        try:
            post(base, "/api/stop", {})
        except urllib.error.URLError:
            # shutdown() can close the socket before the response is flushed;
            # either outcome means the process is going away.
            pass
        proc.wait(timeout=20)
        check("exe exited after /api/stop", proc.returncode is not None,
              proc.returncode)

        try:
            get(base, "/api/ping")
            refused = False
        except urllib.error.URLError:
            refused = True
        check("orphaned tab gets a refused connection (browser: Failed to fetch)",
              refused)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    print("ALL EXE CONSOLE CHECKS PASS · " + WORK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
