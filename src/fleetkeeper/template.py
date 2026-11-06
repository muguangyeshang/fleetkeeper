# -*- coding: utf-8 -*-
"""Built-in config template for `fleetkeeper init`.

A wheel install does not ship the repository's config/ directory, so a new
user had nothing to copy even though the README said "cp the example config".
The template is embedded here (pure data, no I/O) so the console command is
self-sufficient: one command produces a runnable fleet.json offline.

`net_probe_targets` is intentionally empty in the generated file: no targets
means the network gate is disabled (see watchdog.net_gate). Filling in public
IPs on an air-gapped host would otherwise make every sweep look like an
outage.
"""
import json

DEFAULT_PORT = 8080


def default_config(port=DEFAULT_PORT):
    """Return a fresh config dict. Callers get their own copy (no shared state)."""
    return {
        "registry": {
            "defaults": {"heartbeat_period": 60},
            "services": {
                "example": {
                    "name": "Example Service",
                    "port": int(port),
                    "health": {"path": "/", "method": "GET", "timeout": 3},
                    "canary": None,
                    "recovery": None,
                }
            },
        },
        "watchdog": {
            "state_file": "runtime/watchdog-state.json",
            "threshold": 2,
            "re_alert_interval": 15,
            "heal_cooldown_seconds": 180,
            "net_probe_targets": [],
            "net_probe_timeout": 3.0,
            "pause_gates": {},
            "alert_log": "runtime/alerts.log",
            "alert_command": None,
            "history_file": "runtime/history.sqlite3",
            "history_retention_days": 30,
            "notify": {
                "channels": ["webhook", "command"],
                "webhook_url": None,
                "webhook_timeout": 3.0,
                "command": None,
                "command_timeout": 10.0,
            },
        },
    }


def render(cfg):
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
