# -*- coding: utf-8 -*-
"""Config templates are pure data, so these tests need no HTTP and no I/O
except the one that checks the checked-in example file stays in sync."""
import json
import os

import pytest

from fleetkeeper import evidence, template

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_default_config_is_untouched_single_placeholder():
    cfg = template.default_config()
    svcs = cfg["registry"]["services"]
    assert list(svcs) == ["example"]
    assert svcs["example"]["name"] == "Example Service"
    assert svcs["example"]["port"] == template.DEFAULT_PORT
    assert cfg["watchdog"]["net_probe_targets"] == []
    assert cfg["watchdog"]["history_retention_days"] == 30
    # The console writes this file on first run; it must be runnable as-is.
    assert json.loads(template.render(cfg)) == cfg


def test_default_config_accepts_a_port_and_shares_no_state():
    a = template.default_config(port=59201)
    b = template.default_config(port=59202)
    assert a["registry"]["services"]["example"]["port"] == 59201
    assert b["registry"]["services"]["example"]["port"] == 59202
    a["watchdog"]["threshold"] = 99
    assert b["watchdog"]["threshold"] == 2


def test_all_declared_presets_build_valid_configs():
    for name in template.PRESET_LABELS:
        cfg = template.preset_config(name)
        assert json.loads(template.render(cfg)) == cfg
        assert cfg["registry"]["services"]
        for svc in cfg["registry"]["services"].values():
            assert 1 <= svc["port"] <= 65535
            assert svc["health"]["method"] == "GET"
            assert "recovery" in svc
        assert cfg["watchdog"]["net_probe_targets"] == []


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError):
        template.preset_config("kubernetes")


def test_local_ai_preset_has_a_real_business_canary():
    cfg = template.preset_config("local-ai")
    svcs = cfg["registry"]["services"]
    assert set(svcs) == {"ollama", "gateway", "worker"}
    assert svcs["ollama"]["port"] == template.OLLAMA_PORT
    canary = svcs["ollama"]["canary"]
    assert canary["type"] == "http"
    assert canary["path"] == "/api/tags"
    # Regression: when health.path == canary.path, canary_is_protocol_only()
    # caps the ladder at L3 and the preset silently loses its only L4 proof.
    assert svcs["ollama"]["health"]["path"] != canary["path"]
    assert evidence.canary_is_protocol_only(svcs["ollama"]) is False
    # Every expect key must be one the ladder actually evaluates.
    expect = canary["expect"]
    # evidence.check_canary evaluates exactly these four keys (evidence.py:106-134).
    supported = {"status", "min_bytes", "json_path", "min_items"}
    assert set(expect) <= supported
    # No port in the canary: it inherits the service port (evidence.py:89).
    assert "port" not in canary
    assert svcs["gateway"]["port"] == 8080
    assert svcs["worker"]["health"]["path"] == "/health"


def test_preset_list_describes_every_preset():
    items = template.preset_list()
    assert [i["name"] for i in items] == list(template.PRESET_LABELS)
    for item in items:
        assert item["label"]
        assert item["services"]
        assert all("name" in s and "port" in s for s in item["services"])


def test_checked_in_example_matches_the_builtin_preset():
    path = os.path.join(ROOT, "config", "ollama-stack.example.json")
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == template.preset_config("local-ai")


def test_canary_executor_accepts_the_preset_shape():
    """A disconnected probe still proves the executor parses this canary.

    We only assert it did not reject the configuration as unsupported.
    """
    canary = template.preset_config("local-ai")["registry"]["services"]["ollama"]["canary"]
    result = evidence.check_canary(canary, timeout=0.05, default_port=1)
    assert result["configured"] is True
    assert "unsupported canary type" not in result["detail"]
