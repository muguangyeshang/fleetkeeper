# -*- coding: utf-8 -*-
"""Local browser console for people who never open a terminal.

`fleetkeeper start` (or double-clicking the one-file exe) does three things
without any question being asked:

  1. creates a runnable fleet.json next to the program if none exists
  2. serves a single self-contained Chinese page on 127.0.0.1 only
  3. opens the default browser at that page

The page replaces every CLI command with a button. It is pure standard library:
http.server + urllib + webbrowser, no CDN, no external assets, no javascript
framework. Only the loopback interface is bound; the console is not reachable
from the network. All payloads crossing the HTTP boundary are redacted.
"""
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import cli as cli_mod
from . import evidence, heartbeat, history, redact, safeio, template, watchdog
from . import report as report_mod

MAX_BODY_BYTES = 65536
POLL_SECONDS = 5
PLACEHOLDER_KEY = "example"
PLACEHOLDER_NAME = "Example Service"

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服务守护面板 · FleetKeeper</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1f2329;--sub:#6b7280;--line:#e5e7eb;
--ok:#16794a;--warn:#9a6700;--bad:#b42318;--brand:#2563eb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:#fff;border-bottom:1px solid var(--line);padding:20px 28px;
 display:flex;align-items:center;flex-wrap:wrap;gap:12px}
header h1{font-size:20px;margin:0;font-weight:650}
header p{margin:0;color:var(--sub);font-size:13px}
#conn{margin-left:auto;font-size:12px;color:var(--ok);background:#eaf7ef;
 padding:4px 10px;border-radius:20px}
main{max-width:1080px;margin:0 auto;padding:22px 20px 60px}
section{margin-bottom:24px}
h2{font-size:16px;margin:0 0 12px;font-weight:650}
.bar{background:#fff;border:1px solid var(--line);border-radius:12px;
 padding:14px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
button{font:inherit;font-size:14px;padding:9px 16px;border-radius:8px;
 border:1px solid var(--line);background:#fff;color:var(--ink);cursor:pointer}
button:hover{border-color:var(--brand);color:var(--brand)}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
button.primary:hover{opacity:.9;color:#fff}
button.danger{margin-left:auto;color:var(--bad)}
label{font-size:13px;color:var(--sub);display:flex;align-items:center;gap:6px}
.msg{min-height:22px;margin:10px 2px 0;color:var(--brand);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px;border-left:4px solid var(--line)}
.card.ok{border-left-color:var(--ok)}.card.warn{border-left-color:var(--warn)}
.card.bad{border-left-color:var(--bad)}
.card .name{font-weight:650;font-size:15px;word-break:break-all}
.card .port{color:var(--sub);font-size:13px;margin-left:6px}
.card .level{margin-top:10px;font-size:13px}
.card .detail{color:var(--sub);font-size:12px;margin-top:6px;word-break:break-all}
.card .issue{color:var(--bad);font-size:12px;margin-top:8px;word-break:break-all}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px}
form{display:flex;flex-wrap:wrap;gap:10px}
input{font:inherit;font-size:14px;padding:9px 12px;border:1px solid var(--line);
 border-radius:8px;background:#fff;color:var(--ink)}
input[name=name]{flex:2;min-width:180px}input[name=port]{flex:1;min-width:130px}
select{font:inherit;font-size:14px;padding:9px 12px;border:1px solid var(--line);
 border-radius:8px;background:#fff;color:var(--ink);max-width:100%}
.hint{color:var(--sub);font-size:12px;margin:10px 0 0;word-break:break-all}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--sub);font-weight:500}
td.msg-cell{word-break:break-all}
.empty{color:var(--sub);font-size:13px;padding:10px 0}
details{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
summary{cursor:pointer;font-weight:600;font-size:14px}
details p{color:var(--sub);font-size:13px;margin:10px 0 0}
code{background:#f1f2f4;padding:2px 5px;border-radius:4px;font-size:12px}
</style>
</head>
<body>
<header>
  <h1>服务守护面板</h1>
  <p>只在你自己的电脑上运行 · 不上传任何数据 · 关掉程序窗口即停止</p>
  <span id="conn">已连接</span>
</header>
<main>
  <section class="panel" id="intro">
    <h2>第一次用，先选一个场景</h2>
    <p class="hint" id="intro-text"></p>
    <form id="preset-form">
      <select id="preset-select" name="preset"></select>
      <button class="primary" type="submit">套用场景</button>
    </form>
    <p class="hint" id="preset-detail"></p>
  </section>

  <section class="bar">
    <button class="primary" id="btn-check">立即体检</button>
    <button id="btn-sweep">跑一次守护</button>
    <button id="btn-report">打开完整报告</button>
    <label><input type="checkbox" id="auto" checked> 每 5 秒自动刷新</label>
    <button class="danger" id="btn-stop">停止程序</button>
  </section>
  <p class="msg" id="msg"></p>

  <section>
    <h2>我的服务 <span id="count" style="color:#6b7280;font-weight:400"></span></h2>
    <div class="grid" id="cards"></div>
  </section>

  <section class="panel">
    <h2>添加 / 修改服务</h2>
    <form id="add">
      <input name="name" placeholder="服务名称，例如 网关" maxlength="64" required>
      <input name="port" type="number" min="1" max="65535" placeholder="端口，例如 8080" required>
      <button class="primary" type="submit">保存</button>
    </form>
    <p class="hint">端口就是访问服务时网址里冒号后面的数字（例如 <code>http://127.0.0.1:8080</code> 里的 8080）。
    同名服务会更新端口；第一次保存时会自动替换掉占位的示例服务。配置文件：<code id="cfgpath">-</code></p>
  </section>

  <section class="panel">
    <h2>最近发生了什么</h2>
    <table><thead><tr><th>时间</th><th>类型</th><th>服务</th><th>说明</th></tr></thead>
      <tbody id="timeline"></tbody></table>
    <p class="empty" id="tl-empty">还没有记录。点一下「跑一次守护」就会记一条。</p>
  </section>

  <details>
    <summary>这是什么 / 我该干什么</summary>
    <p>1. 先把你自己的服务跑起来，然后在上面填上它的名字和端口，点「保存」。</p>
    <p>2. 点「立即体检」看它活没活。显示红色表示没找到进程或端口不通，先去检查服务本身。</p>
    <p>3. 点「跑一次守护」会做一次检查：真挂了就按配置拉起来，断网时不会乱拉，出事发通知。</p>
    <p>4. 「最近发生了什么」能查到每次检查和每条告警，用来回答「昨晚四点为什么挂」。</p>
    <p>想让它一直盯着：在命令行执行 <code>fleetkeeper watch --interval 60</code>；也可以把这个命令放进系统的定时任务。</p>
  </details>
</main>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function say(t){document.getElementById('msg').textContent=t;}
function tone(level){
 var s=String(level||'');
 if(s.indexOf('L4')===0||s.indexOf('L3')===0)return 'ok';
 if(s.indexOf('L2')===0)return 'warn';
 return 'bad';
}
function stamp(ts){
 if(!ts)return '-';
 var d=new Date(ts*1000),p=function(n){return n<10?'0'+n:''+n;};
 return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+
   p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())+
   '.'+String(d.getMilliseconds()).padStart(3,'0');
}
function api(path,opts){
 return fetch(path,opts||{}).then(function(r){
   if(!r.ok)throw new Error('HTTP '+r.status);
   return r.json();
 });
}
var presetData=[],isPlaceholder=true;
function renderPresets(st){
 isPlaceholder=!!st.is_placeholder;
 document.getElementById('intro-text').textContent=isPlaceholder?
  '现在列表里的是示例占位服务，显示红色是正常现象，不是程序坏了。选一个接近你电脑情况的场景，或者直接在下面填你自己的服务名和端口。':
  '想换一套起始服务，可以在这里套用。套用会覆盖当前的服务列表。';
 presetData=st.presets||[];
 var sel=document.getElementById('preset-select'),h='';
 for(var i=0;i<presetData.length;i++){
  h+='<option value="'+esc(presetData[i].name)+'">'+esc(presetData[i].label)+'</option>';
 }
 sel.innerHTML=h;
 showPresetDetail();
}
function showPresetDetail(){
 var sel=document.getElementById('preset-select'),d=null;
 for(var i=0;i<presetData.length;i++){if(presetData[i].name===sel.value)d=presetData[i];}
 if(!d)return;
 var parts=[];
 for(var j=0;j<d.services.length;j++){
  parts.push(d.services[j].name+' :'+d.services[j].port);
 }
 document.getElementById('preset-detail').textContent='会写入 '+parts.join('、')+'。端口按你自己的实际情况改。';
}
function render(st){
 renderPresets(st);
 document.getElementById('conn').textContent='已连接 · '+esc(st.generated_at||'');
 document.getElementById('cfgpath').textContent=esc(st.config_path||'-');
 var svcs=st.services||[];
 document.getElementById('count').textContent='('+svcs.length+')';
 var html='';
 for(var i=0;i<svcs.length;i++){
   var s=svcs[i],t=tone(s.level);
   var label=t==='ok'?'正常':(t==='warn'?'部分正常':'未通过');
   html+='<div class="card '+t+'">'+
     '<div><span class="name">'+esc(s.name)+'</span><span class="port">:'+esc(s.port)+'</span></div>'+
     '<div class="level"><b style="color:var(--'+(t==='ok'?'ok':t==='warn'?'warn':'bad')+')">'+label+'</b> · '+esc(s.level)+'</div>'+
     '<div class="detail">心跳：'+esc(s.heartbeat)+' · 进程：'+esc((s.pids||[]).join(',')||'无')+'</div>';
   for(var j=0;j<(s.issues||[]).length&&j<3;j++){
     html+='<div class="issue">'+esc(s.issues[j])+'</div>';
   }
   html+='</div>';
 }
 if(!svcs.length)html='<div class="empty">还没有配置服务。在下面填一个名字和端口就行。</div>';
 document.getElementById('cards').innerHTML=html;

 var rows=st.history||[],tl='';
 for(var k=0;k<rows.length&&k<10;k++){
   var r=rows[k];
   tl+='<tr><td>'+stamp(r.ts)+'</td><td>'+esc(r.kind)+'</td><td>'+
       esc(r.service||'fleet')+(r.port?':'+esc(r.port):'')+'</td>'+
       '<td class="msg-cell">'+esc(r.message||'')+'</td></tr>';
 }
 document.getElementById('timeline').innerHTML=tl;
 document.getElementById('tl-empty').style.display=rows.length?'none':'block';
}
function refresh(){
 return api('/api/state').then(render).catch(function(e){
   document.getElementById('conn').textContent='连接中断';say('读取失败：'+e.message);
 });
}
function post(path,body){
 return api(path,{method:'POST',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify(body||{})});
}
document.getElementById('btn-check').onclick=function(){
 say('正在体检…');refresh().then(function(){say('体检完成。');});
};
document.getElementById('btn-sweep').onclick=function(){
 say('正在跑一次守护…');
 post('/api/sweep').then(function(r){
   var parts=[];
   parts.push('问题 '+((r.issues||[]).length)+' 条');
   parts.push('已恢复 '+((r.healed||[]).length)+' 个');
   parts.push('告警 '+((r.alerts||[]).length)+' 条');
   if(r.network_down)parts.push('网络断开：本次不自愈');
   say('守护完成：'+parts.join(' · '));
   return refresh();
 }).catch(function(e){say('守护失败：'+e.message);});
};
document.getElementById('btn-report').onclick=function(){window.open('/report','_blank');};
document.getElementById('btn-stop').onclick=function(){
 if(!confirm('停止后这个页面就不能用了，确定吗？'))return;
 say('正在停止…');
 post('/api/stop').then(function(){
   say('已停止，可以关掉程序窗口了。');
   document.getElementById('conn').textContent='已停止';
 }).catch(function(){
   say('已停止，可以关掉程序窗口了。');
   document.getElementById('conn').textContent='已停止';
 });
};
document.getElementById('preset-select').onchange=showPresetDetail;
document.getElementById('preset-form').onsubmit=function(ev){
 ev.preventDefault();
 var name=document.getElementById('preset-select').value;
 if(!isPlaceholder&&!confirm('套用场景会覆盖当前的服务列表，确定吗？'))return;
 say('正在套用场景…');
 post('/api/preset',{name:name,force:true}).then(function(r){
   say(r.message||'已套用场景');return refresh();
 }).catch(function(e){say('套用失败：'+e.message);});
};
document.getElementById('add').onsubmit=function(ev){
 ev.preventDefault();
 var f=ev.target,name=f.name.value.trim(),port=parseInt(f.port.value,10);
 if(!name||!port){say('名字和端口都要填。');return;}
 post('/api/service',{name:name,port:port}).then(function(r){
   say(r.message||'已保存');f.name.value='';f.port.value='';return refresh();
 }).catch(function(e){say('保存失败：'+e.message);});
};
var timer=setInterval(function(){
 if(document.getElementById('auto').checked)refresh();
},5000);
refresh();
</script>
</body>
</html>
"""


def ensure_config(path):
    """Create a runnable config on first run. Returns True if it was created.

    A wheel install ships no config/ directory. Without this, a new user sees
    a file-not-found error before ever reaching a working screen.
    """
    path = os.path.abspath(path)
    if os.path.exists(path):
        return False
    safeio.atomic_write_text(path, template.render(template.default_config()))
    return True


def default_config_path():
    """A frozen exe must write next to itself, not into the launcher's CWD.

    Double-clicking an exe from Explorer usually gives CWD = C:\\Windows or
    the desktop. Writing there produces a config the user can never find.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                            "fleet.json")
    return os.path.abspath("config/fleet.json")


def _load(path):
    with open(os.path.abspath(path), "r", encoding="utf-8") as f:
        return json.load(f)


def _services_summary(path):
    """Reuse the CLI's ladder logic so the page and the terminal never disagree."""
    cfg = _load(path)
    rows = []
    for key, svc in cli_mod._registry(cfg).items():
        ev = evidence.evaluate_service(svc)
        hb = heartbeat.evaluate(key, int(svc.get("heartbeat_period") or 60),
                                directory=cli_mod._hb_dir(cfg, path))
        rows.append({
            "key": key,
            "name": svc["name"],
            "port": svc.get("port"),
            "level": ev["level_name"],
            "pids": ev.get("pids", []),
            "heartbeat": hb["status"],
            "hb_age": hb.get("age_seconds"),
            "evidence": ev["evidence"],
            "issues": [redact.redact(str(i)) for i in ev["issues"]],
        })
    return cfg, rows


def _history_rows(cfg, path, limit=12):
    db = history.db_path_for(os.path.dirname(os.path.abspath(path)),
                             cfg.get("watchdog") or {})
    return history.query(db, since=history.parse_since("7d"), limit=limit)


def _is_placeholder(cfg):
    """True while the config is still the untouched first-run template."""
    svcs = (cfg.get("registry") or {}).get("services") or {}
    return (list(svcs.keys()) == [PLACEHOLDER_KEY]
            and (svcs.get(PLACEHOLDER_KEY) or {}).get("name") == PLACEHOLDER_NAME)


def apply_preset(path, name, force=False):
    """Replace the service list with a named scenario.

    Refusing to overwrite by default is the important part: a user who already
    saved their own ports must not lose them to one click. The console sends
    force=True only after a browser-side confirm().
    """
    cfg = _load(path)
    if not force and not _is_placeholder(cfg):
        raise ValueError("配置里已经有服务了，套用场景会覆盖当前列表")
    # A JSON object/array here would blow up on the dict lookup with TypeError;
    # normalise it so every bad input becomes the same clean 400.
    name = str(name or "")
    try:
        new_cfg = template.preset_config(name)
    except ValueError:
        raise ValueError("没有这个场景")
    safeio.atomic_write_text(os.path.abspath(path), template.render(new_cfg))
    count = len(new_cfg["registry"]["services"])
    return "已套用场景 {}，共 {} 个服务。端口按你自己的实际情况改。".format(name, count)


def state(path):
    """One JSON payload for the whole page. No secrets are included."""
    cfg, rows = _services_summary(path)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_path": os.path.abspath(path),
        "services": rows,
        "history": _history_rows(cfg, path),
        "service_count": len(rows),
        "is_placeholder": _is_placeholder(cfg),
        "presets": template.preset_list(),
    }


def doctor_report(path):
    """Same P0 / uncovered classification as `fleetkeeper doctor`."""
    cfg, rows = _services_summary(path)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "services": [], "p0": [], "uncovered": []}
    for r in rows:
        item = {"service": r["name"], "port": r["port"], "level": r["level"],
                "issues": r["issues"],
                "heartbeat": {"status": r["heartbeat"], "detail": ""},
                "recovery": None}
        report["services"].append(item)
        if "L0" in r["level"] or "L1" in r["level"] or r["heartbeat"] in ("STALE", "FAILING"):
            report["p0"].append(item)
        elif r["heartbeat"] in ("NO_HEARTBEAT", "ALIVE_ONLY", "PROTOCOL_ONLY"):
            report["uncovered"].append(item)
    return report


def save_service(path, name, port):
    """Add or update one service. Returns a short Chinese status message.

    Only name+port are accepted from the browser: no arbitrary command, path
    or webhook can be injected through the UI. Writes are atomic so a sweep
    reading the file never sees a half-written JSON document.
    """
    name = str(name or "").strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("端口必须是 1 到 65535 之间的数字")
    if not name or len(name) > 64:
        raise ValueError("服务名称要填 1 到 64 个字符")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须是 1 到 65535 之间的数字")

    cfg = _load(path)
    reg = cfg.setdefault("registry", {})
    svcs = reg.setdefault("services", {})

    # The generated template contains one placeholder. Replacing it on the
    # first save keeps the page from showing a permanent red "Example Service"
    # card that the user never created and cannot get rid of.
    only_placeholder = (list(svcs.keys()) == [PLACEHOLDER_KEY]
                        and svcs[PLACEHOLDER_KEY].get("name") == PLACEHOLDER_NAME)

    target_key = None
    for key, svc in svcs.items():
        if svc.get("name") == name or key == name or svc.get("port") == port:
            target_key = key
            break
    if target_key is None:
        if only_placeholder:
            target_key = PLACEHOLDER_KEY
            svcs[target_key] = {}
        else:
            target_key = "svc{}".format(port)

    item = svcs.get(target_key) or {}
    item.update({
        "name": name,
        "port": port,
        "health": item.get("health") or {"path": "/", "method": "GET", "timeout": 3},
        "canary": item.get("canary"),
        "recovery": item.get("recovery"),
    })
    svcs[target_key] = item
    safeio.atomic_write_text(os.path.abspath(path),
                             json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return "已保存：{} :{}".format(name, port)


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "fleetkeeper-console"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Serving every asset to stderr makes the console window unreadable.
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _html(self, text):
        self._send(200, text, "text/html; charset=utf-8")

    def _error(self, exc, code=500):
        # Never echo a stack trace or an absolute path from an exception.
        self._json({"error": redact.redact(str(exc))[:200]}, code)

    @property
    def cfg_path(self):
        return self.server.fk_config

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if route == "/":
                return self._html(PAGE)
            if route == "/report":
                return self._html(report_mod.render(doctor_report(self.cfg_path)))
            if route == "/api/state":
                return self._json(state(self.cfg_path))
            if route == "/api/history":
                q = parse_qs(parsed.query)
                cfg = _load(self.cfg_path)
                db = history.db_path_for(
                    os.path.dirname(os.path.abspath(self.cfg_path)),
                    cfg.get("watchdog") or {})
                try:
                    since = history.parse_since((q.get("last") or ["24h"])[0])
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                rows = history.query(db, since=since,
                                     service=(q.get("service") or [None])[0],
                                     kind=(q.get("kind") or [None])[0],
                                     limit=min(200, int((q.get("limit") or ["50"])[0])))
                return self._json({"rows": rows, "count": history.count(db)})
            if route == "/api/config":
                cfg = _load(self.cfg_path)
                return self._json({"config_path": os.path.abspath(self.cfg_path),
                                   "services": [
                                       {"key": k, "name": v.get("name"),
                                        "port": v.get("port")}
                                       for k, v in cli_mod._registry(cfg).items()]})
            if route == "/api/presets":
                return self._json({"presets": template.preset_list()})
            return self._json({"error": "not found"}, 404)
        except Exception as exc:
            return self._error(exc)

    def do_POST(self):
        route = urlparse(self.path).path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            return self._json({"error": "请求体过大"}, 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "请求体不是合法 JSON"}, 400)
        try:
            if route == "/api/sweep":
                return self._json(watchdog.run(self.cfg_path))
            if route == "/api/service":
                msg = save_service(self.cfg_path, body.get("name"), body.get("port"))
                return self._json({"ok": True, "message": msg})
            if route == "/api/preset":
                # Only a whitelisted preset name crosses this boundary; no raw
                # config, path or command is accepted from the browser.
                msg = apply_preset(self.cfg_path, body.get("name"),
                                   force=bool(body.get("force")))
                return self._json({"ok": True, "message": msg})
            if route == "/api/stop":
                # shutdown() must not be called from the serving thread itself.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json({"ok": True, "message": "stopping"})
            return self._json({"error": "not found"}, 404)
        except (ValueError, KeyError) as exc:
            return self._json({"error": redact.redact(str(exc))[:200]}, 400)
        except Exception as exc:
            return self._error(exc)


def make_server(config_path, port=0):
    """Bind loopback only. port=0 lets the OS pick a free port."""
    path = os.path.abspath(config_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), ConsoleHandler)
    server.daemon_threads = True
    server.fk_config = path
    return server


def serve(config_path, port=0, open_browser=True):
    """Blocking entry point. Returns 0 on a clean stop."""
    path = os.path.abspath(config_path)
    created = ensure_config(path)
    server = make_server(path, port)
    url = "http://127.0.0.1:{}/".format(server.server_port)
    # flush=True: a frozen one-file exe writes to a pipe/console with block
    # buffering; without it the URL only appears after the process exits.
    if created:
        print("第一次使用，已自动生成配置文件：{}".format(path), flush=True)
    print("服务守护面板：{}".format(url), flush=True)
    print("页面已在浏览器中打开。按 Ctrl+C 或关掉本窗口即可停止。", flush=True)
    # Double-clicking opens the browser. FLEETKEEPER_NO_BROWSER is for
    # headless hosts and automated smoke runs; it does not change any other
    # behavior, and CLI callers can also use `start --no-browser`.
    if open_browser and not os.environ.get("FLEETKEEPER_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:
            print("浏览器没能自动打开，请手动复制上面的网址到浏览器。", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0
