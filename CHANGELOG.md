# Changelog

All notable changes to this project are documented in this file.
Format: newest first, `Unreleased` at the top, grouped by Improvements / Bug Fixes.

## Unreleased

### Improvements
- Add CI matrix (3 operating systems, Python 3.8–3.13) and a tag-triggered
  publish workflow with PyPI Trusted Publishing and draft releases.
- Add `CONTRIBUTING.md`, `SECURITY.md`, `RELEASING.md`, issue templates and an
  optional pre-commit config.
- Add a `dev` extra so `pip install -e ".[dev]"` installs the test runner.
- `pyproject.toml`: declare supported Python versions as classifiers.
- `README.md`: badges, table of contents, changelog links to this file instead
  of a second inline copy.

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
