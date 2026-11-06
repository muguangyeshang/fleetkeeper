# -*- coding: utf-8 -*-
"""proc.py is the only module that can end a process, so its guards are tested
against real child processes rather than mocks: a mocked kill would prove
nothing about whether the right PID dies.

Every child here is spawned by the test itself and killed by the test itself.
"""
import os
import subprocess
import sys
import time

import pytest

from fleetkeeper import proc

SLEEP_CODE = "import time; time.sleep 30".replace(" 30", "(30)")


@pytest.fixture
def child():
    """A live process whose command line we know."""
    p = subprocess.Popen([sys.executable, "-c", SLEEP_CODE])
    try:
        # Give the OS a moment to publish the process table entry.
        for _ in range(50):
            if proc.proc_info(p.pid)["alive"]:
                break
            time.sleep(0.05)
        yield p
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


# --- the guards ------------------------------------------------------------

def test_kill_refuses_system_reserved_pids():
    for pid in (0, 1, 2, 3, 4):
        with pytest.raises(proc.Refused):
            proc.kill_pid(pid)


def test_kill_refuses_a_pid_that_is_gone():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=20)
    with pytest.raises(proc.Refused):
        proc.kill_pid(p.pid)


def test_kill_refuses_when_the_command_line_does_not_match(child):
    with pytest.raises(proc.Refused) as exc:
        proc.kill_pid(child.pid, expect="this-text-is-not-in-the-command-line")
    assert "refusing" in str(exc.value)
    assert child.poll() is None, "a mismatched expect must not kill the process"


def test_kill_terminates_a_validated_process(child):
    info = proc.kill_pid(child.pid, expect="time.sleep")
    assert info["pid"] == child.pid
    child.wait(timeout=20)
    assert child.poll() is not None


def test_kill_writes_an_ops_log(tmp_path, child):
    log = tmp_path / "ops.jsonl"
    proc.kill_pid(child.pid, expect="time.sleep", log_path=str(log))
    child.wait(timeout=20)
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"action": "kill_pid"' in lines[0]
    assert str(child.pid) in lines[0]


# --- command-line plumbing -------------------------------------------------

def test_cim_table_parses_tab_separated_rows(monkeypatch):
    class Result:
        stdout = "111\tpython.exe -m http.server\n222\t\nnot-a-row\n"

    monkeypatch.setattr(proc, "subprocess",
                        type("S", (), {"run": staticmethod(lambda *a, **k: Result)}))
    monkeypatch.setattr(proc, "_CMDLINE_CACHE", {"at": 0.0, "table": {}})
    table = proc._cim_cmdlines(force=True)
    assert table == {111: "python.exe -m http.server"}
    # a second read inside the TTL must not run the command again
    runs = []

    def boom(*a, **k):
        runs.append(1)
        raise AssertionError("subprocess must not run inside the cache window")

    monkeypatch.setattr(proc, "subprocess",
                        type("S", (), {"run": staticmethod(boom)}))
    assert proc._cim_cmdlines() == {111: "python.exe -m http.server"}
    assert not runs


def test_proc_info_skips_the_command_line_on_the_hot_path(child, monkeypatch):
    """The console polls every few seconds; it must not pay a PowerShell
    launch per poll for a field it never renders."""
    def boom(*a, **k):
        raise AssertionError("proc_info() must not read command lines by default")

    monkeypatch.setattr(proc, "_cim_cmdlines", boom)
    monkeypatch.setattr(proc, "psutil", None)
    info = proc.proc_info(child.pid)
    assert info["alive"] is True
    assert info["cmdline"] is None


@pytest.mark.skipif(os.name != "nt" and not os.path.exists("/proc"),
                    reason="no way to read a command line on this host")
def test_verify_pid_reads_the_command_line_when_asked(child, monkeypatch):
    monkeypatch.setattr(proc, "psutil", None)
    monkeypatch.setattr(proc, "_CMDLINE_CACHE", {"at": 0.0, "table": {}})
    info = proc.verify_pid(child.pid, expect="time.sleep")
    assert "time.sleep" in (info.get("cmdline") or "")
