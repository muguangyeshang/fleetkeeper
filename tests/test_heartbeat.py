# -*- coding: utf-8 -*-
import time

import fleetkeeper.heartbeat as H


def test_no_heartbeat_status(tmp_path):
    r = H.evaluate("ghost", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "NO_HEARTBEAT"


def test_fresh_business_heartbeat_is_up(tmp_path):
    H.write("gw", ok=True, extra={"proof": "business"}, directory=str(tmp_path))
    r = H.evaluate("gw", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "UP"
    assert r["proof"] == "business"


def test_stale_heartbeat(tmp_path):
    H.write("gw", ok=True, directory=str(tmp_path))
    # age the heartbeat file beyond 2x period
    p = H.heartbeat_path("gw", str(tmp_path))
    data = H.read("gw", str(tmp_path))
    data["observed_at"] = time.time() - 999
    import json
    open(p, "w", encoding="utf-8").write(json.dumps(data))
    r = H.evaluate("gw", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "STALE"


def test_recent_failure_exposes_immediately(tmp_path):
    H.write("gw", ok=False, extra={"last_error": "boom"}, directory=str(tmp_path))
    r = H.evaluate("gw", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "FAILING"
    assert "boom" in r["detail"]
    assert r["consecutive_failures"] == 1


def test_consecutive_failures_accumulate(tmp_path):
    H.write("gw", ok=False, directory=str(tmp_path))
    H.write("gw", ok=False, directory=str(tmp_path))
    H.write("gw", ok=True, directory=str(tmp_path))
    data = H.read("gw", str(tmp_path))
    # success resets the failure counter in the written payload
    assert data["last_ok"] is True
    assert data["consecutive_failures"] == 0
    assert data["last_failure_at"] is None


def test_process_alive_proof_is_weak(tmp_path):
    H.write("gw", ok=True, extra={"proof": "process_alive"}, directory=str(tmp_path))
    r = H.evaluate("gw", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "ALIVE_ONLY"


def test_heartbeat_running_but_no_success(tmp_path):
    # a looping process writes heartbeats but reports success long ago
    H.write("gw", ok=True, directory=str(tmp_path))
    p = H.heartbeat_path("gw", str(tmp_path))
    import json
    data = H.read("gw", str(tmp_path))
    data["last_success_at"] = time.time() - 9999
    data["last_ok"] = False
    open(p, "w", encoding="utf-8").write(json.dumps(data))
    r = H.evaluate("gw", period_seconds=60, directory=str(tmp_path))
    assert r["status"] == "FAILING"


def test_writer_thread(tmp_path):
    hb = H.HeartbeatWriter("threaded", period=1, directory=str(tmp_path))
    hb.start()
    time.sleep(1.2)
    assert H.read("threaded", str(tmp_path)) is not None
    hb.tick()
    hb.fail(RuntimeError("x"))
    hb.stop()
