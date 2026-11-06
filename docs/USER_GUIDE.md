# User Guide

Complete usage and configuration reference for fleetkeeper. For a shortcut,
see [说明书.md](说明书.md) (Chinese, double-click-the-exe path) or
[ARCHITECTURE.md](ARCHITECTURE.md) (design and internals).

## 1. What you get

A single-process watchdog that inspects a fleet of local services, records
what it can prove, heals only when its safety gates allow it, fans alerts out,
and keeps an append-only incident timeline. It has no runtime dependencies,
no daemon, no registry entries, no scheduled tasks created behind your back,
and no outbound network calls unless you configure a webhook.

Three front ends, same core:

| Front end | Requires | Entry point |
|---|---|---|
| Browser console | nothing beyond the exe | double-click `fleetkeeper.exe` |
| Browser console from Python | Python 3.8+ | `fleetkeeper` with no arguments, or `fleetkeeper start` |
| CLI / cron | Python 3.8+ | `fleetkeeper --config fleet.json sweep` |

## 2. Install

Not published on PyPI yet. From a checkout:

```bash
python -m pip install -e .          # gives the `fleetkeeper` console command
fleetkeeper --help
```

No-install alternative:

```bash
PYTHONPATH=src python -m fleetkeeper.cli --config config/fleet.json doctor
# Windows: set PYTHONPATH=src  (once per shell)
```

Windows single-file exe, built on a machine that has Python plus PyInstaller:

```bat
scripts\build_exe.bat
```

The result is `dist\fleetkeeper.exe` (~8 MB). It runs on a machine with no
Python. `start.bat` starts the same browser console on a machine that has
Python installed. The exe is a local build artifact, not a committed file.

Verify any install:

```bash
fleetkeeper init --config fleet.json --port 59201
fleetkeeper --config fleet.json doctor
```

`init` refuses to overwrite an existing config unless `--force` is given. Its
template ships `net_probe_targets: []`, which means the network gate is off.

## 3. First run

Running `fleetkeeper` with no arguments (or double-clicking the exe) does all
of this:

1. Creates `fleet.json` next to the working config path if it does not exist.
2. Binds an HTTP console to `127.0.0.1` on an ephemeral port.
3. Opens the default browser. A console window stays open; closing it stops
   the program.
4. Scans local listening ports and fingerprints the AI services already
   running on the machine. High-confidence matches are written into
   `fleet.json` automatically while the config is still the untouched
   placeholder; the rest are offered as a checkbox list. The placeholder is
   removed as soon as at least one real service is added. A red card for any
   remaining placeholder is expected: nothing is listening on its port.
   A scenario picker and a manual name+port form remain as fallbacks.

Pass `--no-browser` to `start` when running headless. The console is a full
dashboard: a six-metric KPI strip, service cards with an L0-L4 step ladder,
a filterable timeline, an alert list, and a sidebar holding discovery results,
quick add, the scenario picker, a redacted config viewer and a log tail.

Read routes: `GET /`, `/api/ping`, `/api/state`, `/api/overview`,
`/api/history`, `/api/config` (`?view=full` for the redacted full text,
`&download=1` for an attachment), `/api/presets`,
`/api/discover`, `/api/watch`, `/api/alerts`,
`/api/logs?service=KEY&tail=40[&raw=1]`,
`/api/check?key=KEY`, `/report`.

Write routes: `POST /api/sweep`, `/api/service`, `/api/service/update`,
`/api/service/action` (`delete` / `pause` / `resume`), `/api/preset`,
`/api/discover`, `/api/heartbeat`, `/api/check`, `/api/watch`
(`start` / `stop`, interval 5-3600 s), `/api/notify-test`, `/api/stop`.

Routes that rewrite config are explicit and validated. `/api/service` edits
one entry; `/api/service/update` accepts only `name`, `port`, `health_path`,
`health_timeout`, `canary_path`, `heartbeat_period` and `log_file`;
`/api/service/action` moves entries between `registry.services` and
`registry.paused`; `/api/preset` replaces the list; `/api/discover` appends
server-side scan results identified by id; `/api/heartbeat` writes a deadman
heartbeat file. None accepts a raw config, a recovery command, an alert
webhook, an absolute path or an external URL. `/api/preset` refuses to
overwrite saved services unless `force` is true, sent only after a browser
confirm dialog; `/api/discover` only appends and skips ports already present.
`GET /api/discover` returns `configured: true` on candidates whose port is
already in `fleet.json`; those rows are never pre-selected and
`POST /api/discover` ignores their ids.

`POST /api/watch` starts an in-process loop that calls the same
`watchdog.run()` as the CLI and the scheduled task. `GET /api/watch` reports
whether it is running, its interval, run count and last error. Evidence
collection for `/api/state` and `/api/overview` runs in a bounded thread pool
(`manage.evaluate_rows`), so wall time is the slowest single probe rather
than the sum of all probes.

### 3.1 Starting scenarios

`fleetkeeper init --list-presets` prints what each template contains. The
browser console shows the same list in a drop-down.

| Name | Services | Notes |
|---|---|---|
| `empty` (default) | `Example Service` on `--port` (default 8080) | One placeholder. Save a real service over it. |
| `single` | `My Service` on `--port` | Same shape with a less confusing name. |
| `local-ai` | Ollama `:11434`, AI Gateway `:8080`, Queue Worker `:8081` | Ollama gets a real `/api/tags` canary checking `models` has at least one item. Its `health.path` is `/` on purpose: a canary pointing at the same endpoint as the health probe is rejected as protocol-only and never reaches L4. |

`11434` is Ollama's published default port, not a discovery from this host.
`config/ollama-stack.example.json` is the exact `local-ai` config checked in
for reading. A canary with no `port` key inherits its service's port
(`evidence.check_canary`). Every generated config has `net_probe_targets: []`,
which disables the WAN gate; add hosts to enable it.

## 4. CLI reference

`--config` is accepted both before and after the subcommand. Its default is
`config/fleet.json`.

```
fleetkeeper                                            open the browser console
fleetkeeper start [--port N] [--no-browser]             same console, explicit
fleetkeeper init [--config FILE] [--preset NAME] [--force]  write a runnable config
fleetkeeper init --list-presets                            show starting scenarios
fleetkeeper init --discover [--discover-timeout S]         init + autodiscovery
fleetkeeper discover [--apply] [--json] [--ports P,P]      find local AI services
fleetkeeper status [SERVICE] [--json | --html [FILE]]   evidence ladder per service
fleetkeeper doctor [--json | --html [FILE]]             fleet-wide diagnosis
fleetkeeper heartbeat SERVICE [--processed N] [--fail] [--proof ...]  write a heartbeat
fleetkeeper sweep [--json]                              one watchdog sweep (cron entry)
fleetkeeper watch [--interval S]                        sweeps in a loop
fleetkeeper history [--last 24h] [--kind alert] [--service NAME] [--limit N] [--json]
fleetkeeper logs SERVICE [--tail N] [--raw]             service logs, redacted by default
```

`--json` and `--html` are mutually exclusive. `--html` with no filename writes
the document to stdout for piping. `--proof` accepts `business`,
`process_alive`, or `protocol_only`.

### `discover`

`discover` is read-only unless `--apply` is given. It reads the OS listening
table, probes `127.0.0.1` on `/`, `/api/tags`, `/v1/models`, `/system_stats`
(GET only, 0.6 s timeout, 16 KiB body cap), and classifies each responder.
`--apply` appends only `high` confidence rows; saved services are never
overwritten and a port already in the registry is skipped. `--json --apply`
emits exactly one JSON object containing both the scan and the `apply` report.

```bash
fleetkeeper discover --ports 11434,8080,8000
fleetkeeper --config fleet.json discover --apply
fleetkeeper --config fleet.json discover --max-ports 20 --timeout 0.3 --json
```

The browser console runs the same engine on first load with an untouched
config. `GET /api/discover` scans and caches, `POST /api/discover` accepts
`{"auto": true}` or `{"ids": [...]}`. The browser sends ids, never a service
object; the server resolves them against its own cache and validates every
health path and canary before writing.

A real two-service run (one live local HTTP endpoint with a canary, one dead
port):

```
$ fleetkeeper --config fleet.json doctor
fleetkeeper doctor  2026-11-06T18:13:31+0800
====================================================================
[??] LLM Gateway            :59201  L4_BUSINESS
      heartbeat: NO_HEARTBEAT  service has no heartbeat; it cannot prove it is working
[P0] Dead Worker            :59202  L0_CONFIG
      heartbeat: NO_HEARTBEAT  service has no heartbeat; it cannot prove it is working
====================================================================
P0 failures (broken for real):
  - Dead Worker :59202
      no process listening

Insufficiently covered (not failures, but work is unproven):
  - LLM Gateway : heartbeat=NO_HEARTBEAT
  a live port proves the process is not dead; it does not prove work.
```

`??` beside `L4_BUSINESS` is intentional. A live port and a passing canary do
not prove the service completed a unit of work; the doctor refuses to call it
covered.

## 5. Configuration reference

The config is plain JSON. See `config/fleet.example.json` for a filled-out
file and `src/fleetkeeper/template.py` for exactly what `init` writes.

### 5.1 Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `registry` | object | required | Service registry, see 5.2 |
| `watchdog` | object | `{}` | Sweep, gate and notification settings, see 5.3 |
| `heartbeat_dir` | string | `runtime/heartbeat` | Heartbeat JSON files, relative to the config file |

### 5.2 `registry`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `registry.defaults` | object | `{}` | Merged into every service. Service-level keys win. Commonly `heartbeat_period`. |
| `registry.services` | object | required | Map of internal key to service definition. The key is used as the heartbeat file name. |

Each entry of `registry.services`:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | the key | Display name in reports, alerts and the timeline |
| `port` | int | none | Port to watch. A service without a port is ignored by the watchdog sweep. |
| `health` | object | `{}` | `{path, method, timeout}` for the L3 HTTP probe. Defaults `/`, `GET`, `5`. |
| `canary` | object or null | `null` | Business probe, see 5.4 |
| `recovery` | string or null | `null` | Script to launch when the port is dead. Resolved relative to the config file. Leave empty to report without healing. |
| `heartbeat_period` | int | `60` | Maximum acceptable age of a heartbeat, seconds |
| `logs.stderr` / `logs.stdout` | string | none | Log file for the `logs` command. `stderr` wins. Relative to the config file. |

`logs` output is redacted unless `--raw` is passed. The command prints a count
of matched patterns but never the matched text.

### 5.3 `watchdog`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `state_file` | string | `runtime/watchdog-state.json` | Cooldowns, consecutive-abnormal counters, alert state, outage start time |
| `threshold` | int | `2` | Consecutive bad sweeps before the first alert |
| `re_alert_interval` | int | `15` | Re-alert cadence while still failing, in sweeps |
| `heal_cooldown_seconds` | int | `180` | Per-port cooldown after a heal, seconds. Other ports are unaffected. |
| `net_probe_targets` | list of `[host, port]` | `[["223.5.5.5", 443]]` when the key is **absent** | TCP reachability targets for the WAN gate. An explicit `[]` **disables** the gate. |
| `net_probe_timeout` | float | `3.0` | Timeout per reachability probe, seconds |
| `pause_gates` | object | `{}` | Map of gate name to `{ports: [int], marker_file: string}`. If the marker exists, those ports are skipped entirely. |
| `alert_log` | string | `runtime/alerts.log` | Append-only text log, always on |
| `alert_command` | string or list | `null` | Legacy alias for `notify.command` |
| `alert_webhook` | string | `null` | Legacy alias for `notify.webhook_url` |
| `history_file` | string | `runtime/history.sqlite3` | SQLite event stream |
| `history_retention_days` | number | `30` | Events older than this are pruned on append. `0` keeps everything. |
| `notify` | object | `{}` | Outbound channels, see 5.5 |

The distinction for `net_probe_targets` matters. A generated config contains
`[]`, so a fresh install never probes a public IP and never treats an
air-gapped host as an outage. Only a config that omits the key gets the
built-in default.

### 5.4 `canary` and `expect`

```json
"canary": {
  "type": "http",
  "path": "/v1/models",
  "method": "GET",
  "host": "127.0.0.1",
  "port": null,
  "expect": { "status": 200, "min_bytes": 2, "json_path": "data", "min_items": 1 }
}
```

| Key | Default | Meaning |
|---|---|---|
| `type` | required | Only `http` is executed. Other types are reported as unsupported rather than guessed. |
| `path` | `/` | Canary path |
| `method` | `GET` | HTTP method |
| `host` | `127.0.0.1` | Canary host |
| `port` | service `port` | Omitting it is safe; it defaults to the registry port |
| `expect.status` | none | Required HTTP status |
| `expect.min_bytes` | none | Minimum response body size |
| `expect.json_path` | none | Top-level key in the JSON body |
| `expect.min_items` | none | Minimum length of that key's value |

Two behaviours are deliberate:

- If the canary points at the same host, port and path as `health`, it is
  downgraded to `protocol_only`. It cannot prove business success and the
  ladder stays at L3.
- The probe reads at most 64 KiB (`MAX_PROBE_BYTES`). `json_path` inspection
  reuses the same response, so one canary produces one HTTP request.

### 5.5 `notify`

```json
"notify": {
  "channels": ["webhook", "command"],
  "webhook_url": "http://127.0.0.1:9100/hook",
  "webhook_timeout": 3.0,
  "command": ["python", "hook_command.py"],
  "command_timeout": 10.0
}
```

| Key | Default | Meaning |
|---|---|---|
| `channels` | `["webhook", "command"]` | Channels to attempt. `"file"` is accepted as a label; that log is owned by `alert_log`. |
| `webhook_url` | `null` | HTTP POST endpoint. `null` disables the channel. |
| `webhook_timeout` | `3.0` | POST timeout, seconds |
| `command` | `null` | String or argv list. The payload arrives on **stdin**, never as a shell argument. |
| `command_timeout` | `10.0` | Subprocess timeout, seconds |

With no `notify` block at all, `sweep` behaves exactly like the 0.1.1 file-log
behaviour. A failing channel returns `error ...`, is recorded, and cannot stop
healing or the rest of the sweep.

Payload fields include `source`, `ts`, `iso`, `title`, `message`, `logged_at`,
`kind`, `level`, `service`, `port`, `issues`, `healed`, `net_ok` and
`network_down`. `service` is resolved by mapping the port in the issue line
back to `registry.services`; a multi-service incident lists all affected
names and the first port.

Values under keys matching `api_key`, `token`, `password`, `secret` are masked,
as is key-like text inside any message. Webhook URLs are stripped from error
strings before they are printed.

Minimal end-to-end receivers:

```python
# hook_receiver.py -> python hook_receiver.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        print(json.loads(body))
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 9100), H).serve_forever()
```

```python
# hook_command.py -> receives the same JSON on stdin
import sys, json
print(json.load(sys.stdin))
```

Success line to look for in `sweep` output:

```
notify[abnormal(x2)]: webhook=ok HTTP 200, command=ok exit 0
```

### 5.6 Path resolution

`heartbeat_dir`, `state_file`, `alert_log`, `history_file`,
`pause_gates.*.marker_file`, `recovery` and `logs.*` are resolved against the
**directory of the config file**, never the current working directory. This is
what makes the same config work from a cron entry, a service account, and an
interactive shell. Absolute paths are used as-is.

### 5.7 Minimal config

```json
{
  "registry": {
    "services": {
      "gateway": { "name": "LLM Gateway", "port": 8080 }
    }
  },
  "watchdog": { "net_probe_targets": [] }
}
```

That is enough for `doctor`, `status`, `sweep`, `history` and the console.
Everything else is opt-in.

## 6. Evidence ladder and heartbeats

```
L0  config exists   the service is declared
L1  process exists  a listening process was found
L2  port healthy    exactly one LISTENING process AND a TCP connect succeeds
L3  protocol        an HTTP status line came back (5xx = alive but sick)
L4  business        a canary through the real business path passes
```

L5 (a human got what they wanted) is never auto-claimed. `proc.py` prefers
`psutil.net_connections` when available and otherwise parses `netstat`; when
no PID can be recovered it emits the sentinel `-1` and records "listener
exists but unidentified" without collapsing the ladder.

To give the doctor real business liveness, add one line to the service:

```python
from fleetkeeper.heartbeat import HeartbeatWriter

hb = HeartbeatWriter("my-gateway", period=60).start()
hb.tick()          # in the request handler, after one unit of work
hb.fail(exc)       # on failure: refreshes observed_at, NOT last_success_at
```

Heartbeat states are `UP`, `FAILING`, `STALE`, `ALIVE_ONLY`, `PROTOCOL_ONLY`,
`NO_HEARTBEAT`. Weak proofs never masquerade as `UP`. `hb.fail()` leaving
`last_success_at` untouched is the whole point of a deadman switch.

## 7. Healing and its safety gates

A sweep is one `run(config_path)` call. It performs detection, gating,
healing, alert deduplication, then outbound notification and timeline writes
in a wrapper. A broken webhook or a full disk on the history DB cannot change
whether a port gets healed.

Healing is suppressed when:

| Gate | Condition | Config |
|---|---|---|
| WAN down | every reachability probe fails; issues are recorded, nothing is launched | `net_probe_targets` |
| Threshold | fewer than N consecutive bad sweeps | `threshold` |
| Per-port cooldown | this port was healed recently; other ports are independent | `heal_cooldown_seconds` |
| Pause marker | operator left a marker file for these ports | `pause_gates` |
| No recovery command | `recovery` is empty, so there is nothing to launch | service `recovery` |

Additional invariants: process operations are PID-only and validate the
command line before acting (no kill-by-name API exists); config writes use
atomic replacement plus an edit lease whose baseline hash survives renewal;
clock jumps backwards are detected so a skew cannot bypass a cooldown.

## 8. Alerts, timeline and the offline page

Timeline windows accept `90s`, `30m`, `24h`, `7d`, `all`; a bare number means
hours. Timestamps carry milliseconds, so several sweeps in the same second
stay ordered.

```bash
fleetkeeper --config fleet.json history --last 24h
fleetkeeper --config fleet.json history --last 7d --kind alert --json
fleetkeeper --config fleet.json history --last all --service "LLM Gateway"
```

Real output:

```
$ fleetkeeper --config fleet.json history --last all
TIME                    KIND    LEVEL     SERVICE                  ISSUES MESSAGE
2026-11-06 18:13:32.784 sweep   warning   Dead Worker              1      1 issue(s), 0 healed, 0 alert(s)

1 event(s) shown; total in db: 1. '--last all' shows everything.
```

`--service` matches any member of a multi-service incident. `state.json` is a
snapshot; the SQLite stream is append-only and never overwrites an event.

Offline page:

```bash
fleetkeeper --config fleet.json status --html status.html
fleetkeeper --config fleet.json doctor --html status.html
```

One file with inline CSS, `prefers-color-scheme` support, responsive cards,
a five-rung evidence bar per service, and P0 / coverage-gap badges. No CDN,
no JavaScript, no external fonts, no network calls. Config-derived text is
HTML-escaped. A generated sample is checked in at
[assets/status-page.html](assets/status-page.html).

## 9. Scheduling

One sweep is one cron entry. Windows Task Scheduler, every minute:

```
Program : python.exe
Args    : -m fleetkeeper.cli --config C:\path\config\fleet.json sweep
```

Linux/macOS cron:

```
* * * * * cd /path && python -m fleetkeeper.cli --config config/fleet.json sweep
```

For an interactive loop instead of a scheduler:

```bash
fleetkeeper --config fleet.json watch --interval 60
```

`sweep` prints issues, heal results and channel results. It prints the history
DB path only under `--json`, so cron logs stay readable.

## 10. Platform notes

| Platform | Listener source | Result |
|---|---|---|
| Windows | `netstat -ano` (`LISTENING` + PID) | full ladder |
| Linux | `netstat -tlnp` (parses `4321/python3`) | full ladder |
| macOS / no `-p` | `netstat -an` (`*.80`, no PID) | L1 reports PID unavailable; L2+ still works |
| `psutil` installed | `psutil.net_connections` | preferred everywhere |

`psutil` is optional and never a hard runtime dependency. If `netstat` is
absent and `psutil` is not installed, the fleet reports L0.

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `config not found` | no config at that path | run `fleetkeeper init --config <path>`; the error message already points here |
| Every service is L0 | no listener source available | install `psutil`, or use a system with `netstat` |
| Port answers but `??` | no heartbeat and no canary | wire `HeartbeatWriter`, or configure `canary` |
| Canary configured, still L3 | canary hits the same endpoint as `health` | move the canary to a real business path |
| `sweep` sends nothing | below `threshold`, or both notify channels are empty | run another sweep, or set `webhook_url` / `command` |
| Healing suspended forever | reachability targets all fail | set reachable targets, or `net_probe_targets: []` to disable the gate |
| Config edits ignored | a different `fleet.json` is being read | pass `--config` explicitly; paths resolve against that file's directory |
| Timeline empty | no sweep yet, or window too short | run `sweep`, then `history --last all` |
| Webhook error line contains a token | it should not | check `_short_error`; URLs are stripped and text redacted before printing |

## 12. Upgrade, stop, uninstall

Stop the browser console with its Stop button, `Ctrl+C`, or by closing the
window. Stop a `watch` loop with `Ctrl+C`.

The program registers no service, no scheduled task, no registry key and no
autostart. Upgrade means replacing the package or the exe. Uninstall means
deleting the folder plus whichever scheduler entry you created yourself.
Deleting `runtime/` resets all state; keep `history.sqlite3` if you want the
incident record.

## 13. See also

- [说明书.md](说明书.md) for the button-by-button Chinese walkthrough
- [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries, data flow and test strategy
- [README.md](../README.md) for the project overview and comparison with other tools
