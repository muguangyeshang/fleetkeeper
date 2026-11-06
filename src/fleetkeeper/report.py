# -*- coding: utf-8 -*-
"""Single-file HTML status page.

One self-contained string: inline CSS, no JS, no CDN, no external fonts, no
images. It is meant to be opened by double-clicking a file on an air-gapped
host, or dropped into a static directory served by nginx.

Every value that originates from a config file goes through html.escape --
service names come from user input and are rendered into markup, so a name
containing '<script>' must stay inert.
"""
import html
import time

LEVEL_NAMES = ("L0_CONFIG", "L1_PROCESS", "L2_PORT", "L3_PROTOCOL", "L4_BUSINESS")
LEVEL_TONE = {0: "bad", 1: "bad", 2: "warn", 3: "info", 4: "good"}

CSS = """
:root{--bg:#f5f6f8;--surface:#fff;--text:#1b1f24;--muted:#6b7280;
--border:#e3e7ec;--shadow:0 1px 2px rgba(16,24,40,.06),0 4px 12px rgba(16,24,40,.04);
--bad:#d92d20;--warn:#dc6803;--info:#175cd3;--good:#067647;--slate:#98a2b3;
--bad-bg:#fef3f2;--warn-bg:#fffaeb;--info-bg:#eff8ff;--good-bg:#ecfdf3;--slate-bg:#f2f4f7}
@media (prefers-color-scheme:dark){
:root{--bg:#0d1117;--surface:#161b22;--text:#e6edf3;--muted:#8b949e;
--border:#30363d;--shadow:none;--bad:#ff7b72;--warn:#e3b341;--info:#79c0ff;
--good:#3fb950;--slate:#6e7681;--bad-bg:#2a1416;--warn-bg:#2a2110;
--info-bg:#111d2e;--good-bg:#10251a;--slate-bg:#1c2128}}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 48px;background:var(--bg);color:var(--text);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto}
h1{font-size:21px;font-weight:650;letter-spacing:-.02em;margin:0}
.sub{color:var(--muted);font-size:13px;margin-top:5px}
.head{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;margin-bottom:22px}
.stats{display:flex;gap:10px;flex-wrap:wrap}
.stat{min-width:96px;padding:9px 14px;background:var(--surface);border:1px solid var(--border);
border-radius:10px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:20px;font-weight:650;line-height:1.2}
.stat span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.stat.bad b{color:var(--bad)}.stat.warn b{color:var(--warn)}.stat.good b{color:var(--good)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.card{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--slate);
border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);min-width:0}
.card.bad{border-left-color:var(--bad)}.card.warn{border-left-color:var(--warn)}
.card.info{border-left-color:var(--info)}.card.good{border-left-color:var(--good)}
.top{display:flex;gap:10px;align-items:baseline;justify-content:space-between;flex-wrap:wrap}
.name{font-weight:600;font-size:15px;overflow-wrap:anywhere}
.port{color:var(--muted);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600;
letter-spacing:.02em;margin-top:8px}
.pill.bad{background:var(--bad-bg);color:var(--bad)}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.info{background:var(--info-bg);color:var(--info)}.pill.good{background:var(--good-bg);color:var(--good)}
.pill.slate{background:var(--slate-bg);color:var(--muted)}
.ladder{display:flex;gap:4px;margin:14px 0 10px}
.rung{flex:1;height:5px;border-radius:3px;background:var(--slate-bg);border:1px solid var(--border)}
.rung.on.bad{background:var(--bad);border-color:var(--bad)}
.rung.on.warn{background:var(--warn);border-color:var(--warn)}
.rung.on.info{background:var(--info);border-color:var(--info)}
.rung.on.good{background:var(--good);border-color:var(--good)}
.runglabel{display:flex;justify-content:space-between;color:var(--muted);font-size:10px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace;margin-bottom:12px}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:12.5px;margin-top:10px}
.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}
ul.issues{margin:10px 0 0;padding-left:18px;font-size:12.5px}
ul.issues li{margin:3px 0;color:var(--text)}
ul.issues li::marker{color:var(--bad)}
.empty{background:var(--surface);border:1px dashed var(--border);border-radius:12px;
padding:22px;text-align:center;color:var(--muted)}
footer{margin-top:26px;color:var(--muted);font-size:12px;border-top:1px solid var(--border);padding-top:14px}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
@media(max-width:520px){body{padding:18px 12px 36px}.grid{grid-template-columns:1fr}
.head{align-items:flex-start}.stat{min-width:80px;flex:1}}
"""


def e(value):
    return html.escape("" if value is None else str(value), quote=True)


def _tone(level):
    try:
        return LEVEL_TONE.get(int(level), "slate")
    except (TypeError, ValueError):
        return "slate"


def normalize(data, generated_at=None):
    """Accept either a doctor report or a status row list -> one view model."""
    generated_at = generated_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if isinstance(data, list):
        services = []
        for r in data:
            services.append({
                "key": r.get("key"),
                "service": r.get("name") or r.get("key"),
                "port": r.get("port"),
                "level": _level_number(r.get("level")),
                "level_name": r.get("level") or LEVEL_NAMES[0],
                "issues": r.get("issues") or [],
                "evidence": r.get("evidence") or [],
                "heartbeat": r.get("heartbeat"),
                "hb_age": r.get("hb_age"),
                "pids": r.get("pids") or [],
                "recovery": None,
            })
        return {"generated_at": generated_at, "services": services,
                "p0": [], "uncovered": [], "title": "fleetkeeper status"}
    report = dict(data or {})
    for item in report.get("services", []):
        item["level"] = _level_number(item.get("level"))
        item["level_name"] = item.get("level_name") or (
            LEVEL_NAMES[item["level"]] if 0 <= item["level"] <= 4 else "UNKNOWN")
        item.setdefault("key", item.get("service"))
        item.setdefault("name", item.get("service"))
    report["generated_at"] = report.get("generated_at") or generated_at
    report["title"] = "fleetkeeper doctor"
    report.setdefault("p0", [])
    report.setdefault("uncovered", [])
    return report


def _level_number(value):
    if isinstance(value, int):
        return max(0, min(4, value))
    s = str(value or "")
    for i, name in enumerate(LEVEL_NAMES):
        if s.startswith("L{}".format(i)) or s == name:
            return i
    return 0


def _card(svc, flagged=None):
    tone = _tone(svc.get("level"))
    level = svc.get("level", 0)
    name = svc.get("service") or svc.get("name") or svc.get("key") or "?"
    rungs = "".join(
        '<div class="rung{}{}" title="{}"></div>'.format(
            " on" if i <= level else "", " " + tone if i <= level else "",
            e(LEVEL_NAMES[i]))
        for i in range(5))
    hb = svc.get("heartbeat")
    if isinstance(hb, dict):
        hb_text = "{} — {}".format(hb.get("status", "?"), hb.get("detail", ""))
    else:
        hb_text = hb or "not configured"
    pids = svc.get("pids") or []
    kv = [
        ("heartbeat", hb_text),
        ("pids", ", ".join(map(str, pids)) or "-"),
        ("recovery", svc.get("recovery") or "none"),
    ]
    evidence = svc.get("evidence") or []
    if evidence:
        kv.append(("evidence", " · ".join(map(str, evidence))))
    kv_html = '<dl class="kv">{}</dl>'.format("".join(
        "<dt>{}</dt><dd>{}</dd>".format(e(k), e(v)) for k, v in kv))
    issues = svc.get("issues") or []
    issues_html = ""
    if issues:
        issues_html = '<ul class="issues">{}</ul>'.format(
            "".join("<li>{}</li>".format(e(i)) for i in issues))
    flag = ""
    if flagged == "P0":
        flag = '<span class="pill bad">P0 · broken</span> '
    elif flagged == "??":
        flag = '<span class="pill slate">coverage gap</span> '
    return """<section class="card {tone}">
  <div class="top"><div class="name">{name}</div><div class="port">:{port}</div></div>
  <div>{flag}<span class="pill {tone}">{level_name}</span></div>
  <div class="ladder">{rungs}</div>
  <div class="runglabel"><span>L0 config</span><span>L4 business canary</span></div>
  {kv}{issues}
</section>""".format(tone=e(tone), name=e(name), port=e(svc.get("port") or "-"),
                     flag=flag, level_name=e(svc.get("level_name")),
                     rungs=rungs, kv=kv_html, issues=issues_html)


def render(data, generated_at=None):
    """Return a complete, self-contained HTML document as a string."""
    view = normalize(data, generated_at)
    services = view["services"]
    p0 = view["p0"]
    uncovered = view["uncovered"]
    # Identity comparison would break for a report reloaded from JSON (p0 and
    # services then hold distinct dict objects); key by the stable tuple.
    def _key(x):
        return (x.get("service") or x.get("name"), x.get("port"))

    p0_keys = {_key(x) for x in p0}
    unc_keys = {_key(x) for x in uncovered}
    if services:
        cards = []
        for s in services:
            k = _key(s)
            flag = "P0" if k in p0_keys else ("??" if k in unc_keys else None)
            cards.append(_card(s, flag))
        body = '<div class="grid">{}</div>'.format("".join(cards))
    else:
        body = '<div class="empty">No services in the registry. Add one to <code>registry.services</code>.</div>'

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div><h1>{title}</h1><div class="sub">generated {ts} · local, offline, no external resources</div></div>
    <div class="stats">
      <div class="stat"><b>{total}</b><span>services</span></div>
      <div class="stat bad"><b>{np0}</b><span>P0 failures</span></div>
      <div class="stat warn"><b>{nunc}</b><span>coverage gaps</span></div>
      <div class="stat good"><b>{nok}</b><span>healthy</span></div>
    </div>
  </div>
  {body}
  <footer>Evidence stops at L4 (business canary). L5 (user-visible outcome) is never auto-claimed.
  A live port proves the process is not dead; it does not prove work is being done.</footer>
</div>
</body>
</html>
""".format(title=e(view.get("title")), ts=e(view.get("generated_at")), css=CSS,
           total=len(services), np0=len(p0), nunc=len(uncovered),
           nok=max(0, len(services) - len(p0) - len(uncovered)), body=body)
