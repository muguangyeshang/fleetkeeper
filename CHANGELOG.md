# Changelog

All notable changes to this project are documented in this file.
Format: newest first, `Unreleased` at the top, grouped by Improvements / Bug Fixes.

## Unreleased

### Improvements
- `sweep` and the status cards answer different questions: a sweep checks
  processes and ports, the cards grade L0-L4 business evidence. A green
  "问题 0 条" beside a red L0 card looked like a bug, so the console now
  says so explicitly after a sweep when any card is still L0/L1/L2.
- Component-level acceptance harnesses: `scripts/component_acceptance.py`
  (46 checks over every HTTP route, write paths, error paths and utilities
  on a throwaway server) and `scripts/verify_console_errors.js` (14 checks
  executing the page's own JS in a DOM stub).
- A dead-process diagnosis path in the browser console: `GET /api/ping` is a
  config-free, probe-free liveness endpoint. On a failed `/api/state` the page
  pings first, then separates "the exe exited" from "state() threw" and shows
  different recovery steps. Three consecutive failures pause the 5-second
  poll and expose a reconnect button; "Stop" clears the timer and disables
  the buttons. Non-2xx responses now surface the server's `error` field, and
  non-JSON responses are labelled as a possible port takeover. Documented in
  `说明书.md`, the README FAQ and the API table.
- `Who this is for` in the README and a matching section in `说明书.md`: name
  the intended reader (several long-lived services on one machine) and the
  readers who should use Gatus / Uptime Kuma / systemd instead. Replace the
  "绝大多数人" claim in the Chinese manual with a concrete audience.
- Built-in starting scenarios in `template.py` (`empty`, `single`, `local-ai`),
  `fleetkeeper init --preset NAME` and `init --list-presets`. `local-ai` ships
  Ollama on its published default port with a real `/api/tags` business
  canary; `config/ollama-stack.example.json` is the same config checked in.
  The Ollama health path is `/` rather than `/api/tags`: pointing both at the
  same endpoint makes `canary_is_protocol_only` withhold L4. An exe smoke run
  caught this on the first pass; `test_template.py` now locks it.
- Browser console: a first-run scenario picker that explains why the
  placeholder card is red, `GET /api/presets` and `POST /api/preset`. Applying
  a scenario over saved services requires a confirm dialog and an explicit
  `force`; the API rejects unknown, unhashable and non-whitelisted names.
- `docs/`: a full documentation set — `说明书.md` (Chinese walkthrough for
  the double-click path), `USER_GUIDE.md` (complete CLI and configuration
  reference), `ARCHITECTURE.md` (module map, sweep data flow, safety gates,
  performance limits, test strategy, extension points), `docs/README.md`
  index, and a genuinely generated `docs/assets/status-page.html` sample with
  zero external references.
- `README.md`: add a real terminal transcript under "What it looks like", a
  documentation index, project layout, a comparison with Gatus / Uptime Kuma
  / Healthchecks / supervisor, an FAQ and a roadmap. Fix the mislabelled
  A/B/C install options.
- `RELEASING.md`: paste-ready GitHub About description, topics, social
  preview guidance, first-push commands and draft release notes for `v0.2.0`.
- `ui.py` + `fleetkeeper start`: a zero-dependency local browser console.
  Running `fleetkeeper` with **no arguments** (or double-clicking the exe)
  generates `fleet.json`, serves a Chinese page bound to `127.0.0.1` only,
  and opens the default browser. Buttons replace status / doctor / sweep /
  history / report; the page adds a service by name + port and replaces the
  generated placeholder on first save. No CDN, no external assets, no secrets
  crossing the wire (only name/port/service rows are returned).
- `scripts/exe_entry.py`, `scripts/build_exe.bat`, `start.bat`: build a
  standalone Windows one-file exe (~8 MB, runs with no Python on PATH).
  `sys.frozen` resolves its config next to the exe instead of Explorer's CWD.
  `FLEETKEEPER_NO_BROWSER=1` and `start --no-browser` support headless hosts.
- `fleetkeeper init`: a wheel install now produces a runnable `fleet.json` in
  one command. Previously the README told users to copy a file that only
  exists in a repository checkout. `--port`, `--force`, and refusal to clobber
  an existing config are included.
- `--config` is accepted both before and after the subcommand.
- `notify.py`: outbound alert fan-out — webhook (stdlib `urllib`) and command
  (JSON on stdin), plus the existing file channel. Payloads are redacted by
  both content and field name; webhook URLs are stripped from error strings;
  a failing channel is isolated from the healing path. `alert_command` is no
  longer a dead config key.
- `history.py`: append-only sqlite event stream (`sweep` + `alert` rows),
  30-day retention configurable via `watchdog.history_retention_days`, and a
  `history` CLI command with `--last 24h|30m|7d|90s|all`, `--kind`,
  `--service`, `--limit`, `--json`.
- `report.py`: `status --html` / `doctor --html` produce one self-contained
  offline HTML page (inline CSS, light/dark, responsive, five-rung evidence
  bar, P0 badges). No JS, CDN, fonts or images; all config text is escaped.
- `README.md`: lead with the self-heal safety semantics instead of the
  evidence ladder, and document the three commands a new user actually needs.
- `template.py`: built-in config template so the console command is
  self-sufficient; covered by a test that keeps its keys in sync with the
  shipped `config/fleet.example.json`.
- 64 new tests (115 total), stable across three consecutive runs: real loopback
  HTTP console tests, a real local webhook receiver, a real subprocess command
  hook, XSS escaping, timeline retention, P0 badges after JSON round-trip,
  CLI html/history/init/start paths, and a frozen-exe double-click smoke run.
- Config-not-found errors now point at `fleetkeeper init`.
- Add CI matrix (3 operating systems, Python 3.8–3.13) and a tag-triggered
  publish workflow with PyPI Trusted Publishing and draft releases.
- Add `CONTRIBUTING.md`, `SECURITY.md`, `RELEASING.md`, issue templates and an
  optional pre-commit config.
- Add a `dev` extra so `pip install -e ".[dev]"` installs the test runner.
- `pyproject.toml`: declare supported Python versions as classifiers.
- `README.md`: badges, table of contents, changelog links to this file instead
  of a second inline copy.

### Bug Fixes
- A POST body that parsed as JSON but was not an object (array / string /
  number / null) reached `body.get(...)` and returned 500. It is now a clean
  400 with "请求体必须是 JSON 对象"; `test_ui.py` locks all four shapes.
- An orphaned browser tab printed the raw `TypeError: Failed to fetch` every
  5 seconds after the exe window was closed (or after "Stop" was pressed).
  Ports are OS-assigned on each launch, so a restarted exe never reclaims the
  old URL. The page now explains the cause in Chinese, tells the user to
  reopen the URL printed by the new window, and stops hammering the dead port.
- `watchdog._run_core()` used `wcfg.get("net_probe_targets") or [DEFAULT]`,
  so an explicit `[]` was silently replaced with a public-IP reachability
  probe on every sweep. Both the template and the README promise that an
  empty list disables the gate; the earlier `net_gate([])` fix did not cover
  this path, and its regression test only passed while the WAN happened to be
  reachable. A missing key now falls back to the built-in target while an
  explicit `[]` disables the gate. The regression test simulates a total
  outage, so it fails if the fallback ever returns.
- A full user journey from a clean wheel install found and fixed five more
  first-run problems (see Improvements above for `init` and `--config`):
  timeline rows now carry the real service name and port instead of `fleet`,
  `--service` matches any member of a multi-service incident, timestamps show
  milliseconds so same-second sweeps stay ordered, and `sweep` no longer
  prints the DB path on every cron run.
- `net_gate([])` returned `False`, so a config with no probe targets was read
  as "WAN unreachable" and healing stayed suspended forever. An empty list now
  means the network gate is disabled; `init` generates one.
- CLI `--help` still described the project as an "evidence-based guardian";
  it now states the self-heal safety positioning used by the README and
  package metadata.

## 0.1.1 - 2026-11-06

### Improvements
- `evidence.check_canary` falls back to the service port, so the documented
  config reaches `L4_BUSINESS` without a redundant `canary.port`.
- `cli._registry` actually merges `registry.defaults` (service keys win).
- `proc._parse_netstat` is a pure parser covering Windows `LISTENING`,
  Linux `4321/python3`, and macOS port-only output.
- `README.md` states the real install methods and platform limits; version
  bumped to 0.1.1 in `pyproject.toml` and `__init__.py`.
- 10 new regression tests; 51 passed, stable across three consecutive runs.
- End-to-end: the README example config reaches `L4_BUSINESS` on a live HTTP
  fleet via `doctor`, `status`, and `sweep`.

### Bug Fixes
- `http_probe` caps the response body at 64 KiB; `json_path` inspection reuses
  the same response instead of issuing a second canary request.
- `cli._hb_dir` takes the config path explicitly; the `_load._path` module-level
  attribute that leaked state across two configs is gone.
- Unknown-PID listeners no longer collapse the evidence ladder to L0 on POSIX.
- `canary_is_protocol_only` and `evaluate_service` now agree on port fallback.

## 0.1.0 - 2026-11-05

### Improvements
- Initial extraction from a production homelab: `evidence` (L0–L4 ladder),
  `heartbeat` (deadman switch + `HeartbeatWriter`), `watchdog` (per-port heal
  cooldowns, network gate, pause gates, alert storm suppression), `safeio`
  (atomic writes + edit leases), `proc` (PID-only operations), `redact`
  (secret scanning) and `cli` (status / doctor / sweep / watch / heartbeat /
  logs).
- Pure standard library, config fully externalized, no personal data.
- 41 pytest tests including a fake live fleet over real HTTP sockets.

### Bug Fixes
- Normalize `heal_history` keys to `int` after JSON reload. In the original
  in-house watchdog the keys came back as strings, so `heal_history.get(port)`
  always missed and per-port healing cooldowns never actually applied.
