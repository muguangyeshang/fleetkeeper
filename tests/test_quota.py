# -*- coding: utf-8 -*-
"""免费额度接入的解析与降级规则。

这些用例全部离线：网络层被 monkeypatch，测的是「上游长什么样 -> 我们怎么理解」，
而不是上游此刻返回什么。真实抓取另有 e2e 脚本，不进单元测试。
"""
import json
import urllib.error

import pytest

import fleetkeeper.quota as Q


def _card(name, value, status="还没领"):
    kind, worth = Q.classify(value)
    return {"id": name, "name": name, "value": value, "status": status,
            "kind": kind, "worth": worth,
            "size": Q.unit_size(value) if kind == "unit" else 0.0,
            "state": ("done" if status in Q.DONE_STATES
                      else "gone" if status in Q.GONE_STATES else "todo")}


def _board(cards, today=535.0):
    return {"asOf": "2026-11-10 09:00:00", "fetchedAt": 1.0,
            "todayTotal": today, "monthTotal": 0.0,
            "unclaimed": len(cards), "cards": cards}


def test_classify_three_kinds():
    assert Q.classify("¥334") == ("money", 334.0)
    assert Q.classify("¥1.2") == ("money", 1.2)
    assert Q.classify("免费")[0] == "free"
    assert Q.classify("3亿Token")[0] == "unit"
    assert Q.classify("200额度")[0] == "unit"


def test_unit_size_ranks_magnitudes():
    # 只用于 unit 桶内排序，不参与价格计算
    assert Q.unit_size("3亿Token") == pytest.approx(3e8)
    assert Q.unit_size("1.5万PU") == pytest.approx(1.5e4)
    assert Q.unit_size("200额度") == pytest.approx(200)
    assert Q.unit_size("免费") == 0


def test_shape_puts_cash_first_then_by_amount():
    cards = [_card("豆包", "200额度"), _card("Rime", "¥33"),
             _card("阿里云百炼", "¥334"), _card("免费甲", "免费")]
    snap = Q._shape(_board(cards), [], 14)
    assert [c["name"] for c in snap["cards"]] == ["阿里云百炼", "Rime", "豆包", "免费甲"]


def test_shape_stats_split_by_kind_and_state():
    cards = [_card("A", "¥10"), _card("B", "¥20", status="已领"),
             _card("C", "免费"), _card("D", "500积分", status="已抢完")]
    st = Q._shape(_board(cards), [], 14)["stats"]
    assert st["total"] == 4
    assert st["todo"] == 2 and st["done"] == 1 and st["gone"] == 1
    assert st["money"] == 2 and st["unit"] == 1 and st["free"] == 1
    # 只有可领的现金才计入"今天还能拿多少"
    assert st["moneySum"] == 10.0


def test_summary_omits_cards():
    snap = Q._shape(_board([_card("A", "¥10")]), [], 14)
    s = Q.summary(snap)
    assert "cards" not in s
    assert s["stats"]["money"] == 1
    assert s["todayTotal"] == 535.0


def test_summary_survives_empty_snapshot():
    s = Q.summary({})
    assert s["stats"]["total"] == 0 and s["resetCount"] == 0


def test_fetch_board_parses_react_comment_money(monkeypatch):
    page = (
        '<div aria-label="Rime ¥334，还没领，查看领取说明"></div>'
        '<div aria-label="ZCode 免费，还没领，查看领取说明"></div>'
        '<div aria-label="Rime ¥334，还没领，查看领取说明"></div>'      # 重复渲染
        '<span>今天还能领 ¥<!-- -->535</span><span>本月已领到 ¥0</span>'
        '<span>52 张，还没收</span>'
    )
    monkeypatch.setattr(Q, "_get", lambda url, **kw: page)
    b = Q.fetch_board()
    assert b["todayTotal"] == 535.0
    assert b["unclaimed"] == 52
    assert len(b["cards"]) == 2                     # 去重后两条
    rime = [c for c in b["cards"] if c["name"] == "Rime"][0]
    assert rime["kind"] == "money" and rime["worth"] == 334.0
    assert rime["dup"] == 2


def test_resets_fall_back_to_recent_on_429(monkeypatch):
    """全量历史限流时不能把面板清空：/recent 还能用，标 partial 即可。"""
    calls = []
    payload = {"events": [{
        "createdAt": "2026-11-10T03:04:00+08:00",
        "title": "重置卡已发放", "displayLabel": "重置卡发放",
        "scope": "Ultra、Team Premium", "status": "confirmed",
        "url": "https://aihot.news/claude-reset", "posts": [{"text": "正文"}],
    }]}

    def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("-resets"):
            raise urllib.error.HTTPError(url, 429, "rate limited", {}, None)
        return json.dumps(payload)

    monkeypatch.setattr(Q, "_get", fake_get)
    ev = Q.fetch_resets(days=14)
    assert any(u.endswith("/recent") for u in calls)
    # 三个产品各拿到一条，且都带着"这只是近期变更"的标记
    assert len(ev) == 3
    assert all(e["partial"] is True for e in ev)
    assert {e["product"] for e in ev} == {"Codex", "Claude", "Antigravity"}


def test_resets_keeps_other_products_when_one_fails(monkeypatch):
    def fake_get(url, **kw):
        if "claude" in url:
            raise urllib.error.URLError("boom")
        return json.dumps({"events": []})

    monkeypatch.setattr(Q, "_get", fake_get)
    ev = Q.fetch_resets(days=14)
    errs = [e for e in ev if "error" in e]
    assert len(errs) == 1 and errs[0]["product"] == "Claude"


def test_cached_never_blocks_and_marks_loading(monkeypatch, tmp_path):
    monkeypatch.setenv("FLEETKEEPER_HOME", str(tmp_path))
    started = []
    monkeypatch.setattr(Q.threading, "Thread",
                        lambda target=None, **kw: started.append(target) or type(
                            "T", (), {"start": lambda self: None})())
    snap = Q.cached()
    assert snap["loading"] is True          # 空缓存 -> 触发后台抓取
    assert snap["cards"] == []
    assert started                          # 但当前调用线程没被拖住


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEETKEEPER_HOME", str(tmp_path))
    assert Q.load_cache() == {}
    Q.save_cache({"board": {"todayTotal": 1.0}})
    assert Q.load_cache()["board"]["todayTotal"] == 1.0
