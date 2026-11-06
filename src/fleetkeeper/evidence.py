# -*- coding: utf-8 -*-
"""Evidence ladder for service health (L0..L4).

Core rule: a listening port is NOT a working service. Each rung collects one
more piece of proof, and the report names the exact rung reached:

  L0 config exists   the service is declared in the registry
  L1 process exists  a listening process was found and is alive
  L2 port healthy    exactly one LISTENING process AND a TCP connect succeeds
  L3 protocol        an HTTP status line came back (4xx/5xx means the service
                     responds but its business layer is broken)
  L4 business        a canary through the real business path passes
"""
import http.client
import socket

from . import proc

LEVEL_NAME = {0: "L0_CONFIG", 1: "L1_PROCESS", 2: "L2_PORT",
              3: "L3_PROTOCOL", 4: "L4_BUSINESS"}

# A probe must never let an upstream stream an unbounded body into memory.
# Canaries that need to inspect JSON are capped at the same budget; if the
# payload is truncated, json parsing fails loudly instead of guessing.
MAX_PROBE_BYTES = 65536


def tcp_probe(port, host="127.0.0.1", timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def http_probe(port, path="/", method="GET", host="127.0.0.1", timeout=5.0,
               body=None, headers=None, max_bytes=MAX_PROBE_BYTES):
    """{ok, status, reason, bytes, snippet, raw, error}; ok=True means a status
    line was received (even 4xx/5xx count as protocol-level liveness).

    `raw` is the bounded response body. Keeping it on the result means a
    canary can inspect JSON from the SAME request -- no second round trip,
    no chance of the two probes observing different states.
    """
    from . import redact
    res = {"ok": False, "status": None, "reason": None,
           "bytes": 0, "snippet": "", "raw": b"", "error": None}
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read(max_bytes)
        res.update({
            "ok": True,
            "status": resp.status,
            "reason": resp.reason,
            "bytes": len(raw),
            "raw": raw,
            "snippet": redact.redact(raw.decode("utf-8", errors="replace"))[:200],
        })
    except Exception as e:
        res["error"] = "{}: {}".format(type(e).__name__, e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return res


def check_canary(canary, timeout=8.0, default_port=None, max_bytes=MAX_PROBE_BYTES):
    """Minimal canary executor (type "http" for now).

    `default_port` is the service's registry port: a canary normally lives on
    the same host/port as its service, so forcing every config to repeat the
    port invites an omission that silently pins the ladder at L3.

    Example:
      {"type": "http", "path": "/v1/models",
       "expect": {"status": 200, "json_path": "data", "min_items": 1}}
    """
    if not canary:
        return {"configured": False, "passed": None, "detail": "没配业务探针"}
    ctype = canary.get("type")
    if ctype != "http":
        return {"configured": True, "passed": None,
                "detail": "不支持的探针类型：{}".format(ctype)}

    port = canary.get("port") or default_port
    if port is None:
        return {"configured": True, "passed": False,
                "detail": "探针和服务都没写端口"}

    p = http_probe(port, canary.get("path", "/"),
                   method=canary.get("method", "GET"),
                   host=canary.get("host", "127.0.0.1"),
                   timeout=timeout, max_bytes=max_bytes)
    if not p["ok"]:
        return {"configured": True, "passed": False,
                "detail": "业务探针请求失败：{}".format(p["error"])}

    expect = canary.get("expect") or {}
    ok = True
    detail = "HTTP {}".format(p["status"])
    if "status" in expect and p["status"] != expect["status"]:
        ok = False
        detail += "（期望 {}）".format(expect["status"])
    if "min_bytes" in expect and p["bytes"] < expect["min_bytes"]:
        ok = False
        detail += "（响应 {}B，少于 {}B）".format(p["bytes"], expect["min_bytes"])
    if "json_path" in expect:
        import json
        try:
            # Reuse the bounded body from the probe above: one request per
            # canary, and bytes are capped so a huge response cannot OOM us.
            payload = json.loads(p["raw"].decode("utf-8", errors="replace"))
            cur = payload
            for key in str(expect["json_path"]).split("."):
                if isinstance(cur, list) and key.isdigit():
                    cur = cur[int(key)]
                elif isinstance(cur, dict):
                    cur = cur.get(key)
                else:
                    cur = None
                if cur is None:
                    break
            n = len(cur) if isinstance(cur, (list, dict)) else (1 if cur is not None else 0)
            min_items = int(expect.get("min_items", 1))
            if n < min_items:
                ok = False
            detail += "（{} 命中 {} 项，要求 >={}）".format(
                expect["json_path"], n, min_items)
        except Exception as e:
            ok = False
            detail += "（解析失败：{}）".format(e)
    return {"configured": True, "passed": ok, "detail": detail}


def canary_is_protocol_only(svc):
    """A canary that hits the same endpoint as the health check cannot prove
    business success -- it just re-proves the protocol layer."""
    canary = svc.get("canary") or {}
    health = svc.get("health") or {}
    return (canary.get("type") == "http"
            and (canary.get("port") or svc.get("port")) == svc.get("port")
            and canary.get("path", "/") == health.get("path", "/")
            and canary.get("method", "GET").upper() == health.get("method", "GET").upper())


def evaluate_service(svc):
    """Full evidence collection for one registry entry."""
    name = svc.get("name") or svc.get("key")
    port = svc.get("port")
    out = {"name": name, "port": port, "level": 0, "level_name": LEVEL_NAME[0],
           "evidence": [], "issues": [], "notes": []}

    if port is None:
        out["issues"].append("配置里没有写端口")
        return out

    owners = proc.find_listener(port)
    # On systems where netstat cannot expose PIDs, find_listener reports a
    # sentinel (-1). That is still proof a listener exists; treating it as
    # "no process" would make the whole ladder collapse to L0 on POSIX.
    pids = [o["pid"] for o in owners if o.get("pid")]
    real_pids = [p for p in pids if isinstance(p, int) and p > 0]
    out["pids"] = real_pids

    if not pids:
        out["issues"].append("没有进程在监听这个端口")
        return out
    out["level"] = 1
    if real_pids:
        out["evidence"].append("进程 PID {} 正在监听 :{}".format(
            ",".join(map(str, real_pids)), port))
    else:
        out["evidence"].append("端口 :{} 有监听（netstat 拿不到 PID）".format(port))

    if len(real_pids) > 1:
        out["issues"].append("有 {} 个进程同时监听（疑似重复实例）".format(len(real_pids)))

    if not tcp_probe(port):
        out["issues"].append("TCP 连不上（端口被占但服务没响应）")
        return out
    out["level"] = 2
    out["evidence"].append("TCP 连接成功")

    health = svc.get("health") or {}
    if str(health.get("type") or "http").lower() == "tcp":
        # A non-HTTP service (MySQL, Redis, Nginx...): an HTTP GET proves
        # nothing and a 404 would brand it broken. For these, the TCP
        # connect IS the protocol layer -- the service answered in its own
        # protocol as far as a generic probe can tell. Ladder stops at L3;
        # there is no generic business canary for arbitrary protocols.
        if tcp_probe(port, timeout=float(health.get("timeout") or 3)):
            out["level"] = 3
            out["level_name"] = LEVEL_NAME[3]
            out["evidence"].append("非 HTTP 服务，TCP 握手成功")
        else:
            out["issues"].append("TCP 握手失败：服务可能刚退出或端口被别的程序占了")
        return out
    hp = http_probe(port, health.get("path", "/"),
                    method=health.get("method", "GET"),
                    timeout=health.get("timeout", 5))
    if not hp["ok"]:
        out["issues"].append("HTTP 探测失败：{}".format(hp["error"]))
        return out
    out["level"] = 3
    out["evidence"].append("HTTP {} {}".format(hp["status"], hp["reason"] or ""))
    if hp["status"] >= 500:
        out["issues"].append("服务返回 5xx，业务层不可用")
    # A 4xx from the health path is not proof of a broken service. Most
    # OpenAI-compatible gateways answer 404 or 401 on "/" while serving
    # /v1/models perfectly; the ladder already reached L3 on that status.
    # Whether it matters depends on the business probe, so the verdict is
    # deferred to the canary below instead of colouring the card red here.
    health_4xx = 400 <= hp["status"] < 500

    if canary_is_protocol_only(svc):
        canary = {"configured": True, "passed": None, "proof": "protocol_only",
                  "detail": "与健康检查同一端点，只能证明协议层"}
    else:
        canary = check_canary(svc.get("canary"), default_port=port)
    out["canary"] = canary
    if canary.get("passed") is True:
        out["level"] = 4
        out["evidence"].append("业务探针通过：{}".format(canary["detail"]))
        if health_4xx:
            out["notes"].append(
                "健康路径 {} 返回 {}，业务探测已通过，不影响判定".format(
                    health.get("path", "/"), hp["status"]))
    else:
        if canary.get("configured") and canary.get("passed") is False:
            out["issues"].append("业务探针未通过：{}".format(canary["detail"]))
        if health_4xx:
            out["issues"].append(
                "健康路径返回 {}，可能需要鉴权或路径不对".format(hp["status"]))

    out["level_name"] = LEVEL_NAME[out["level"]]
    return out
