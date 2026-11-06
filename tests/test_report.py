# -*- coding: utf-8 -*-
"""The HTML page must be self-contained, inert, and readable offline."""
import json

from fleetkeeper import report as R


def _rows():
    return [
        {"key": "gw", "name": "LLM Gateway", "port": 8080,
         "level": "L4_BUSINESS", "pids": [1234], "heartbeat": "UP",
         "hb_age": 3.2, "evidence": ["TCP connect OK", "HTTP 200 OK"],
         "issues": []},
        {"key": "llm", "name": "Local LLM", "port": 1234,
         "level": "L0_CONFIG", "pids": [], "heartbeat": "NO_HEARTBEAT",
         "hb_age": None, "evidence": [], "issues": ["no process listening"]},
    ]


def test_status_page_is_a_complete_offline_document():
    html = R.render(_rows())
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in html and "prefers-color-scheme" in html
    assert "<script" not in html.lower()
    # no external resources of any kind
    for token in ("http://", "https://", "<link ", "<img ", "@import", "url("):
        assert token not in html.lower(), token
    assert "LLM Gateway" in html
    assert ":8080" in html
    assert "L4_BUSINESS" in html
    assert "no process listening" in html
    assert "viewport" in html  # mobile
    assert html.rstrip().endswith("</html>")


def test_service_names_are_escaped():
    rows = _rows()
    rows[0]["name"] = '<script>alert("x")</script>'
    html = R.render(rows)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'alert("x")' not in html  # quotes escaped too


def test_ladder_has_five_rungs():
    html = R.render(_rows())
    assert html.count('title="L') == 10  # 2 cards x 5 rungs
    assert 'class="rung on good"' in html


def test_doctor_report_marks_p0():
    rep = {
        "generated_at": "2026-11-06T16:00:00+0800",
        "services": [
            {"service": "Dead Gateway", "port": 59101,
             "level_name": "L0_CONFIG", "issues": ["no process listening"],
             "heartbeat": {"status": "NO_HEARTBEAT", "detail": "no heartbeat"}},
            {"service": "Live Gateway", "port": 59102,
             "level_name": "L4_BUSINESS", "issues": [],
             "heartbeat": {"status": "UP", "detail": "fresh"}},
        ],
        "p0": [], "uncovered": [],
    }
    rep["p0"].append(rep["services"][0])
    html = R.render(rep)
    assert "P0 · broken" in html
    assert "fleetkeeper doctor" in html


def test_p0_survives_json_round_trip():
    """p0 entries become distinct dict objects after JSON reload; the badge
    must still land on the right card (key by value, not identity)."""
    rep = {
        "generated_at": "2026-11-06T16:00:00+0800",
        "services": [{"service": "Dead", "port": 59101, "level": "L0_CONFIG",
                      "level_name": "L0_CONFIG", "issues": ["down"],
                      "heartbeat": {"status": "NO_HEARTBEAT", "detail": ""}}],
        "p0": [], "uncovered": [],
    }
    rep["p0"].append(dict(rep["services"][0]))
    reloaded = json.loads(json.dumps(rep))
    assert "P0 · broken" in R.render(reloaded)


def test_empty_registry_renders_hint():
    html = R.render([])
    assert "No services in the registry" in html


def test_level_number_parsing():
    assert R._level_number("L3_PROTOCOL") == 3
    assert R._level_number(4) == 4
    assert R._level_number("nonsense") == 0
