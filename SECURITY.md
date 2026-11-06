# Security Policy

## Supported versions

Only the latest non-prerelease version receives fixes.

| Version | Supported |
|---|---|
| 0.2.0 | yes |
| 0.1.1 | no |
| 0.1.0 | no |

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** form under the repository's Security
tab. Do not open a public issue for an exploitable problem.

Include: the affected version, the config shape that triggers it (with secrets
removed), the observed vs expected behaviour, and a minimal reproduction.
fleetkeeper's `redact.py` exists to keep keys, tokens and private-key markers out
of logs — please use it when preparing a report.

## Threat model

**What the console is.** A local control plane with no password, on purpose:
a password prompt would defeat the "double-click the exe and use it" path that
the whole project is built around. It listens on `127.0.0.1` only — no LAN, no
public interface — and it is not meant to be tunneled, proxied or exposed.

**What keeps the rest of the web out.** Binding to loopback is not enough on
its own, because the browser is shared with every page the user visits. Three
checks in `ui.py` close that gap:

| Check | Blocks |
|---|---|
| `Host` must be `127.0.0.1:PORT` / `localhost:PORT` | DNS rebinding: `evil.example` resolving to 127.0.0.1 is not a name the console answers to |
| `Origin`, when present, must be the console's own origin | a page on another site issuing a cross-site request |
| writes require `Content-Type: application/json` | "simple" cross-site POSTs (`text/plain` needs no preflight) |

`GET` routes are read-only by construction; every state change is a `POST`
behind those checks, and the body is size-capped at 64 KiB.

**Out of scope by design.**

- Any process or user already running on the machine can call the console. Do
  not run it on a host where you do not trust local processes.
- `fleet.json` is trusted input: `recovery`, `alert_command` and webhook values
  in it are executed or fetched as written. The browser cannot set those fields
  (`manage.update_service` whitelists editable keys), but the file itself can.
- Killing is PID-scoped and command-line validated (`proc.verify_pid`); there is
  deliberately no kill-by-name interface.

