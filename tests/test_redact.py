# -*- coding: utf-8 -*-

import fleetkeeper.redact as R


def test_scan_finds_but_never_echoes():
    text = 'config: {"api_key": "supersecretvalue123", "note": "ok"}'
    hits = R.scan(text)
    names = [n for n, _ in hits]
    assert "api_key" in names
    # scan returns counts only, never the secret itself
    assert "supersecretvalue123" not in str(hits)


def test_redact_openai_key():
    out = R.redact("using sk-abcdef1234567890abcdef in prod")
    assert "sk-abcdef1234567890abcdef" not in out
    assert "[REDACTED]" in out


def test_redact_bearer():
    out = R.redact("Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in out


def test_redact_jwt():
    out = R.redact("token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


def test_redact_private_key_marker():
    out = R.redact("-----BEGIN RSA PRIVATE KEY-----")
    assert "PRIVATE KEY" not in out or "[REDACTED]" in out


def test_clean_text_untouched():
    assert R.redact("nothing sensitive here") == "nothing sensitive here"
