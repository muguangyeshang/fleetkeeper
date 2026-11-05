# fleetkeeper

**Evidence-based guardian for a fleet of local AI services.**
Pure standard library. Zero dependencies. Windows-first, runs anywhere Python runs.

```
pip install fleetkeeper        # or just copy the src/fleetkeeper folder
```

---

## Why this exists

A local AI stack — LLM gateways, Ollama, llama.cpp servers, ComfyUI, job
runners, scrapers — fails in ways generic tools (supervisor, NSSM, systemd,
uptime pingers) do not cover:

1. **A port is alive but the service is a zombie.** The process holds the
   listener; every TCP connect times out. "Port up" dashboards stay green
   while every request dies.
2. **A process is running but stopped working.** The main loop is wedged;
   the heartbeat writer hasn't produced a successful unit of work in hours.
   Process monitors see nothing wrong.
3. **A self-heal loop fights a network outage.** The WAN is down, so every
   health check fails, so the healer spawns the whole fleet every cycle —
   a dozen zombies and a CPU spike, then the same again when the cooldown
   expires. Against an outage, healing is noise amplification.
4. **Several agents share one config file.** Read-modify-write races
   silently drop edits; nobody knows who overwrote whom.
5. **Killing the wrong process.** Ten services run under the same
   `pythonw.exe`. Killing by image name is indiscriminate slaughter.

fleetkeeper is the distilled counter-measures — each one earned in a
production homelab running 40+ guarded services:

| Mechanism | Module | The rule it enforces |
|---|---|---|
| Evidence ladder L0→L4 | `evidence.py` | a listening port is **not** a working service |
| Deadman-switch heartbeats | `heartbeat.py` | liveness is judged by "how old is the last success", never "does the process exist" |
| Port watchdog with per-port cooldowns | `watchdog.py` | one dead port must not delay the others; cooldown must survive green sweeps |
| Network gate | `watchdog.py` | WAN down ⇒ record, **never** heal |
| Pause gates (marker files) | `watchdog.py` | a human's "intentionally offline" is not an incident |
| Alert storm suppression | `watchdog.py` | first alert at threshold N, re-alert at fixed intervals, one alert on recovery |
| Atomic writes + edit leases | `safeio.py` | concurrent writers coordinate or fail loudly; a lease keeps its baseline hash across renewal |
| PID-only process ops | `proc.py` | no kill-by-name API exists; PID ops validate the command line first |
| Secret redaction | `redact.py` | logs and config writes are scanned; hits are counted, never echoed |

## The evidence ladder

```
L0  config exists   the service is declared in the registry
L1  process exists  a listening process was found and is alive
L2  port healthy    exactly one LISTENING process AND a TCP connect succeeds
L3  protocol        an HTTP status line came back (5xx = alive but sick)
L4  business        a canary through the real business path passes
```

`fleetkeeper doctor` never claims more than it proved. A service whose port
answers but which has no heartbeat shows as `NO_HEARTBEAT` — listed as
"insufficiently covered", not healthy, not broken. Evidence stops at L4;
L5 (a human actually got what they wanted) is never auto-claimed.

## Quick start

```bash
# 1. copy the example config and point it at your services
cp config/fleet.example.json config/fleet.json

# 2. see the whole fleet with evidence levels
python -m fleetkeeper.cli --config config/fleet.json doctor

# 3. run one watchdog sweep (this is your cron entry)
python -m fleetkeeper.cli --config config/fleet.json sweep

# or run it in a loop
python -m fleetkeeper.cli --config config/fleet.json watch --interval 60
```

### Wiring a service into heartbeats (one line)

```python
from fleetkeeper.heartbeat import HeartbeatWriter

hb = HeartbeatWriter("my-gateway", period=60).start()
# ...in your request handler:
hb.tick()          # one unit of work completed
# ...on failure:
hb.fail(exc)       # refreshes observed_at, NOT last_success_at
```

The doctor then distinguishes: UP (fresh business heartbeat) /
FAILING (last attempt failed) / STALE (heartbeat too old) /
ALIVE_ONLY / PROTOCOL_ONLY (weak proofs, never masquerade as UP).

### Scheduling (Windows Task Scheduler, every minute)

```
Program : python.exe
Args    : -m fleetkeeper.cli --config C:\path\config\fleet.json sweep
```

On Linux/macOS: `* * * * * cd /path && python -m fleetkeeper.cli --config config/fleet.json sweep`

## Config

```jsonc
{
  "registry": { "services": {
    "gateway": {
      "name": "LLM Gateway", "port": 8080,
      "health": { "path": "/", "method": "GET", "timeout": 5 },
      "canary": { "type": "http", "path": "/v1/models",
                  "expect": { "status": 200, "json_path": "data", "min_items": 1 } },
      "recovery": "watchdogs/gateway_watchdog.py"   // what to launch when dead
    }
  }},
  "watchdog": {
    "threshold": 2,                  // consecutive bad sweeps before first alert
    "re_alert_interval": 15,         // re-alert cadence (sweeps)
    "heal_cooldown_seconds": 180,    // PER PORT
    "net_probe_targets": [["223.5.5.5", 443]],
    "pause_gates": { "llm": { "ports": [1234], "marker_file": "PAUSE_LLM" } },
    "alert_log": "runtime/alerts.log"
  }
}
```

The canary `expect` block supports `status`, `min_bytes`, and
`json_path` + `min_items`. A canary pointing at the same endpoint as the
health check is automatically downgraded to `protocol_only` — it cannot
prove business success, and fleetkeeper refuses to pretend otherwise.

## CLI

```
fleetkeeper status [SERVICE] [--json]    evidence ladder per service
fleetkeeper doctor [--json]              fleet-wide diagnosis (P0 vs uncovered)
fleetkeeper heartbeat SERVICE [--fail]   write one heartbeat manually
fleetkeeper sweep [--json]               one watchdog sweep (cron entry)
fleetkeeper watch [--interval S]         sweeps in a loop
fleetkeeper logs SERVICE [--tail N]      service logs, redacted by default
```

## What it deliberately does not do

- **No kill-by-name.** `proc.py` exposes PID-only operations and validates
  the command line against an expected substring before acting.
- **No healing during a network outage.** Recorded, never executed.
- **No "everything is fine" claims.** Uncovered services are listed as
  uncovered, not silently green.

## Tested

41 pytest tests, including a fake live fleet over real HTTP sockets:
cooldown persistence across green sweeps, duplicate-instance handling,
network-down suspension, pause gates, alert storm suppression, clock-skew
poisoning, lease renewal keeping its baseline, redaction of keys/JWTs/
private-key markers, and end-to-end CLI runs.

```bash
python -m pytest
```

## License

MIT
