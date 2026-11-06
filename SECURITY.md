# Security Policy

## Supported versions

Only the latest non-prerelease version receives fixes.

| Version | Supported |
|---|---|
| 0.1.1 | yes |
| 0.1.0 | no |

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** form under the repository's Security
tab. Do not open a public issue for an exploitable problem.

Include: the affected version, the config shape that triggers it (with secrets
removed), the observed vs expected behaviour, and a minimal reproduction.
fleetkeeper's `redact.py` exists to keep keys, tokens and private-key markers out
of logs — please use it when preparing a report.

## Scope notes

fleetkeeper binds to loopback by design and does not expose an unauthenticated
control plane. Recovery commands come from the local config file; treat that file
as trusted input and keep it out of world-writable locations.
