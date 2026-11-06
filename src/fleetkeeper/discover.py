# -*- coding: utf-8 -*-
"""Identify local AI services instead of asking the user to type a port.

Why this exists: the first-run console still asked for a service name and a
port. The OS already knows which ports are listening, and the common local AI
products answer distinguishable read-only HTTP endpoints, so that step is work
the program can do itself.

Scope is deliberately narrow:
  * only 127.0.0.1 is contacted, never a public address
  * only GET requests to read-only paths; no POST, no webhook, no state change
  * response bodies are bounded by evidence.http_probe and never returned to
    the browser -- only a short redacted reason string crosses the boundary
  * discovery never kills, restarts or reconfigures a process; the only write
    is registry entries in fleet.json

Cost: O(P + H*R) HTTP GETs where P <= max_ports (60 listening ports),
H = ports that answered HTTP, R = 6 probe paths. Phase 1 probes "/" for every
port, phase 2 probes the product-specific paths only for ports that answered.
All probes use a bounded pool (16 workers, 0.6s connect/read timeout each),
so a normal localhost stack is classified in 1-3 seconds. The worst case is
ceil(P/16)*timeout + R*timeout when every port hangs rather than refusing.
"""
import copy
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import evidence, proc, redact, safeio, template

MAX_PORTS = 60
MAX_WORKERS = 16
DEFAULT_TIMEOUT = 0.6
PROBE_BYTES = 16384
PLACEHOLDER_KEY = "example"
PLACEHOLDER_NAME = "Example Service"

# Published defaults of the products people actually run on one machine.
# These only set probe order; a port absent from this list is still scanned.
KNOWN_PORTS = (
    11434, 8080, 8081, 8000, 8001, 3000, 5000, 5001, 7860, 7861,
    8188, 1234, 11435, 9000, 9997, 4891, 6333, 5173, 4000, 8088,
    8888, 10000, 11436, 11437, 8008, 8181, 6006, 8082, 8089, 2000,
)

# System services and databases. A GET to them yields nothing useful and can
# show up in their logs, so skip unless the caller passes --ports explicitly.
SKIP_PORTS = frozenset({
    1, 7, 9, 13, 19, 21, 22, 23, 25, 53, 80, 110, 135, 139, 143,
    443, 445, 993, 995, 1024, 1433, 1521, 3306, 3389, 5432, 5985,
    6379, 27017, 47001, 5357, 2869, 2179,
})

# Ordered: the first matching signature wins. Every probe path is read-only.
SIGNATURES = (
    {
        "kind": "ollama", "label": "Ollama", "confidence": "high",
        "health_path": "/",
        "canary": {"type": "http", "path": "/api/tags",
                   "expect": {"status": 200, "json_path": "models",
                              "min_items": 1}},
        "probes": (
            {"path": "/api/tags", "json_array_key": "models",
             "reason": "GET /api/tags 返回 models 列表"},
            {"path": "/", "contains": "ollama is running",
             "reason": "根路径返回 Ollama is running"},
        ),
    },
    {
        "kind": "openai-compatible",
        "label": "OpenAI 兼容接口", "confidence": "high",
        "health_path": "/",
        "canary": {"type": "http", "path": "/v1/models",
                   "expect": {"status": 200, "json_path": "data",
                              "min_items": 1}},
        "probes": (
            {"path": "/v1/models", "json_array_key": "data",
             "reason": "GET /v1/models 返回 data 模型列表"},
        ),
    },
    {
        "kind": "comfyui", "label": "ComfyUI", "confidence": "high",
        "health_path": "/",
        "canary": {"type": "http", "path": "/system_stats",
                   "expect": {"status": 200, "json_path": "system",
                              "min_items": 1}},
        "probes": (
            {"path": "/system_stats", "json_key": "system",
             "reason": "GET /system_stats 返回 system 字段"},
        ),
    },
    {
        "kind": "open-webui", "label": "Open WebUI", "confidence": "medium",
        "health_path": "/", "canary": None,
        "probes": (
            {"path": "/", "contains_any": ("open-webui", "open webui"),
             "reason": "页面标题含 Open WebUI"},
        ),
    },
    {
        "kind": "gradio", "label": "Gradio / text-generation-webui",
        "confidence": "medium", "health_path": "/", "canary": None,
        "probes": (
            {"path": "/", "contains": "gradio",
             "reason": "页面由 Gradio 生成"},
        ),
    },
    {
        # Last: any HTTP status line counts as protocol liveness. Never
        # auto-applied -- a random dev server must not become a "service".
        "kind": "http", "label": "未识别的 HTTP 服务", "confidence": "low",
        "health_path": "/", "canary": None,
        "probes": (
            {"path": "/", "any_status": True,
             "reason": "根路径有 HTTP 响应，但没匹配到已知 AI 服务"},
        ),
    },
)

ALLOWED_KINDS = frozenset(s["kind"] for s in SIGNATURES)

# Process image names / command lines are used only for classification. The
# raw command line may contain user paths, so it never leaves this module.
PROCESS_HINTS = (
    (re.compile(r"(?:^|[\\/\s])ollama(?:\.exe)?(?:$|\s)", re.I),
     "ollama", "Ollama"),
    (re.compile(r"lm[ _-]?studio", re.I),
     "openai-compatible", "LM Studio"),
    (re.compile(r"vllm|llama[ _.-]?(?:server|cpp)|koboldcpp|llamafile", re.I),
     "openai-compatible", "本地推理服务"),
    (re.compile(r"comfyui", re.I), "comfyui", "ComfyUI"),
    (re.compile(r"open[-_ ]?webui", re.I), "open-webui", "Open WebUI"),
    (re.compile(r"text-generation-webui|oobabooga|gradio", re.I),
     "gradio", "text-generation-webui"),
    (re.compile(r"localai|litellm|gpt4all", re.I),
     "openai-compatible", "本地 AI 网关"),
)

# Phase 1 probes "/" once per port. Phase 2 probes only the three
# product-specific JSON endpoints; HTML fingerprints reuse the phase-1 body,
# so a port costs at most 4 GETs instead of 6.
EXTRA_PATHS = ("/api/tags", "/v1/models", "/system_stats")
PROBE_PATHS = ("/",) + EXTRA_PATHS


def _probe(port, path, timeout):
    """One bounded localhost GET. Never raises: a dead port is data, not a bug."""
    try:
        return evidence.http_probe(port, path, timeout=timeout,
                                   max_bytes=PROBE_BYTES)
    except Exception as exc:
        return {"ok": False, "status": None, "reason": None, "bytes": 0,
                "snippet": "", "raw": b"", "error": str(exc)}


def _json_body(res):
    """Parse the bounded body once. Returns None when it is not JSON."""
    try:
        return json.loads(res["raw"].decode("utf-8", errors="replace"))
    except Exception:
        return None


def _rule_matches(res, rule):
    """A 200-299 status plus one declared shape. 4xx/5xx still prove HTTP
    liveness and are listed as unrecognised services, but they must never be
    mistaken for a product fingerprint (which would create a bogus canary)."""
    if not res or not res.get("ok"):
        return False
    if rule.get("any_status"):
        return True
    status = res.get("status") or 0
    if not 200 <= status < 300:
        return False
    text = res["raw"].decode("utf-8", errors="replace").lower()
    if "contains" in rule:
        return str(rule["contains"]).lower() in text
    if "contains_any" in rule:
        return any(str(x).lower() in text for x in rule["contains_any"])
    payload = _json_body(res)
    if not isinstance(payload, dict):
        return False
    if "json_array_key" in rule:
        return isinstance(payload.get(rule["json_array_key"]), list)
    if "json_key" in rule:
        return payload.get(rule["json_key"]) is not None
    return False


def _process_table():
    """{pid: {name, cmdline}} in ONE system call.

    proc.proc_info() starts a fresh `tasklist` process per PID. On a machine
    with 48 HTTP listeners that alone took ~10 seconds and returned mojibake
    for Chinese image names (tasklist writes the ANSI code page, not UTF-8).
    One bulk read fixes both. Command lines may contain user paths, so this
    table never leaves the module.
    """
    table = {}
    if proc.psutil is not None:
        try:
            for p in proc.psutil.process_iter(["pid", "name", "cmdline"]):
                info = p.info
                cmd = info.get("cmdline") or []
                table[int(info["pid"])] = {
                    "name": info.get("name") or "",
                    "cmdline": " ".join(cmd) if isinstance(cmd, (list, tuple))
                               else str(cmd),
                }
            return table
        except Exception:
            table = {}
    if os.name == "nt":
        raw = ""
        # "mbcs" is the Windows ANSI code page (GBK on zh-CN hosts); trying
        # UTF-8 first is what produced mojibake in the real scan.
        for enc in ("mbcs", "utf-8", "cp936"):
            try:
                raw = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                    timeout=10, encoding=enc, errors="replace",
                    creationflags=proc.CREATE_NO_WINDOW).stdout or ""
                break
            except Exception:
                continue
        for line in raw.splitlines():
            parts = [c.strip().strip('"') for c in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                table[int(parts[1])] = {"name": parts[0], "cmdline": ""}
    else:
        try:
            raw = subprocess.run(["ps", "-eo", "pid=,comm=,args="],
                                 capture_output=True, timeout=10,
                                 text=True, errors="replace").stdout or ""
            for line in raw.splitlines():
                bits = line.strip().split(None, 2)
                if len(bits) >= 2 and bits[0].isdigit():
                    table[int(bits[0])] = {
                        "name": os.path.basename(bits[1]),
                        "cmdline": bits[2] if len(bits) > 2 else ""}
        except Exception:
            pass
    return table


def _process_hint(port, listener_map, table=None):
    """{pid, process, kind, label} from the first recognisable listener owner.

    psutil's cmdline can contain absolute user paths; only the image basename
    is kept. On hosts without psutil the cmdline is blank, so the image name
    is all we get -- still enough for `ollama.exe`. `table` is the bulk result
    of _process_table(); None falls back to a single proc_info() for tests.
    """
    pids = listener_map.get(port) or set()
    for pid in sorted(pids, key=lambda x: int(x) if str(x).lstrip("-").isdigit() else 0):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        info = (table or {}).get(pid)
        if info is None:
            detail = proc.proc_info(pid)
            info = {"name": detail.get("name") or "",
                    "cmdline": detail.get("cmdline") or ""}
        image = os.path.basename(info.get("name") or "")
        text = "{} {}".format(image, (info.get("cmdline") or "")[:512])
        for pattern, kind, label in PROCESS_HINTS:
            if pattern.search(text):
                return {"pid": pid, "process": image or None,
                        "kind": kind, "label": label}
        return {"pid": pid, "process": image or None, "kind": None, "label": None}
    return {"pid": None, "process": None, "kind": None, "label": None}


def _candidate(port, results, hint):
    """Build one candidate, or None when the port is not an HTTP service."""
    for sig in SIGNATURES:
        hit = None
        for rule in sig["probes"]:
            res = results.get(rule["path"])
            if _rule_matches(res, rule):
                hit = (res, rule)
                break
        if not hit:
            continue
        res, rule = hit
        kind = sig["kind"]
        label = sig["label"]
        confidence = sig["confidence"]
        health_path = sig["health_path"]
        canary = copy.deepcopy(sig["canary"])
        evidence_text = rule["reason"]
        # A known process image is stronger evidence than a page body. When a
        # generic HTTP answer belongs to e.g. ollama.exe, promote it to the
        # product signature so it gets the right health path and canary.
        if hint.get("kind") == kind and hint.get("label"):
            label = hint["label"]
            confidence = "high"
            evidence_text += "；进程 {}".format(hint.get("process") or "已识别")
        elif hint.get("kind") and kind == "http":
            parent = next((s for s in SIGNATURES
                           if s["kind"] == hint["kind"]), None)
            if parent:
                kind = hint["kind"]
                label = hint["label"]
                confidence = "high"
                health_path = parent["health_path"]
                canary = copy.deepcopy(parent["canary"])
                evidence_text += "；进程 {} 是 {}".format(
                    hint.get("process") or "?", label)
        elif hint.get("process") and confidence != "low":
            evidence_text += "；进程 {}".format(hint["process"])
        # A proxy that forwards /api/tags is Ollama-compatible, not Ollama.
        # Saying "Ollama" for herdsman.exe would make the label lie.
        if kind == "ollama" and hint.get("process") \
                and "ollama" not in str(hint["process"]).lower():
            label = "Ollama 兼容接口"
            evidence_text += "（不是 ollama 进程，按兼容接口对待）"
        return {
            "id": "{}-{}".format(kind, port),
            "kind": kind,
            # The card already renders ":<port>" next to the name. Repeating
            # the port inside the name produced "OpenAI 兼容接口 :4000 :4000".
            "name": label,
            "port": int(port),
            "pid": hint.get("pid"),
            # Low-confidence rows are random local programs. Do not put their
            # image names on the page: that is host-specific information.
            "process": hint.get("process") if confidence != "low" else None,
            "confidence": confidence,
            "evidence": redact.redact(evidence_text)[:180],
            "health_path": health_path,
            "canary": canary,
            "status": res.get("status"),
            "recommended": confidence == "high",
        }
    # No product signature. A recognisable AI process with no HTTP answer is
    # still worth offering (the user can fix the health path afterwards), but
    # a random TCP listener is not.
    if hint.get("kind") and any(r.get("ok") for r in results.values() if r):
        label = hint["label"]
        return {
            "id": "{}-{}".format(hint["kind"], port),
            "kind": hint["kind"], "name": label, "port": int(port),
            "pid": hint.get("pid"), "process": hint.get("process"),
            "confidence": "medium",
            "evidence": redact.redact("进程 {} 在监听，但没返回已知接口特征".format(
                hint.get("process") or hint["kind"]))[:180],
            "health_path": "/", "canary": None, "status": None,
            "recommended": False,
        }
    return None


def _ordered_ports(listener_map, exclude_ports, max_ports, explicit_ports):
    """Known AI ports first, then ascending. Returns (ports, source)."""
    if explicit_ports:
        ports = []
        for p in explicit_ports:
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            if 1 <= p <= 65535 and p not in ports:
                ports.append(p)
        return ports[:max_ports], "explicit"
    exclude = set(exclude_ports or ())
    ports = [int(p) for p in listener_map
             if 1 <= int(p) <= 65535
             and int(p) not in exclude
             and int(p) not in SKIP_PORTS
             and int(p) >= 1024]
    rank = {p: i for i, p in enumerate(KNOWN_PORTS)}
    ports.sort(key=lambda p: (rank.get(p, len(KNOWN_PORTS)), p))
    ports = ports[:max_ports]
    if ports:
        return ports, "listeners"
    # netstat/psutil unavailable (or nothing listening): try the published
    # defaults. Refused connections are immediate, so this costs milliseconds.
    return [p for p in KNOWN_PORTS if p not in exclude][:max_ports], "known-defaults"


def scan(ports=None, exclude_ports=None, max_ports=MAX_PORTS,
         timeout=DEFAULT_TIMEOUT, listener_map=None, workers=MAX_WORKERS):
    """Scan localhost listeners and return service candidates. Never writes.

    `listener_map` is injectable for tests and for hosts where proc.listeners()
    is known to be unavailable. `ports` overrides discovery entirely.
    """
    started = time.time()
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(0.05, min(timeout, 5.0))
    workers = max(1, min(int(workers or MAX_WORKERS), 64))
    max_ports = max(1, min(int(max_ports or MAX_PORTS), 1024))

    if listener_map is None:
        try:
            listener_map = proc.listeners() or {}
        except Exception:
            listener_map = {}
    ordered, source = _ordered_ports(listener_map, exclude_ports,
                                     max_ports, ports)

    # Phase 1: one GET "/" per port, all in parallel. A refused or non-HTTP
    # port is dropped here, so phase 2 usually touches only a handful.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        root_results = dict(zip(
            ordered,
            pool.map(lambda p: _probe(p, "/", timeout), ordered)))
    http_ports = [p for p in ordered if root_results.get(p, {}).get("ok")]

    # Phase 2: one task per (port, path). Serialising per port meant one slow
    # service held a worker for R*timeout; flat concurrency bounds the whole
    # phase at ceil(H*3/workers)*timeout instead.
    tasks = [(p, path) for p in http_ports for path in EXTRA_PATHS]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(
            lambda t: _probe(t[0], t[1], timeout), tasks))
    port_results = {p: {"/": root_results.get(p)} for p in http_ports}
    for (port, path), res in zip(tasks, answers):
        port_results[port][path] = res

    # One bulk process read for all candidates, not one tasklist per port.
    table = _process_table()
    candidates = []
    for port in http_ports:
        hint = _process_hint(port, listener_map, table)
        item = _candidate(port, port_results[port], hint)
        if item:
            candidates.append(item)
    # Stable order: confident, known products first; unknown HTTP last.
    weight = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (weight.get(c["confidence"], 3), c["port"]))
    # A desktop can run a dozen /v1/models proxies. Number duplicate labels
    # so the registry and the cards distinguish them without repeating ports.
    totals = {}
    for c in candidates:
        totals[c["name"]] = totals.get(c["name"], 0) + 1
    seen = {}
    for c in candidates:
        base = c["name"]
        if totals[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            c["name"] = "{} {}".format(base, seen[base])[:64]

    return {
        "scanned_ports": ordered,
        "http_ports": http_ports,
        "candidates": candidates,
        "recommended": [c["id"] for c in candidates if c["recommended"]],
        "elapsed_ms": int((time.time() - started) * 1000),
        "source": source,
        "auto_applied": 0,
    }


def _validate(candidate):
    """Only fields produced by scan() may become registry entries. The browser
    sends ids, never a service object, but validate defensively anyway: an
    arbitrary health path or canary would later be executed by the watchdog."""
    cid = str(candidate.get("id") or "")
    if not re.fullmatch(r"[a-z0-9-]+-\d{1,5}", cid):
        raise ValueError("识别结果编号不合法")
    kind = candidate.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ValueError("识别结果类型不合法")
    try:
        port = int(candidate.get("port"))
    except (TypeError, ValueError):
        raise ValueError("识别结果端口不合法")
    if not 1 <= port <= 65535:
        raise ValueError("识别结果端口不合法")
    hp = candidate.get("health_path") or "/"
    if not isinstance(hp, str) or not hp.startswith("/") or len(hp) > 256:
        raise ValueError("识别结果健康检查路径不合法")
    canary = candidate.get("canary")
    if canary is not None:
        if not isinstance(canary, dict) or canary.get("type") != "http":
            raise ValueError("识别结果业务探针不合法")
        path = canary.get("path") or "/"
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 256:
            raise ValueError("识别结果业务探针路径不合法")
        if not isinstance(canary.get("expect") or {}, dict):
            raise ValueError("识别结果业务探针期望不合法")
    return port, hp, canary


def apply_config(path, candidates, ids=None, auto=False):
    """Append discovered services to fleet.json. Returns a short report.

    Adds only; never overwrites a saved service and never removes one, except
    the untouched first-run placeholder. `ids=None` + auto=True selects the
    high-confidence candidates, which is what the console does on first open.
    """
    path = os.path.abspath(path)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = template.default_config()
    reg = cfg.setdefault("registry", {})
    svcs = reg.setdefault("services", {})
    if not isinstance(svcs, dict):
        raise ValueError("配置里的 registry.services 不是对象")

    chosen_ids = None if ids is None else set(str(x) for x in ids)
    selected = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        if auto and chosen_ids is None:
            if not c.get("recommended"):
                continue
        elif chosen_ids is not None and c.get("id") not in chosen_ids:
            continue
        elif chosen_ids is None:
            continue
        selected.append(c)

    used_ports = {int(v.get("port")) for v in svcs.values()
                  if isinstance(v, dict) and str(v.get("port", "")).isdigit()}
    added, skipped = [], []
    for c in selected:
        port, hp, canary = _validate(c)
        if port in used_ports:
            skipped.append("{} :{} 已在配置里".format(c.get("name"), port))
            continue
        used_ports.add(port)
        key = "auto{}".format(port)
        n = 2
        while key in svcs:
            key = "auto{}_{}".format(port, n)
            n += 1
        svcs[key] = {
            "name": str(c.get("name") or c.get("kind"))[:64],
            "port": port,
            "health": {"path": hp, "method": "GET", "timeout": 3},
            "canary": copy.deepcopy(canary),
            "recovery": None,
        }
        added.append("{} :{}".format(svcs[key]["name"], port))

    placeholder_removed = False
    if added and list(svcs.keys()).count(PLACEHOLDER_KEY) == 1 \
            and (svcs.get(PLACEHOLDER_KEY) or {}).get("name") == PLACEHOLDER_NAME:
        # Only drop the placeholder when it is not one of the freshly added
        # entries (they use auto<port> keys, so it never is).
        svcs.pop(PLACEHOLDER_KEY, None)
        placeholder_removed = True

    if added:
        safeio.atomic_write_text(
            path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    if not added:
        message = "没有加入新服务：{}".format(
            "选中的端口都已在配置里" if skipped else "没有选中可自动加入的服务")
    else:
        message = "已自动识别并加入 {} 个服务：{}".format(len(added), "、".join(added))
        if placeholder_removed:
            message += "。占位的示例服务已移除"
        if skipped:
            message += "。跳过 {} 项（端口已存在）".format(len(skipped))
    return {"ok": True, "added": added, "skipped": skipped,
            "auto_applied": len(added), "message": message,
            "placeholder_removed": placeholder_removed}
