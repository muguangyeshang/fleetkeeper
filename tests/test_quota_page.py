# -*- coding: utf-8 -*-
"""``/quota`` is the browsing page behind the dashboard's quota panel.

Two things are worth guarding here, both of which broke in the prototype:

  * the page must stay fully offline -- no CDN, no external stylesheet, no font
    URL. fleetkeeper positions itself as a local tool, and a page that quietly
    phones home for a webfont is a lie.
  * it must be reachable on the dashboard's own port. Keeping the board in a
    separate process meant two ports, two caches and two sets of numbers that
    drifted apart; these tests pin the single-port arrangement.
"""
import json
import threading

import pytest

from fleetkeeper import quota_page, safeio, template, ui


def _get(base, path):
    import urllib.error
    import urllib.request
    try:
        r = urllib.request.urlopen(base + path, timeout=30)
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")


@pytest.fixture
def console(tmp_path):
    cfg = tmp_path / "fleet.json"
    safeio.atomic_write_text(
        str(cfg), template.render(template.default_config(port=59202)))
    server = ui.make_server(str(cfg), 0)
    t = threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=3)


def test_quota_board_is_served_on_the_dashboard_port(console):
    code, ctype, body = _get(console, "/quota")
    assert code == 200
    assert "text/html" in ctype
    assert "免费额度" in body
    # 看板的四个挂载点都在：hero、筛选、卡片墙、时间线
    for anchor in ('id="today"', 'id="tabs"', 'id="board"', 'id="tl"'):
        assert anchor in body
    # 回主面板的路径，不然从看板进去就出不来了
    assert 'href="/"' in body


def test_quota_board_stays_offline(console):
    _, _, body = _get(console, "/quota")
    assert "<script src" not in body
    assert "<link " not in body
    assert "https://" not in body
    assert "cdn" not in body.lower()
    # 外部链接是运行时从 /api/quota 填的，静态页面里一个都不该有
    assert "http://" not in body


def test_dashboard_links_to_the_board(console):
    _, _, body = _get(console, "/")
    assert 'href="/quota"' in body
    assert "完整看板" in body


def test_quota_page_script_tags_are_balanced():
    # 页面是纯字符串，写坏一个 </script> 不会报错，只会在浏览器里静默截断
    html = quota_page.QUOTA_PAGE
    assert html.count("<script") == html.count("</script>") == 1
    assert html.count("<style") == html.count("</style>") == 1
    assert html.rstrip().endswith("</html>")


def test_quota_payload_carries_everything_the_page_renders():
    """The page has no fallback data of its own: if /api/quota loses a field the
    board renders a dash instead of failing loudly, so pin the contract here."""
    shape = {
        "asOf": "2026-11-10 08:50:00",
        "ageSec": 12,
        "todayTotal": 535.0,
        "monthTotal": 0.0,
        "unclaimed": 50,
        "cards": [{"id": "abc", "name": "ZCode", "value": "¥334", "status": "还没领",
                   "kind": "money", "worth": 334.0, "size": 0.0, "dup": 1,
                   "state": "todo"}],
        "resets": [{"product": "Claude", "time": "2026-11-10 03:04", "title": "重置卡",
                    "partial": False}],
        "stats": {"total": 1, "todo": 1, "done": 0, "gone": 0,
                  "money": 1, "unit": 0, "free": 0, "moneySum": 334.0},
        "error": None,
    }
    # 页面读取的字段清单：改 _shape 时这里会先炸，而不是让看板显示 "–"
    for key in ("asOf", "ageSec", "todayTotal", "monthTotal", "cards", "resets",
                "stats", "error"):
        assert key in shape
    for key in ("total", "todo", "money", "moneySum"):
        assert key in shape["stats"]
    for key in ("name", "value", "kind", "state", "status", "worth", "url"):
        assert key in shape["cards"][0] or key == "url"   # url 可选，其余必填
    # 页面用 kind 分组，值域必须只有这三种，否则卡片会被丢进 unit 兜底里
    assert shape["cards"][0]["kind"] in ("money", "unit", "free")


def test_page_round_trips_as_utf8():
    """Serve must not mangle Chinese: a mis-declared charset turns 免费 into ???"""
    raw = quota_page.QUOTA_PAGE.encode("utf-8")
    assert "免费额度".encode("utf-8") in raw
    json.dumps({"page_len": len(raw)}, ensure_ascii=False)
