# -*- coding: utf-8 -*-
"""Built-in config templates for `fleetkeeper init` and the browser console.

A wheel install does not ship the repository's config/ directory, so a new
user had nothing to copy even though the README said "cp the example config".
Templates are embedded here (pure data, no I/O) so the console command is
self-sufficient: one command produces a runnable fleet.json offline.

`net_probe_targets` is intentionally empty in every generated file: no targets
means the network gate is disabled (see watchdog.net_gate). Filling in public
IPs on an air-gapped host would otherwise make every sweep look like an
outage.

Presets exist because a fresh install showing one red "Example Service" card
tells a first-time user nothing. A preset gives them a realistic service list
they can edit instead of an empty JSON editor.
"""
import json

DEFAULT_PORT = 8080

# 11434 is Ollama's published default port, not host-specific information.
# Keeping the real value means a user with Ollama installed sees a green card
# on the first run instead of having to guess a port number.
OLLAMA_PORT = 11434
GATEWAY_PORT = 8080
WORKER_PORT = 8081

PRESET_LABELS = {
    "empty": "空白起步，一个示例占位服务",
    "single": "单个 HTTP 服务",
    "local-ai": "本地 AI 三件套，Ollama 加网关加队列",
}


def _watchdog_block():
    """One shared watchdog section. Returns a fresh dict on every call."""
    return {
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
    }


def _service(name, port, path="/", canary=None):
    return {
        "name": name,
        "port": int(port),
        "health": {"path": path, "method": "GET", "timeout": 3},
        "canary": canary,
        "recovery": None,
    }


def default_config(port=DEFAULT_PORT):
    """Return a fresh config dict. Callers get their own copy (no shared state).

    Kept compatible with the original template: existing tests, `ensure_config`
    and the placeholder-replacement logic in ui.save_service all rely on the
    single `example` key.
    """
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
        "watchdog": _watchdog_block(),
    }


def preset_config(name, port=DEFAULT_PORT):
    """Build a config for one named scenario. Raises ValueError on unknown names.

    `port` only affects the `empty` and `single` presets; `local-ai` uses the
    published defaults of the products it names.
    """
    if name not in PRESET_LABELS:
        raise ValueError("unknown preset: {}".format(name))

    if name == "empty":
        return default_config(port=port)

    if name == "single":
        cfg = default_config(port=port)
        svc = cfg["registry"]["services"]["example"]
        svc["name"] = "My Service"
        return cfg

    # local-ai: a real Ollama install answers /api/tags with {"models": [...]}.
    # health.path MUST differ from canary.path. evidence.canary_is_protocol_only
    # deliberately refuses L4 when both hit the same endpoint -- a second
    # request to the health URL proves no more than the health check did.
    # Ollama's root path answers "Ollama is running", so use it for liveness.
    services = {
        "ollama": _service(
            "Ollama", OLLAMA_PORT, "/",
            {"type": "http", "path": "/api/tags",
             "expect": {"status": 200, "json_path": "models", "min_items": 1}}),
        "gateway": _service("AI Gateway", GATEWAY_PORT, "/"),
        "worker": _service("Queue Worker", WORKER_PORT, "/health"),
    }
    return {
        "registry": {"defaults": {"heartbeat_period": 60},
                     "services": services},
        "watchdog": _watchdog_block(),
    }


def preset_list():
    """Describe presets for the browser console. No I/O, no config paths."""
    out = []
    for name, label in PRESET_LABELS.items():
        cfg = preset_config(name)
        out.append({
            "name": name,
            "label": label,
            "services": [
                {"name": s["name"], "port": s["port"]}
                for s in cfg["registry"]["services"].values()
            ],
        })
    return out


def render(cfg):
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
