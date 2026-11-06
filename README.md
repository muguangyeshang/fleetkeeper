# fleetkeeper

[![CI](https://github.com/muguangyeshang/fleetkeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/muguangyeshang/fleetkeeper/actions/workflows/ci.yml)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

**A self-healing watchdog for local AI services, with the safety switches the
usual ones don't have.** WAN down ⇒ record and *never* heal; per-port cooldowns
instead of a global one; pause markers so an operator's "intentionally offline"
is not an incident; PID-validated process ops instead of kill-by-name; and no
auto-claim that a service works just because its port answers.

Pure standard library, zero dependencies. Windows-first, Linux supported.
One sweep is one cron entry.

```bash
fleetkeeper --config fleet.json doctor --html status.html   # offline page, open it in a browser
fleetkeeper --config fleet.json history --last 24h          # what healed itself at 4am
fleetkeeper --config fleet.json sweep                       # cron entry: detect, gate, heal, alert
```

The CI badge goes live after the repository is pushed to GitHub.

## Contents

- [Install](#install)
- [Why this exists](#why-this-exists)
- [The evidence ladder](#the-evidence-ladder)
- [Quick start](#quick-start)
- [Alerts, history and the status page](#alerts-history-and-the-status-page)
- [Config](#config)
- [Platform notes](#platform-notes)
- [CLI](#cli)
- [What it deliberately does not do](#what-it-deliberately-does-not-do)
- [Tested](#tested)
- [Changelog](#changelog)
- [Contributing](#contributing)
- [Releasing](#releasing)
- [Security](#security)
- [License](#license)

## Install

Not published on PyPI yet. Two ways, both from a checkout:

```bash
# A) editable install — gives you the `fleetkeeper` console command
python -m pip install -e .
fleetkeeper --help

# B) install nothing — copy src/fleetkeeper anywhere, or point PYTHONPATH at it
PYTHONPATH=src python -m fleetkeeper.cli --config config/fleet.json doctor
```

The Quick start below assumes A. With B, prefix each command with
`PYTHONPATH=src` (Windows: `set PYTHONPATH=src` first).

Tagging a `v*` release builds the sdist/wheel, opens a **draft** GitHub release
and publishes to PyPI via Trusted Publishing (`.github/workflows/publish.yml`).
The first publish requires registering a pending publisher on PyPI — see
[RELEASING.md](RELEASING.md).

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
| Secret redaction | `redact.py` | logs, outbound alerts and config writes are scanned; hits are counted, never echoed |
| Outbound alert fan-out | `notify.py` | webhook + command + file; a broken channel never breaks a sweep; payloads are JSON on stdin, not shell arguments |
| Append-only timeline | `history.py` | `state.json` is a snapshot; sqlite keeps every sweep and alert so an incident can be reconstructed |
| Offline status page | `report.py` | one self-contained HTML file, no CDN/JS/fonts, escapes all config-derived text |

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

A canary hitting the same endpoint as the health check is automatically
downgraded to `protocol_only`: it cannot prove business success, and
fleetkeeper refuses to pretend otherwise.

## Quick start

```bash
# 1. create a config (no checkout needed -- `init` works from a wheel install)
fleetkeeper init --config fleet.json --port 8080

# 2. see the whole fleet with evidence levels (text, JSON, or an offline HTML page)
python -m fleetkeeper.cli --config config/fleet.json doctor
python -m fleetkeeper.cli --config config/fleet.json doctor --html status.html

# 3. run one watchdog sweep (this is your cron entry)
python -m fleetkeeper.cli --config config/fleet.json sweep

# 4. look back at what happened
python -m fleetkeeper.cli --config config/fleet.json history --last 24h

# or run sweeps in a loop
python -m fleetkeeper.cli --config config/fleet.json watch --interval 60
```

`init` writes one placeholder service on `--port`; edit
`registry.services` to point it at your real ports. `net_probe_targets` is
empty in the template, which means the network gate is **off** — add probe
hosts to enable it. Re-running `init` refuses to overwrite unless `--force`
is given.

`--config` works either before or after the subcommand:
`fleetkeeper --config fleet.json doctor` and
`fleetkeeper doctor --config fleet.json` are equivalent.

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

## Alerts, history and the status page

Three things were missing from a plain port watchdog: getting told, seeing the
fleet at a glance, and answering "what happened overnight". All three are
standard-library only and default to **off** (an existing config behaves
exactly as before).

**Get told.** Add to the `watchdog` block:

```jsonc
"alert_log": "runtime/alerts.log",          // file channel (always on)
"alert_command": null,                       // legacy alias for a command
"history_file": "runtime/history.sqlite3",
"notify": {
  "channels": ["webhook", "command"],
  "webhook_url": "http://127.0.0.1:9100/hook",
  "webhook_timeout": 3.0,
  "command": ["python", "hook_command.py"],   // list or string; receives JSON on stdin
  "command_timeout": 10.0
}
```

A minimal receiver to verify the webhook end to end:

```python
# hook_receiver.py  ->  python hook_receiver.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        print(json.loads(body))          # {title, level, message, issues, healed, ts, ...}
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 9100), H).serve_forever()
```

```python
# hook_command.py  (receives the same JSON on stdin, not on the command line)
import sys, json
print(json.load(sys.stdin))
```

`sweep` prints `notify[abnormal(x2)]: webhook=ok HTTP 200` — that line is the
success criterion. Fields named `api_key` / `token` / `password` / `secret`
and any key-like text in a message are replaced with `[REDACTED]` before the
payload leaves the host; webhook URLs are stripped out of error messages. A
dead channel yields `error ...` and the sweep still heals normally.

**See the fleet.** `status --html status.html` / `doctor --html status.html`
write one self-contained file: inline CSS, dark/light via
`prefers-color-scheme`, responsive cards, a five-rung evidence bar per service,
and P0 / coverage-gap badges. No CDN, no JS, no external fonts, no network
calls — it opens by double-clicking on an offline host. `--html` with no path
prints the document to stdout for piping. Config-derived names are HTML-escaped.

**Answer "what happened at 4am".** Every sweep and every alert is appended to
`runtime/history.sqlite3`. Retention defaults to 30 days; set
`history_retention_days` in the `watchdog` block to change it (`0` keeps
everything). `state.json` remains a snapshot; the timeline never overwrites.

```bash
fleetkeeper --config fleet.json history --last 24h
fleetkeeper --config fleet.json history --last 7d --kind alert --json
fleetkeeper --config fleet.json history --last all --service "LLM Gateway"
```

Accepted windows: `90s`, `30m`, `24h`, `7d`, `all`; a bare number means hours.
Timestamps carry milliseconds so several sweeps in the same second stay
ordered. Each row names the affected service(s) — resolved from the port in
the issue line back to `registry.services` — so `--service` really filters
(and a multi-service incident still matches any one of its members). The same
service/port pair is included in webhook and command payloads.

## Config

```jsonc
{
  "registry": {
    "defaults": { "heartbeat_period": 60 },   // inherited by every service
    "services": {
      "gateway": {
        "name": "LLM Gateway", "port": 8080,
        "health": { "path": "/", "method": "GET", "timeout": 5 },
        "canary": {
          "type": "http", "path": "/v1/models",
          "expect": { "status": 200, "json_path": "data", "min_items": 1 }
        },
        "recovery": "watchdogs/gateway_watchdog.py"   // what to launch when dead
      }
    }
  },
  "watchdog": {
    "threshold": 2,                  // consecutive bad sweeps before first alert
    "re_alert_interval": 15,         // re-alert cadence (sweeps)
    "heal_cooldown_seconds": 180,    // PER PORT
    "net_probe_targets": [["1.1.1.1", 443]],
    "pause_gates": { "gateway": { "ports": [8080], "marker_file": "PAUSE_GW" } },
    "alert_log": "runtime/alerts.log",
    "history_file": "runtime/history.sqlite3",
    "history_retention_days": 30,
    "notify": {
      "channels": ["webhook", "command"],
      "webhook_url": null,             // null = channel disabled
      "command": null                  // legacy alias: "alert_command"
    }
  }
}
```

Notes that matter in practice:

- A canary may omit `port` and `host` — they default to the service's port and
  `127.0.0.1`. Omitting them used to pin the ladder at L3; the README example
  is now a regression test.
- Every probe reads at most 64 KiB of body (`MAX_PROBE_BYTES`). A canary with
  `json_path` inspects the **same** response — one request per canary, not two.
- `expect` supports `status`, `min_bytes`, and `json_path` + `min_items`.
- `registry.defaults` is merged into each service; service-level keys win.
  `heartbeat_dir`, `state_file`, `alert_log`, `history_file` and
  `pause_gates.marker_file` resolve relative to the config file, not the
  current working directory.
- `notify` is optional. With no `webhook_url` and no `command`, `sweep` behaves
  exactly like 0.1.1 (file log only). See
  [Alerts, history and the status page](#alerts-history-and-the-status-page).

## Platform notes

| Platform | Listener source | Result |
|---|---|---|
| Windows | `netstat -ano` (`LISTENING` + PID) | full ladder |
| Linux | `netstat -tlnp` (parses `4321/python3`) | full ladder |
| macOS / no `-p` | `netstat -an` (`*.80`, no PID) | L1 reports "PID unavailable"; L2+ still works |
| `psutil` installed | `psutil.net_connections` | preferred on every platform |

When no PID can be recovered, `proc.py` emits the sentinel `-1` and
`evidence.py` records "a listener exists but is unidentified" — it does not
collapse the ladder to L0. If `netstat` is absent entirely, install `psutil`
(optional, never a hard dependency) or the fleet will report L0.

## CLI

```
fleetkeeper init [--config FILE] [--port N] [--force]   write a runnable config
fleetkeeper status [SERVICE] [--json | --html [FILE]]   evidence ladder per service
fleetkeeper doctor [--json | --html [FILE]]             fleet-wide diagnosis (P0 vs uncovered)
fleetkeeper heartbeat SERVICE [--fail] [--proof ...]    write one heartbeat manually
fleetkeeper sweep [--json]                              one watchdog sweep (cron entry)
fleetkeeper watch [--interval S]                        sweeps in a loop
fleetkeeper history [--last 24h] [--kind alert] [--json]  incident timeline
fleetkeeper logs SERVICE [--tail N] [--raw]             service logs, redacted by default
```

`--html` with no filename writes the document to stdout. `--json` and
`--html` are mutually exclusive.

## What it deliberately does not do

- **No kill-by-name.** `proc.py` exposes PID-only operations and validates
  the command line against an expected substring before acting.
- **No healing during a network outage.** Recorded, never executed.
- **No "everything is fine" claims.** Uncovered services are listed as
  uncovered, not silently green.
- **No unbounded reads.** Probes cap the response body at 64 KiB.
- **No duplicate canary traffic.** JSON inspection reuses the probe response.

## Tested

88 pytest tests, including a fake live fleet over real HTTP sockets, a real
local webhook receiver and a real subprocess command hook:
cooldown persistence across green sweeps, duplicate-instance handling,
network-down suspension, pause gates, alert storm suppression, clock-skew
poisoning, lease renewal keeping its baseline, redaction of keys/JWTs/
private-key markers (including field-name-based masking in structured
payloads), HTML escaping of config-derived names, timeline retention, and
end-to-end CLI runs.

Ten regression tests (`test_regressions.py`) lock the four review findings:
README-example canary reaching L4, bounded body reads, one request per
canary, `defaults` merging, config paths not leaking between runs, Windows /
Linux / macOS netstat parsing, and unknown-PID tolerance.

```bash
python -m pytest
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) — `Unreleased` is always at the top.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: no third-party runtime
imports, every behaviour change gets a test, no machine-specific values in
config examples.

## Security

See [SECURITY.md](SECURITY.md). Only the latest non-prerelease version is
supported; vulnerabilities go through a private advisory, not a public issue.

## Releasing

See [RELEASING.md](RELEASING.md) for the version bump, tag and verification
commands.

## License

MIT
