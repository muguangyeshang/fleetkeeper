# -*- coding: utf-8 -*-
"""Free-quota view: not "is the service alive" but "how much is left today".

fleetkeeper's evidence ladder answers whether a local AI service still works.
This module answers the neighbouring question for the same fleet: how much
free quota the upstream providers are handing out today, and which of them
just reset their limits. A gateway can sit at L4 and still be useless because
the upstream key behind it ran dry -- quota is the missing half of the story.

Two public, keyless shapes, one fetch cycle:

  * ``aihot.news/free-tokens`` — server-rendered card wall. Every grant is one
    ``aria-label="<产品> <额度>，<状态>，查看领取说明"``; the headline sums the
    money-shaped ones. The page 403s the default urllib UA, so a browser UA is
    mandatory. The rendering is React, so the currency number arrives as
    ``¥<!-- -->535`` and the regex has to eat the comment node.
  * ``aihot.news/api/v1/{codex,claude,antigravity}-resets`` — reset event
    streams. The full history is served once per 10 minutes per client; going
    faster earns a 429, so the TTL here is deliberately longer than the page's.

Hard rule: ``/api/state`` is polled every 5 seconds and must never wait on the
public internet. ``cached()`` therefore returns whatever is on hand and
refreshes in a background thread; only the CLI (``snapshot()``) blocks.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from . import safeio

BOARD_URL = "https://aihot.news/free-tokens"
API_BASE = "https://aihot.news/api/v1"
RESET_PRODUCTS = (("codex", "Codex"), ("claude", "Claude"), ("antigravity", "Antigravity"))

BOARD_TTL = 1800      # 源站每日 08:00 才换一批卡，半小时抓一次足够
RESET_TTL = 900       # 上游全量历史 10 分钟放一次，调小只会换来 429
HTTP_TIMEOUT = 20

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

STATUS_SET = ("还没领", "已领", "已抢完", "已下架", "今日已领")
CARD_RE = re.compile(r'aria-label="([^"]+?)，(' + "|".join(STATUS_SET) + r')，')
MONEY_RE = re.compile(r"¥(?:\s*<!--\s*-->)?\s*([\d,]+(?:\.\d+)?)")
UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)?\s*(Token|tokens|积分|额度|Neurons|PU|Credits|智点|颗粒|魔粒|算力)")

DONE_STATES = ("已领", "今日已领")
GONE_STATES = ("已抢完", "已下架")

_lock = threading.Lock()          # 保护进程内缓存与刷新标志
_inflight = {"board": False, "resets": False}


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------

def cache_file():
    """Cache lives outside the install dir：exe 可能装在 Program Files。"""
    base = os.environ.get("FLEETKEEPER_HOME") or os.path.join(
        os.path.expanduser("~"), ".fleetkeeper")
    return os.path.join(base, "quota.json")


def load_cache():
    path = cache_file()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data):
    path = cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        safeio.atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        # 缓存写不进去不该让面板挂掉；下次刷新会再试
        pass


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------

def _get(url, timeout=HTTP_TIMEOUT, accept="application/json"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _money_near(text, keyword, span=600):
    i = text.find(keyword)
    if i < 0:
        return None
    m = MONEY_RE.search(text[i:i + span])
    return float(m.group(1).replace(",", "")) if m else None


def classify(value):
    """'¥334' -> money, '3亿Token' -> unit, '免费' -> free. Number is the sort key."""
    v = (value or "").strip()
    m = MONEY_RE.match(v)
    if m:
        return "money", float(m.group(1).replace(",", ""))
    if "免费" in v or v == "0":
        return "free", 0.0
    return "unit", 0.0


def unit_size(value):
    """Rough magnitude for unit cards so '3亿 Token' outranks '200 额度'.

    Only used for ordering inside the unit bucket, never displayed as a price.
    """
    m = UNIT_RE.search(value or "")
    if not m:
        return 0.0
    n = float(m.group(1))
    scale = {"万": 1e4, "亿": 1e8}.get(m.group(2) or "", 1.0)
    return n * scale


def fetch_board(timeout=HTTP_TIMEOUT):
    """One pass over the card wall. Raises on network failure."""
    text = _get(BOARD_URL, timeout=timeout,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    cards, seen = [], {}
    for m in CARD_RE.finditer(text):
        label, status = m.group(1), m.group(2)
        name, _, value = label.rpartition(" ")
        if not name:                     # 没有空格时整段当名字，额度留空
            name, value = label, ""
        name = _unescape(name)
        value = _unescape(value)
        key = name + "|" + value
        if key in seen:                  # 同一张卡 SSR 里渲染多次，只留一条
            seen[key]["dup"] += 1
            continue
        kind, worth = classify(value)
        # id 必须跨进程稳定：str 的 hash() 每个进程带随机种子，前端 keyed 渲染会抖
        rec = {"id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:10],
               "name": name, "value": value, "status": status,
               "kind": kind, "worth": worth,
               "size": unit_size(value) if kind == "unit" else 0.0,
               "dup": 1,
               "state": ("done" if status in DONE_STATES
                         else "gone" if status in GONE_STATES else "todo")}
        seen[key] = rec
        cards.append(rec)

    m = re.search(r"(\d+)\s*张，还没收", text)
    unclaimed = int(m.group(1)) if m else sum(1 for c in cards if c["state"] == "todo")
    return {
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fetchedAt": time.time(),
        "todayTotal": _money_near(text, "今天还能领"),
        "monthTotal": _money_near(text, "本月已领到"),
        "unclaimed": unclaimed,
        "cards": cards,
    }


def _unescape(text):
    import html
    return html.unescape(text).strip()


def _resets_payload(prod, timeout):
    """Full history first; on 429 fall back to /recent.

    Upstream serves the full history once per 10 minutes per client and answers
    429 with a retry hint otherwise. Returning nothing on that 429 would blank
    the panel for ten minutes even though /recent still works, so the light
    endpoint is the fallback -- it only covers recent changes, hence `partial`.
    """
    try:
        return json.loads(_get("%s/%s-resets" % (API_BASE, prod), timeout=timeout)), False
    except urllib.error.HTTPError as exc:
        if exc.code != 429:
            raise
        return json.loads(_get("%s/%s-resets/recent" % (API_BASE, prod), timeout=timeout)), True


def fetch_resets(days=14, timeout=15):
    """One event stream per product; a single failing product must not sink the rest."""
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    out = []
    for prod, label in RESET_PRODUCTS:
        try:
            data, partial = _resets_payload(prod, timeout)
        except Exception as exc:                       # noqa: BLE001 单产品挂了继续
            out.append({"product": label, "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        for ev in data.get("events") or []:
            raw = ev.get("createdAt") or ev.get("confirmedAt") or ""
            try:
                ts = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            posts = ev.get("posts") or []
            out.append({
                "product": label,
                "time": ts.strftime("%m-%d %H:%M"),
                "ts": ts.timestamp(),
                "title": ev.get("title") or ev.get("displayLabel") or "",
                "scope": ev.get("scope") or (ev.get("presentation") or {}).get("audienceZh") or "",
                "status": ev.get("status") or "",
                "url": ev.get("url") or "",
                "quote": re.sub(r"\s+", " ", (posts[0].get("text") if posts else "")).strip()[:90],
                "partial": partial,
            })
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

def _stats(cards):
    buckets = {"money": 0, "unit": 0, "free": 0}
    money_sum = 0.0
    for c in cards:
        buckets[c["kind"]] = buckets.get(c["kind"], 0) + 1
        if c["kind"] == "money" and c["state"] == "todo":
            money_sum += c["worth"]
    return {
        "total": len(cards),
        "todo": sum(1 for c in cards if c["state"] == "todo"),
        "done": sum(1 for c in cards if c["state"] == "done"),
        "gone": sum(1 for c in cards if c["state"] == "gone"),
        "money": buckets["money"],
        "unit": buckets["unit"],
        "free": buckets["free"],
        "moneySum": round(money_sum, 2),
    }


def _shape(board, resets, days, error=None):
    # 排序放在数据侧：卡片墙和 CLI 表格都要「现金在前、金额大的在前」，
    # 前端再排一遍只会让两处规则慢慢走偏。unit 用估算体量排，free 垫底。
    cards = sorted(board.get("cards") or [],
                   key=lambda c: (0 if c["kind"] == "money" else 2 if c["kind"] == "free" else 1,
                                  -float(c.get("worth") or 0),
                                  -float(c.get("size") or 0),
                                  c["name"]))
    return {
        "asOf": board.get("asOf"),
        "fetchedAt": board.get("fetchedAt") or 0,
        "ageSec": int(time.time() - board["fetchedAt"]) if board.get("fetchedAt") else None,
        "todayTotal": board.get("todayTotal"),
        "monthTotal": board.get("monthTotal"),
        "unclaimed": board.get("unclaimed") or 0,
        "cards": cards,
        "resets": [r for r in (resets or []) if "error" not in r][:20],
        "resetErrors": [r for r in (resets or []) if "error" in r],
        "stats": _stats(cards),
        "days": days,
        "error": error,
        "stale": bool(error and cards),
    }


def summary(snap):
    """The slice `/api/state` carries. Cards go through their own endpoint."""
    snap = snap or {}
    return {
        "asOf": snap.get("asOf"),
        "ageSec": snap.get("ageSec"),
        "todayTotal": snap.get("todayTotal"),
        "monthTotal": snap.get("monthTotal"),
        "unclaimed": snap.get("unclaimed", 0),
        "stats": snap.get("stats") or _stats([]),
        "resetCount": len(snap.get("resets") or []),
        "lastReset": (snap.get("resets") or [{}])[0].get("time") if snap.get("resets") else None,
        "error": snap.get("error"),
        "stale": snap.get("stale", False),
    }


def snapshot(days=14, refresh=False, allow_cache=True):
    """Blocking build. CLI path; the console never calls this on a request thread."""
    cache = load_cache() if allow_cache else {}
    board = cache.get("board") or {}
    resets = cache.get("resets") or []
    error = None

    fresh_board = board.get("fetchedAt") and (time.time() - board["fetchedAt"] < BOARD_TTL)
    if refresh or not fresh_board:
        try:
            board = fetch_board()
            cache["board"] = board
            save_cache(cache)
        except Exception as exc:                       # noqa: BLE001
            error = "%s: %s" % (type(exc).__name__, str(exc)[:150])
            if not board:
                board = {}

    fresh_resets = cache.get("resetsAt") and (time.time() - cache["resetsAt"] < RESET_TTL)
    if refresh or not fresh_resets:
        fresh = fetch_resets(days=days)
        # 三个产品全挂时保留旧数据，别用空列表把面板清空
        if fresh and all("error" in r for r in fresh):
            error = error or "重置流取数失败：" + (fresh[0].get("error") or "")[:80]
        else:
            resets = fresh
            cache["resets"] = resets
            cache["resetsAt"] = time.time()
            save_cache(cache)

    return _shape(board, resets, days, error=error)


def cached(days=14):
    """Non-blocking: serve what we have, refresh in the background when stale.

    The console polls /api/state every 5s. Doing a public HTTPS request there
    would make the whole dashboard hang whenever the upstream is slow, so the
    request path only ever reads memory + disk.
    """
    with _lock:
        cache = load_cache()
        board = cache.get("board") or {}
        resets = cache.get("resets") or []
        board_stale = not (board.get("fetchedAt")
                           and time.time() - board["fetchedAt"] < BOARD_TTL)
        resets_stale = not (cache.get("resetsAt")
                            and time.time() - cache["resetsAt"] < RESET_TTL)
        start_board = board_stale and not _inflight["board"]
        start_resets = resets_stale and not _inflight["resets"]
        if start_board:
            _inflight["board"] = True
        if start_resets:
            _inflight["resets"] = True

    if start_board:
        threading.Thread(target=_bg_board, daemon=True).start()
    if start_resets:
        threading.Thread(target=_bg_resets, args=(days,), daemon=True).start()

    snap = _shape(board, resets, days,
                  error=None if board else "首次抓取中，稍等几秒")
    snap["loading"] = start_board or start_resets
    return snap


def _bg_board():
    try:
        board = fetch_board()
        with _lock:
            cache = load_cache()
            cache["board"] = board
            save_cache(cache)
    except Exception:                                  # noqa: BLE001 后台线程不能抛
        pass
    finally:
        with _lock:
            _inflight["board"] = False


def _bg_resets(days):
    try:
        fresh = fetch_resets(days=days)
        if fresh and all("error" in r for r in fresh):
            return
        with _lock:
            cache = load_cache()
            cache["resets"] = fresh
            cache["resetsAt"] = time.time()
            save_cache(cache)
    except Exception:                                  # noqa: BLE001
        pass
    finally:
        with _lock:
            _inflight["resets"] = False


# --------------------------------------------------------------------------
# 终端渲染
# --------------------------------------------------------------------------

def render_table(snap):
    lines = []
    total = snap.get("todayTotal")
    head = "免费额度 · %s" % ((snap.get("asOf") or "")[:16])
    if total is not None:
        head += "   今天还能领 ¥%d" % round(total)
    lines.append(head)
    st = snap.get("stats") or {}
    lines.append("卡池 %d 张 · 可领 %d · 现金合计 ¥%d · 重置事件 %d 条"
                 % (st.get("total", 0), st.get("todo", 0),
                    round(st.get("moneySum", 0)), len(snap.get("resets") or [])))
    if snap.get("error"):
        lines.append("注意：%s" % snap["error"])
    lines.append("")

    cards = sorted(
        snap.get("cards") or [],
        key=lambda c: (0 if c["kind"] == "money" else 1, -c.get("worth", 0), c["name"]))
    for c in cards:
        mark = {"todo": " ", "done": "✓", "gone": "×"}.get(c["state"], " ")
        lines.append("  %s %-14s %-12s %s"
                     % (mark, c["name"][:14], c["value"][:12], c["status"]))
    if not cards:
        lines.append("  （还没有数据，稍后再试或加 --refresh）")

    resets = snap.get("resets") or []
    if resets:
        lines.append("")
        lines.append("最近重置：")
        for r in resets[:8]:
            lines.append("  %s  %-12s %s" % (r.get("time", ""), r.get("product", ""),
                                             r.get("title", "")[:48]))
    return "\n".join(lines)
