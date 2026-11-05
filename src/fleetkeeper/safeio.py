# -*- coding: utf-8 -*-
"""Atomic writes + edit leases + backups.

When multiple agents/scripts share the same config files, read-modify-write
races silently drop changes. This module makes three behaviors the default,
not an option:

  1. atomic write: tmp -> fsync -> os.replace; the disk always holds a
     complete old file or a complete new file
  2. edit lease: writers register a holder; conflicts raise instead of
     "last writer wins"
  3. pre-write drift check: the sha256 recorded at lease time must still
     match the file on disk, otherwise the write aborts
"""
import hashlib
import json
import os
import time

LEASE_SUFFIX = ".lease"


class LeaseBusy(Exception):
    pass


class Conflict(Exception):
    pass


def normalize(path):
    """Canonicalize a path so the same file is never mistaken for two targets.

    Every ignored difference silently bypasses lease protection:
      - relative vs absolute        -> abspath
      - slash direction / vs \\     -> normpath
      - Git Bash style /d/foo       -> D:\\foo (otherwise a phantom D:\\d\\foo)
      - case differences (Windows)  -> normcase
      - trailing slashes, . and ..
    """
    p = path
    if len(p) >= 3 and p[0] == "/" and p[1].isalpha() and p[2] == "/":
        p = "{}:\\{}".format(p[1], p[3:])
    p = os.path.abspath(p)
    return os.path.normcase(os.path.normpath(p))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    path = normalize(path)
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_bytes(path, data):
    """Write to a temp file in the same directory, then os.replace (atomic)."""
    path = normalize(path)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".{}.{}.tmp".format(os.path.basename(path), os.getpid()))
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_text(path, text, encoding="utf-8"):
    atomic_write_bytes(path, text.encode(encoding))


def backup_path_for(path):
    ts = time.strftime("%Y%m%d_%H%M%S")
    return "{}.bak_{}".format(path, ts)


def backup(path):
    """Copy the file to a timestamped .bak_; return the backup path or None
    when the source does not exist."""
    path = normalize(path)
    if not os.path.exists(path):
        return None
    dst = backup_path_for(path)
    with open(path, "rb") as src, open(dst, "wb") as out:
        for chunk in iter(lambda: src.read(65536), b""):
            out.write(chunk)
    return dst


def lease_path_for(path):
    return normalize(path) + LEASE_SUFFIX


def read_lease(path):
    """Read a lease; missing/corrupt/expired all return None (expiry
    self-heals, so a crashed writer cannot deadlock the file forever)."""
    lp = lease_path_for(path)
    if not os.path.exists(lp):
        return None
    try:
        with open(lp, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        return None
    if time.time() > float(info.get("expires_at", 0)):
        try:
            os.remove(lp)
        except OSError:
            pass
        return None
    return info


def acquire_lease(path, holder, ttl=300, expect_sha=None):
    """Register a lease. A different active holder -> LeaseBusy.

    Renewal keeps the original base_sha256 instead of re-sampling: otherwise
    "the file changed while I held the lease" would be erased by the renewal
    itself and the pre-write drift check would be defeated.
    """
    path = normalize(path)
    cur = read_lease(path)
    if cur and cur.get("holder") != holder:
        raise LeaseBusy("file held by {} (pid {}), {}s left".format(
            cur.get("holder"), cur.get("pid"),
            int(float(cur.get("expires_at", 0)) - time.time())))
    if expect_sha is not None:
        base = expect_sha
    elif cur and cur.get("holder") == holder and cur.get("base_sha256"):
        base = cur.get("base_sha256")  # renewal: keep the original baseline
    else:
        base = sha256_file(path)       # new lease: sample current content
    now = time.time()
    info = {
        "holder": holder,
        "pid": os.getpid(),
        "acquired_at": (cur or {}).get("acquired_at", now),
        "expires_at": now + ttl,
        "base_sha256": base,
    }
    atomic_write_text(lease_path_for(path), json.dumps(info, ensure_ascii=False, indent=2))
    return info


def release_lease(path, holder=None):
    lp = lease_path_for(path)
    if not os.path.exists(lp):
        return False
    if holder:
        cur = read_lease(path)
        if cur and cur.get("holder") != holder:
            raise LeaseBusy("lease held by {}; {} may not release it".format(
                cur.get("holder"), holder))
    try:
        os.remove(lp)
        return True
    except OSError:
        return False


def write_with_lease(path, data, holder, ttl=300, do_backup=True, expect_sha=None):
    """Full controlled write: lease -> backup -> drift check -> atomic write.

    Returns {"backup", "sha256", "bytes"}.
    """
    lease = acquire_lease(path, holder, ttl=ttl, expect_sha=expect_sha)
    base = lease.get("base_sha256")
    try:
        cur = sha256_file(path)
        if base != cur:
            raise Conflict("file changed after lease baseline (base {} actual {}); write aborted".format(
                (base or "")[:12], (cur or "")[:12]))
        bpath = backup(path) if (do_backup and os.path.exists(path)) else None
        atomic_write_bytes(path, data)
        return {"backup": bpath, "sha256": sha256_file(path), "bytes": len(data)}
    finally:
        release_lease(path, holder)
