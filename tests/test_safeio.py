# -*- coding: utf-8 -*-
import time
import json

import pytest

import fleetkeeper.safeio as S


def test_normalize_gitbash_path(tmp_path):
    assert S.normalize("/d/foo/bar") == S.normalize("D:\\foo\\bar")


def test_normalize_case_and_slashes(tmp_path):
    a = S.normalize(str(tmp_path / "Cfg.JSON"))
    b = S.normalize(str(tmp_path / "cfg.json") + os_sep())
    assert a == b


def os_sep():
    import os
    return "\\" if os.name == "nt" else "/"


def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "cfg.json"
    S.atomic_write_text(str(p), json.dumps({"a": 1}))
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = tmp_path / "x.txt"
    S.atomic_write_text(str(p), "hello")
    leftovers = [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert leftovers == []


def test_lease_conflict_between_holders(tmp_path):
    p = tmp_path / "shared.json"
    p.write_text("{}", encoding="utf-8")
    S.acquire_lease(str(p), "agent-a", ttl=60)
    with pytest.raises(S.LeaseBusy):
        S.acquire_lease(str(p), "agent-b", ttl=60)
    S.release_lease(str(p), "agent-a")
    assert S.read_lease(str(p)) is None


def test_lease_expiry_self_heals(tmp_path):
    p = tmp_path / "shared.json"
    p.write_text("{}", encoding="utf-8")
    info = S.acquire_lease(str(p), "ghost", ttl=-1)  # already expired
    assert info["expires_at"] <= time.time()
    assert S.read_lease(str(p)) is None  # expired lease is invisible


def test_lease_renewal_keeps_baseline(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("v1", encoding="utf-8")
    S.acquire_lease(str(p), "me", ttl=60)
    p.write_text("v2", encoding="utf-8")  # third party changed it mid-lease
    info = S.acquire_lease(str(p), "me", ttl=60)  # renewal
    assert info["base_sha256"] == S.sha256_bytes(b"v1")  # NOT re-sampled
    with pytest.raises(S.Conflict):
        S.write_with_lease(str(p), b"v3", "me")


def test_write_with_lease_creates_backup(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text('{"old": true}', encoding="utf-8")
    res = S.write_with_lease(str(p), b'{"new": true}', "me")
    assert res["sha256"] == S.sha256_bytes(b'{"new": true}')
    bak = res["backup"]
    assert bak and "old" in open(bak, "rb").read().decode()


def test_write_with_lease_aborts_on_drift(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text("original", encoding="utf-8")
    S.acquire_lease(str(p), "me", ttl=60)
    p.write_text("tampered", encoding="utf-8")
    with pytest.raises(S.Conflict):
        S.write_with_lease(str(p), b"mine", "me")
    assert p.read_text(encoding="utf-8") == "tampered"  # nothing overwritten
