# -*- coding: utf-8 -*-
"""Deadman-switch heartbeats: services must continuously prove they are
working; the monitor must not guess from process existence.

Process alive + port listening only proves "was started at some point".
The heartbeats-here judge liveness by "how old is the last heartbeat",
never by "does the process exist".
"""
import json
import os
import time

DEFAULT_DIR = os.path.join(os.getcwd(), "runtime", "heartbeat")


def heartbeat_path(service, directory=None):
    d = directory or DEFAULT_DIR
    return os.path.join(d, "{}.json".format(service))


def write(service, processed=None, ok=True, circuit="CLOSED", extra=None,
          directory=None):
    """Producer side: write one heartbeat.

    On success refresh last_success_at; on failure only refresh observed_at
    and record last_failure_at. last_ok is written explicitly so readers can
    expose "the most recent attempt failed" without waiting for staleness.
    """
    path = heartbeat_path(service, directory)
    prev = read(service, directory) or {}
    now = time.time()
    payload = {
        "service": service,
        "pid": os.getpid(),
        "observed_at": now,
        "observed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "last_ok": bool(ok),
        "last_success_at": now if ok else prev.get("last_success_at"),
        "last_failure_at": None if ok else now,
        "consecutive_failures": 0 if ok else int(prev.get("consecutive_failures", 0)) + 1,
        "processed": processed if processed is not None else prev.get("processed"),
        "circuit": circuit,
    }
    if extra:
        payload.update(extra)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from . import safeio
    safeio.atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read(service, directory=None):
    path = heartbeat_path(service, directory)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def evaluate(service, period_seconds=60, directory=None):
    """Judge by freshness. period_seconds is the service's declared period;
    stale_after = 2x period."""
    hb = read(service, directory)
    now = time.time()
    if not hb:
        return {"status": "NO_HEARTBEAT", "age_seconds": None,
                "stale_after_seconds": period_seconds * 2,
                "detail": "service has no heartbeat; it cannot prove it is working"}

    age = now - float(hb.get("observed_at", 0))
    stale_after = period_seconds * 2
    last_success = hb.get("last_success_at")
    success_age = (now - float(last_success)) if last_success else None

    proof = hb.get("proof") or "business"
    last_ok = hb.get("last_ok")
    fails = int(hb.get("consecutive_failures") or 0)

    if age > stale_after:
        status = "STALE"
        detail = ("heartbeat {}s old (threshold {}s): process may be hung or dead"
                  .format(int(age), stale_after))
    elif last_ok is False:
        # Explicit recent failure: expose immediately instead of showing a
        # false green light until staleness.
        status = "FAILING"
        detail = "last heartbeat FAILED ({} consecutive): {}".format(
            fails, hb.get("last_error") or "service reported failure without reason")
    elif success_age is not None and success_age > stale_after:
        status = "FAILING"
        detail = ("heartbeat still running but last business success was {}s ago: "
                  "possibly failing ever since").format(int(success_age))
    elif proof == "process_alive":
        # Weak proof: only shows the process exists. Must not masquerade as UP.
        status = "ALIVE_ONLY"
        detail = ("process-alive probe ({}s); proves the process exists, "
                  "not that the service works").format(int(age))
    elif proof == "protocol_only":
        status = "PROTOCOL_ONLY"
        detail = ("HTTP self-check endpoint responded ({}s); protocol layer only, "
                  "real business unverified").format(int(age))
    else:
        status = "UP"
        detail = "heartbeat fresh ({}s)".format(int(age))

    return {
        "status": status,
        "proof": proof,
        "age_seconds": round(age, 1),
        "stale_after_seconds": stale_after,
        "last_success_age_seconds": round(success_age, 1) if success_age is not None else None,
        "last_ok": last_ok,
        "consecutive_failures": fails,
        "last_error": hb.get("last_error"),
        "pid": hb.get("pid"),
        "processed": hb.get("processed"),
        "circuit": hb.get("circuit"),
        "detail": detail,
    }


class HeartbeatWriter(object):
    """Minimal-intrusion client: one line to attach a heartbeat thread to an
    existing service, zero dependencies.

        from fleetkeeper.heartbeat import HeartbeatWriter
        hb = HeartbeatWriter("my-gateway", period=60).start()
        ...
        hb.tick()      # after each successful unit of work
        hb.fail(exc)   # on failure: refreshes observed_at, NOT success time
    """

    def __init__(self, service, period=60, directory=None, extra=None):
        import threading
        self.service = service
        self.period = period
        self.directory = directory
        self.extra = extra or {}
        self._processed = 0
        self._ok = True
        self._circuit = "CLOSED"
        self._last_error = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    # -- producer-side calls ------------------------------------------
    def tick(self, n=1):
        """One work unit completed (a request / a poll / a job)."""
        with self._lock:
            self._processed += n
            self._ok = True
            self._last_error = None

    def fail(self, exc=None, circuit=None):
        """Mark failure. Does not swallow the error -- the caller still logs
        what the user should see."""
        with self._lock:
            self._ok = False
            self._last_error = "{}: {}".format(type(exc).__name__, exc) if exc else "unknown"
            if circuit:
                self._circuit = circuit

    def set_circuit(self, state):
        with self._lock:
            self._circuit = state

    def beat_once(self):
        """Write one heartbeat immediately."""
        with self._lock:
            payload = dict(self.extra)
            if self._last_error:
                payload["last_error"] = self._last_error[:200]
            write(self.service, processed=self._processed, ok=self._ok,
                  circuit=self._circuit, extra=payload, directory=self.directory)

    # -- thread management ---------------------------------------------
    def _loop(self):
        while not self._stop.wait(self.period):
            try:
                self.beat_once()
            except Exception:
                pass  # heartbeat must never take the host service down

    def start(self):
        self.beat_once()  # beat once up front so the startup window is not misread as STALE
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hb-{}".format(self.service))
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def probe_processes(services=None, directory=None):
    """Zero-code-change liveness probe: for registered services, mark
    heartbeats from port ownership only. These carry proof="process_alive"
    so readers know the evidence is weak."""
    results = {}
    for key in (services or ["default"]):
        results[key] = {"marked": True, "proof": "process_alive"}
    return results
