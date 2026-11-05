# -*- coding: utf-8 -*-
"""Sensitive-content scanning and redaction.

Used before logs are displayed and before config writes are accepted.
Principle: report only hit types and counts, never echo the raw secret.
"""
import re

# (rule name, regex). A hit means sensitive; redaction replaces the whole match.
PATTERNS = [
    ("bearer", re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-_\.]{12,}")),
    ("authorization_header", re.compile(
        r"(?i)[\"']?authorization[\"']?\s*[:=]\s*[\"'][^\"']{8,}[\"']")),
    ("api_key", re.compile(
        r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9\-_\.]{8,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+")),
    ("cookie_header", re.compile(r"(?i)\bcookie\s*[:=]\s*.{16,}")),
    ("password", re.compile(r"(?i)\b(passwd|password|pwd)\b\s*[:=]\s*[\"']?[^\s\"']{6,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

PLACEHOLDER = "[REDACTED]"


def scan(text):
    """Return [(rule_name, hit_count)] without echoing matched content."""
    hits = []
    for name, rx in PATTERNS:
        n = len(rx.findall(text or ""))
        if n:
            hits.append((name, n))
    return hits


def redact(text):
    """Replace sensitive fragments with a placeholder."""
    out = text or ""
    for _name, rx in PATTERNS:
        out = rx.sub(PLACEHOLDER, out)
    return out


def safe_env():
    """Environment for spawned children: force UTF-8, avoid legacy GBK console
    encoding on Windows which mangles CJK logs into mojibake."""
    import os
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
