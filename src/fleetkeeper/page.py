# -*- coding: utf-8 -*-
"""The whole browser console as one self-contained HTML string.

Kept in its own module so `ui.py` stays an HTTP router. No CDN, no external
fonts, no script src, no link tag: the frozen exe has no assets to ship and
the page must work with the machine completely offline. Every control maps to
one loopback endpoint documented in docs/说明书.md.
"""

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服务守护面板 · FleetKeeper</title>
<style>
:root{
 --bg:#f3f5fa;--card:#fff;--ink:#182230;--sub:#6b7688;--line:#e3e8f0;
 --ok:#0f8a55;--ok-bg:#e8f7ef;--warn:#b27a06;--warn-bg:#fff5dd;
 --bad:#c8322c;--bad-bg:#fdeceb;--brand:#3457d5;--brand-bg:#eef2ff;
 --navy:#1b2744;--shadow:0 1px 2px rgba(24,34,48,.04),0 8px 24px rgba(24,34,48,.05)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:linear-gradient(115deg,#1b2744 0%,#2a3d6b 62%,#3457d5 100%);
 color:#fff;padding:16px 26px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;
 box-shadow:0 2px 14px rgba(20,32,60,.18)}
.logo{width:34px;height:34px;border-radius:10px;background:rgba(255,255,255,.16);
 border:1px solid rgba(255,255,255,.28);display:flex;align-items:center;justify-content:center;
 font-weight:700;font-size:15px;letter-spacing:.5px;flex:none}
.brand{display:flex;flex-direction:column;line-height:1.25}
.brand b{font-size:17px;letter-spacing:.3px}
.brand span{font-size:11.5px;opacity:.78}
.head-right{margin-left:auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
#conn{font-size:12px;color:#0d6b42;background:#dcf6e8;padding:5px 12px;
 border-radius:20px;font-weight:600;white-space:nowrap}
#conn.off{color:#a5211c;background:#fde3e1}
#conn.stopped{color:#4b5563;background:#e8ebf0}
.uptime{font-size:11.5px;opacity:.8;white-space:nowrap}
header button{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28);
 color:#fff;border-radius:8px;font-size:12px;padding:6px 12px;cursor:pointer}
header button:hover{background:rgba(255,255,255,.26)}
button{font:inherit;font-size:13px;padding:8px 14px;border-radius:8px;
 border:1px solid var(--line);background:#fff;color:var(--ink);cursor:pointer;
 transition:border-color .15s,background .15s,transform .1s}
button:hover{border-color:var(--brand);color:var(--brand)}
button:active{transform:translateY(1px)}
button:disabled{opacity:.45;cursor:default;transform:none}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:600}
button.primary:hover{background:#2948bd;color:#fff}
button.ghost{background:transparent}
button.danger{color:var(--bad)}
button.mini{font-size:11.5px;padding:4px 9px;border-radius:6px}
label{font-size:12.5px;color:var(--sub);display:inline-flex;align-items:center;gap:6px}
main{max-width:1420px;margin:0 auto;padding:20px 22px 56px}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:13px 15px;box-shadow:var(--shadow);position:relative;overflow:hidden;min-height:86px}
.kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--brand)}
.kpi.ok:before{background:var(--ok)}.kpi.warn:before{background:var(--warn)}
.kpi.bad:before{background:var(--bad)}.kpi.info:before{background:#7c5cd6}
.kpi .k-label{font-size:11.5px;color:var(--sub);display:block;margin-bottom:4px}
.kpi .k-num{font-size:27px;font-weight:700;line-height:1.15;letter-spacing:-.5px}
.kpi .k-foot{font-size:11px;color:var(--sub);display:block;margin-top:3px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.toolbar{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:11px 14px;display:flex;flex-wrap:wrap;gap:9px;align-items:center;
 box-shadow:var(--shadow);margin-bottom:12px}
.toolbar .sep{width:1px;height:22px;background:var(--line);margin:0 2px}
.toolbar .grow{flex:1}
.watch-box{display:flex;align-items:center;gap:6px;background:var(--brand-bg);
 border:1px solid #d9e2ff;border-radius:9px;padding:4px 8px}
.watch-box input{width:62px;padding:5px 7px;font-size:12.5px}
#watch-state{font-size:11.5px;color:var(--brand);white-space:nowrap;max-width:230px;
 overflow:hidden;text-overflow:ellipsis}
.msg{min-height:20px;margin:0 2px 14px;font-size:12.5px;color:var(--brand)}
.msg.err{color:var(--bad)}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;align-items:start}
.stack{display:flex;flex-direction:column;gap:16px;min-width:0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px;box-shadow:var(--shadow)}
.panel summary{font-size:14.5px;font-weight:650;cursor:pointer;user-select:none}
.panel summary:hover{color:var(--brand)}
.panel details{margin:0}
.panel h2{font-size:14.5px;margin:0 0 12px;font-weight:650;display:flex;
 align-items:center;gap:8px;flex-wrap:wrap}
.panel h2 .count{font-weight:400;color:var(--sub);font-size:12.5px}
.panel h2 .right{margin-left:auto;font-weight:400;font-size:12px}
.hint{color:var(--sub);font-size:11.5px;margin:8px 0 0;word-break:break-all}
.section-sub{color:var(--sub);font-size:12px;margin:-6px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;
 position:relative;transition:box-shadow .15s,border-color .15s;
 display:flex;flex-direction:column}
.card:hover{box-shadow:0 6px 20px rgba(24,34,48,.09);border-color:#cfd8ea}
.card.ok{border-top:3px solid var(--ok)}
.card.warn{border-top:3px solid var(--warn)}
.card.bad{border-top:3px solid var(--bad)}
.card.paused{opacity:.62;border-top-color:#9aa4b5}
.card-top{display:flex;align-items:flex-start;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--bad);margin-top:7px;flex:none;
 box-shadow:0 0 0 3px var(--bad-bg)}
.card.ok .dot{background:var(--ok);box-shadow:0 0 0 3px var(--ok-bg)}
.card.warn .dot{background:var(--warn);box-shadow:0 0 0 3px var(--warn-bg)}
.card .name{font-weight:650;font-size:14px;word-break:break-all;line-height:1.35}
.card .port{color:var(--sub);font-size:12px;font-weight:400}
.chip{font-size:10.5px;padding:2px 8px;border-radius:20px;white-space:nowrap;
 background:var(--bad-bg);color:var(--bad);font-weight:600}
.chip.ok{background:var(--ok-bg);color:var(--ok)}
.chip.warn{background:var(--warn-bg);color:var(--warn)}
.chip.info{background:var(--brand-bg);color:var(--brand)}
.chip.gray{background:#f0f2f6;color:var(--sub)}
.ladder{display:flex;gap:4px;margin:12px 0 10px}
.ladder span{flex:1;text-align:center;font-size:9.5px;padding:4px 0;border-radius:5px;
 background:#f0f2f6;color:#9aa4b5;letter-spacing:.2px}
.ladder span.on{background:var(--ok-bg);color:var(--ok);font-weight:650}
.ladder span.part{background:var(--warn-bg);color:var(--warn);font-weight:650}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px;font-size:11.5px;overflow-wrap:anywhere;
 color:var(--sub);border-top:1px dashed var(--line);padding-top:9px}
.meta b{color:var(--ink);font-weight:600}
.issues{margin-top:9px;background:var(--bad-bg);border-radius:7px;padding:7px 9px;
 font-size:11.5px;color:var(--bad);word-break:break-word;line-height:1.5}
.note{margin-top:6px;background:#f4f6fa;border-radius:7px;padding:5px 9px;
 font-size:11px;color:#5a6478;word-break:break-word;line-height:1.5}
.evid{margin-top:8px;font-size:11px;color:var(--sub);word-break:break-word;line-height:1.45;
 background:#f7f8fb;border-left:2px solid #d8dfec;border-radius:0 6px 6px 0;padding:5px 8px;
 display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-actions{display:flex;flex-wrap:wrap;gap:4px;margin-top:auto;
 border-top:1px solid var(--line);padding-top:9px}
.card-actions button{font-size:11px;padding:4px 7px}
.skeleton{background:linear-gradient(90deg,#f0f2f6 25%,#e6eaf2 50%,#f0f2f6 75%);
 background-size:200% 100%;animation:shimmer 1.3s infinite;border-radius:8px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.sk-card{height:176px;border:1px solid var(--line);border-radius:12px;padding:14px}
.sk-line{height:11px;margin-bottom:9px}
.empty{color:var(--sub);font-size:12.5px;padding:16px 4px;text-align:center;
 border:1px dashed var(--line);border-radius:9px;background:#fafbfe}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--sub);font-weight:600;font-size:11px;background:#fafbfe;position:sticky;top:0}
tbody tr:nth-child(even){background:#fcfdff}
td.msg-cell{word-break:break-all;max-width:420px}
.scroll{max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:9px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center}
select,input{font:inherit;font-size:12.5px;padding:7px 10px;border:1px solid var(--line);
 border-radius:8px;background:#fff;color:var(--ink)}
select{max-width:100%}
input[type=number]{width:88px}
form{display:flex;flex-wrap:wrap;gap:8px}
form input[name=name]{flex:2;min-width:120px}
form input[name=port]{flex:1;min-width:96px}
code{background:#f0f2f6;padding:2px 5px;border-radius:4px;font-size:11px}
.disc-row{display:flex;align-items:flex-start;gap:9px;padding:8px 9px;
 border:1px solid var(--line);border-radius:9px;margin-bottom:7px;background:#fcfdff}
.disc-row:hover{border-color:#cfd8ea}
.disc-row.is-in{background:#f5f6fa;opacity:.75}
.disc-row .d-main{flex:1;min-width:0}
.disc-row .d-name{font-weight:650;font-size:12.5px;word-break:break-all}
.disc-row .d-ev{color:var(--sub);font-size:11px;line-height:1.45;margin-top:2px;
 word-break:break-word;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
 overflow:hidden}
.disc-list{max-height:352px;overflow:auto}
.disc-more{cursor:pointer;text-align:center;font-size:11.5px;color:var(--brand);
 padding:7px;border:1px dashed var(--line);border-radius:8px;margin-top:8px;background:#fafbfe}
.disc-more:hover{border-color:#cfd8ea;background:#f2f6ff}
.alert-item{display:flex;gap:9px;padding:8px 10px;border-left:3px solid var(--warn);
 background:var(--warn-bg);border-radius:0 7px 7px 0;margin-bottom:7px;font-size:12px}
.alert-item .a-time{color:var(--sub);font-size:11px;white-space:nowrap}
.alert-item .a-msg{word-break:break-all}
details{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 15px;
 box-shadow:var(--shadow)}
summary{cursor:pointer;font-weight:650;font-size:13px}
details p{color:var(--sub);font-size:12px;margin:9px 0 0}
pre{background:#101a2e;color:#dbe6ff;border-radius:9px;padding:12px;font-size:11px;
 line-height:1.5;overflow:auto;max-height:280px;white-space:pre-wrap;word-break:break-all}
.log-out{background:#101a2e;color:#dbe6ff;border-radius:9px;padding:10px 12px;
 font-size:11px;line-height:1.55;max-height:230px;overflow:auto;
 white-space:pre-wrap;word-break:break-all;font-family:Consolas,Monaco,monospace}
.editor{position:fixed;z-index:30;right:20px;bottom:20px;width:360px;max-width:calc(100vw - 40px);
 background:#fff;border:1px solid #cfd8ea;border-radius:14px;padding:16px;
 box-shadow:0 18px 50px rgba(20,32,60,.24);display:none}
.editor h3{margin:0 0 4px;font-size:14px}
.editor .row{display:flex;gap:8px;align-items:center;margin-top:8px}
.editor .row label{width:88px;flex:none}
.editor .row input{flex:1;min-width:0}
.editor .foot{display:flex;gap:8px;margin-top:14px}
.foot-note{font-size:11.5px;color:var(--sub);margin-top:10px;line-height:1.5}
@media(max-width:1240px){.cols{grid-template-columns:minmax(0,1fr) 340px}}
@media(max-width:1120px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:900px){.cols{grid-template-columns:1fr}header{padding:14px 16px}
 main{padding:16px 12px 40px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<header>
  <div class="logo">FK</div>
  <div class="brand"><b>服务守护面板 · FleetKeeper</b>
    <span>本机运行 · 数据不出这台电脑 · 关掉窗口即停止</span></div>
  <div class="head-right">
    <span class="uptime" id="uptime">运行时长 -</span>
    <button id="btn-reconnect" style="display:none">重新连接</button>
    <span id="conn">已连接</span>
  </div>
</header>
<main>
  <div class="kpis" id="kpis">
    <div class="kpi"><span class="k-label">服务总数</span><span class="k-num" id="kpi-total">–</span><span class="k-foot" id="kpi-total-foot">正在读取…</span></div>
    <div class="kpi ok"><span class="k-label">协议以上正常</span><span class="k-num" id="kpi-healthy">–</span><span class="k-foot">端口与 HTTP 都通</span></div>
    <div class="kpi warn"><span class="k-label">需要关注</span><span class="k-num" id="kpi-attention">–</span><span class="k-foot">只有端口通</span></div>
    <div class="kpi bad"><span class="k-label">未通过</span><span class="k-num" id="kpi-down">–</span><span class="k-foot">进程或端口不通</span></div>
    <div class="kpi info"><span class="k-label">业务证据达成</span><span class="k-num" id="kpi-business">–</span><span class="k-foot">业务探针真的过了</span></div>
    <div class="kpi"><span class="k-label">累计事件</span><span class="k-num" id="kpi-events">–</span><span class="k-foot" id="kpi-events-foot">守护 / 恢复 / 告警</span></div>
  </div>

  <div class="toolbar">
    <button class="primary" id="btn-check">立即体检</button>
    <button id="btn-sweep">跑一次守护</button>
    <div class="watch-box">
      <button class="mini" id="btn-watch">开启常驻守护</button>
      <label>每 <input type="number" id="watch-interval" min="5" max="3600" value="60"> 秒</label>
    </div>
    <span id="watch-state">常驻守护：未开启</span>
    <div class="sep"></div>
    <button id="btn-report">完整报告</button>
    <button id="btn-notify-set">通知设置</button>
    <button id="btn-notify-test">测试通知</button>
    <button id="btn-autostart">开机自启</button>
    <button id="btn-export">导出配置</button>
    <div class="grow"></div>
    <label><input type="checkbox" id="auto" checked> 每 5 秒自动刷新</label>
    <button class="danger" id="btn-stop">停止程序</button>
  </div>
  <p class="msg" id="msg"></p>

  <div class="cols">
    <div class="stack">
      <section class="panel">
        <h2>我的服务 <span class="count" id="count"></span>
          <span class="right" id="health-hint">L0 配置 · L1 进程 · L2 端口 · L3 协议 · L4 业务</span></h2>
        <div class="grid" id="cards"></div>
      </section>

      <section class="panel">
        <h2>最近发生了什么 <span class="count" id="tl-count"></span></h2>
        <div class="filters">
          <select id="tl-window">
            <option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option>
            <option value="30d">最近 30 天</option><option value="all">全部</option>
          </select>
          <select id="tl-kind">
            <option value="">所有类型</option><option value="sweep">守护检查</option>
            <option value="alert">告警</option><option value="heal">恢复</option>
            <option value="heartbeat">心跳</option>
          </select>
          <select id="tl-service"><option value="">所有服务</option></select>
          <select id="tl-limit"><option value="20">20 条</option>
            <option value="50">50 条</option><option value="100">100 条</option></select>
          <button class="mini" id="btn-tl-reload">刷新事件</button>
        </div>
        <div class="scroll"><table>
          <thead><tr><th>时间</th><th>类型</th><th>服务</th><th>说明</th></tr></thead>
          <tbody id="timeline"></tbody></table></div>
        <p class="empty" id="tl-empty" style="display:none">还没有记录。点一下「跑一次守护」就会记一条。</p>
      </section>

      <section class="panel">
        <h2>告警记录 <span class="count" id="alert-count"></span></h2>
        <div id="alert-list"></div>
      </section>

      <details>
        <summary>这是什么 / 我该干什么</summary>
        <p>1. 页面打开时会自动扫描本机端口，AI 服务（Ollama、OpenAI 兼容接口、ComfyUI…）会直接写进配置，MySQL、Redis、Nginx 这类非 HTTP 服务也认得出来；没认出来的可以勾上手动加入，或者自己填名字和端口。</p>
        <p>2. 每张卡片上的五级阶梯是「配置 → 进程 → 端口 → 协议 → 业务」，越靠右越能证明服务真的在干活。红色表示没找到进程或端口不通，先去检查服务本身。</p>
        <p>3. 「跑一次守护」做一次检查：真挂了就按配置拉起来，断网时不会乱拉，出事发通知。「开启常驻守护」让面板自己按间隔重复检查，不用另开命令行。</p>
        <p>4. 卡片上的「编辑」可以改健康检查路径、业务探针路径、心跳周期和日志文件；「写入心跳」给无法自己上报的服务补一条证据；「暂停」让守护跳过它但不删配置。</p>
        <p>5. 「最近发生了什么」能按时间、类型、服务筛选，用来回答「昨晚四点为什么挂」。</p>
      </details>
    </div>

    <div class="stack">
      <section class="panel" id="discover-panel">
        <h2>自动识别本机服务</h2>
        <p class="hint" id="discover-text">正在找你电脑上已经跑着的服务，不用自己填名字和端口…</p>
        <div style="display:flex;gap:7px;margin:11px 0 9px;flex-wrap:wrap">
          <button class="mini" id="btn-discover">重新扫描</button>
          <button class="mini primary" id="btn-discover-add">把勾中的加进配置</button>
        </div>
        <div class="disc-list" id="discover-list"></div>
        <div id="disc-more-slot"></div>
        <p class="hint" id="discover-note">「确定」默认勾上；「待确认」要你自己勾，加错了可以在下面改端口。</p>
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

      <details class="panel" id="intro">
        <summary>备选：手动选一个场景（平时用不到）</summary>
        <p class="hint" id="intro-text"></p>
        <form id="preset-form">
          <select id="preset-select" name="preset"></select>
          <button class="primary" type="submit">套用场景</button>
        </form>
        <p class="hint" id="preset-detail"></p>
      </details>

      <details class="panel">
        <summary>配置全文（排障时才需要看）</summary>
        <div class="filters"><button class="mini" id="btn-config">读取配置</button>
          <button class="mini" id="btn-export-side">另存为文件</button></div>
        <pre id="config-text">点「读取配置」查看当前 fleet.json（敏感值已打码）。</pre>
      </details>

      <section class="panel">
        <h2>查看服务日志</h2>
        <div class="filters"><select id="log-service"><option value="">选一个服务</option></select>
          <input type="number" id="log-tail" min="1" max="300" value="40" title="显示最后多少行">
          <label><input type="checkbox" id="log-raw"> 原文</label></div>
        <div class="filters"><button class="mini" id="btn-log">读取日志</button></div>
        <div class="log-out" id="log-output">选一个服务，再点「读取日志」。</div>
        <p class="hint">默认会把密钥、令牌这类敏感串打码；勾「原文」才显示原始内容。</p>
      </section>
    </div>
  </div>

  <div class="editor" id="editor">
    <h3 id="edit-title">编辑服务</h3>
    <p class="hint" id="edit-sub" style="margin:0"></p>
    <div class="row"><label>名称</label><input id="edit-name" maxlength="64"></div>
    <div class="row"><label>端口</label><input id="edit-port" type="number" min="1" max="65535"></div>
    <div class="row"><label>健康检查路径</label><input id="edit-health" placeholder="/"></div>
    <div class="row"><label>业务探针路径</label><input id="edit-canary" placeholder="/v1/models"></div>
    <div class="row"><label>心跳周期(秒)</label><input id="edit-period" type="number" min="5" max="86400"></div>
    <div class="row"><label>日志文件</label><input id="edit-log" placeholder="svc.log"></div>
    <div class="row"><label><input type="checkbox" id="edit-restart"> 挂了自动重启</label></div>
    <div class="row" id="edit-restart-row" style="display:none">
      <label>重启命令</label><input id="edit-restart-cmd" placeholder="按它原来启动它的命令填">
    </div>
    <div class="foot"><button class="primary" id="btn-edit-save">保存修改</button>
      <button id="btn-edit-close">取消</button>
      <button class="mini" id="btn-edit-heartbeat" title="这台服务自己不会上报心跳时，替它补一条当前证据">写入一条心跳</button></div>
    <p class="foot-note">重启命令在服务连续挂掉后由常驻守护执行（自动识别的服务会按它原来的启动命令预填）。</p>
  </div>

  <div class="editor" id="notify-box" style="display:none">
    <h3>通知设置</h3>
    <p class="hint" style="margin:0">服务出问题时往这里推一条消息。填一个钉钉/飞书/Server酱等平台的 Webhook 地址。</p>
    <div class="row"><label>Webhook</label><input id="nf-url" placeholder="钉钉 / 飞书 / Server酱 的 Webhook 地址"></div>
    <div class="row"><label><input type="checkbox" id="nf-on" checked> 启用 Webhook 通道</label></div>
    <div class="foot"><button class="primary" id="btn-nf-save">保存</button>
      <button id="btn-nf-close">关闭</button>
      <button class="mini" id="btn-nf-test">先发一条测试</button></div>
  </div>
</main>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function say(t,err){
 var m=document.getElementById('msg');
 m.textContent=t;m.className=err?'msg err':'msg';
}
function explain(e){
 var m=String(e&&e.message||e||'');
 if(m==='SERVICE_DOWN')return '连不上本机服务了：程序窗口被关掉，或刚才点了「停止程序」。重新双击 fleetkeeper.exe，用新窗口里打印的网址打开本页。';
 if(m==='BAD_JSON')return '这个端口回的不是本程序的 JSON，可能被别的程序占用了。';
 if(m.indexOf('HTTP ')===0)return '服务还在，但这次没读到状态（'+m+'）。';
 return m;
}
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
   p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
function shortTime(ts){
 if(!ts)return '-';
 var d=new Date(ts*1000),p=function(n){return n<10?'0'+n:''+n;};
 return p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
function api(path,opts){
 // Only the network layer is normalised here: a stopped exe yields TypeError
 // "Failed to fetch". HTTP errors and bad JSON are different failures with
 // different fixes, so they keep their own labels.
 return fetch(path,opts||{}).catch(function(e){
   var err=new Error('SERVICE_DOWN');err.cause=e;throw err;
 }).then(function(r){
   if(!r.ok){
     return r.text().then(function(t){
       var detail='';
       try{detail=(JSON.parse(t)||{}).error||'';}catch(_){}
       throw new Error('HTTP '+r.status+(detail?'：'+detail:''));
     });
   }
   return r.json().catch(function(pe){
     var err=new Error('BAD_JSON');err.cause=pe;throw err;
   });
 });
}
function post(path,body){
 return api(path,{method:'POST',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify(body||{})});
}
function get(path){return api(path);}
var presetData=[],isPlaceholder=true,lastState=null;
var pollTimer=null,stopped=false,inFlight=false,pendingRefresh=false;
var failures=0,MAX_FAILURES=3;
var discoveryDone=false,discoveryBusy=false,discoveryData=[];
var editKey=null,editIsTcp=false,watchRunning=false,stateError=false;
var lastRow=null;   // the card row currently open in the editor (for restart toggle)
function el(id){return document.getElementById(id);}
function setConn(text,cls){
 var c=el('conn');c.textContent=text;c.className=cls||'';
}
function showReconnect(on){
 el('btn-reconnect').style.display=on?'':'none';
}
function startPoll(){
 if(pollTimer)return;
 pollTimer=setInterval(function(){
   if(el('auto').checked&&!stopped&&!inFlight)refresh();
 },5000);
}
function pausePoll(){
 if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
 showReconnect(true);
}
// /api/ping touches no config, no port probes and no network: it is the
// cheapest way to tell "the exe is gone" from "state() threw an exception".
function diagnose(){
 return api('/api/ping').then(function(){return 'ALIVE';},
                             function(){return 'DOWN';});
}
function stopUI(){
 stopped=true;
 if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
 showReconnect(false);
 setConn('已停止','stopped');
 say('已停止，可以关掉程序窗口了。以后要用，重新双击 fleetkeeper.exe，用新窗口里打印的网址打开。');
 var ids=['btn-check','btn-sweep','btn-report','btn-stop','btn-discover',
          'btn-discover-add','btn-watch','btn-notify-test','btn-export',
          'btn-tl-reload','btn-log','btn-config','btn-export-side',
          'btn-edit-save'];
 for(var i=0;i<ids.length;i++)el(ids[i]).disabled=true;
}
function renderSkeleton(){
 var h='';
 for(var i=0;i<3;i++){
  h+='<div class="sk-card"><div class="skeleton sk-line" style="width:55%"></div>'+
     '<div class="skeleton sk-line" style="width:35%"></div>'+
     '<div class="skeleton sk-line" style="width:100%;height:22px"></div>'+
     '<div class="skeleton sk-line" style="width:80%"></div>'+
     '<div class="skeleton sk-line" style="width:65%"></div></div>';
 }
 el('cards').innerHTML=h;
}
function renderPresets(st){
 isPlaceholder=!!st.is_placeholder;
 el('intro-text').textContent=isPlaceholder?
  '程序会自动识别本机服务并写进配置。占位示例服务显示红色是正常现象，不是程序坏了。没识别到时再选一个接近你电脑情况的场景，或者直接在下面填端口。':
  '想换一套起始服务，可以在这里套用。套用会覆盖当前的服务列表。';
 presetData=st.presets||[];
 var sel=el('preset-select'),h='';
 for(var i=0;i<presetData.length;i++){
  h+='<option value="'+esc(presetData[i].name)+'">'+esc(presetData[i].label)+'</option>';
 }
 sel.innerHTML=h;
 showPresetDetail();
}
function showPresetDetail(){
 var sel=el('preset-select'),d=null;
 for(var i=0;i<presetData.length;i++){if(presetData[i].name===sel.value)d=presetData[i];}
 if(!d)return;
 var parts=[];
 for(var j=0;j<d.services.length;j++){
  parts.push(d.services[j].name+' :'+d.services[j].port);
 }
 el('preset-detail').textContent='会写入 '+parts.join('、')+'。端口按你自己的实际情况改。';
}
function discRow(c,i){
 var tag=c.confidence==='high'?'确定':(c.confidence==='medium'?'可能':'待确认');
 var cls=c.confidence==='high'?'ok':(c.confidence==='medium'?'warn':'gray');
 if(c.configured){tag='已在配置';cls='gray';}
 var ev=String(c.evidence||'');
 return '<div class="disc-row'+(c.configured?' is-in':'')+'" title="'+esc(ev)+'">'+
   '<input type="checkbox" id="disc-chk-'+i+'"'+
   (c.recommended?' checked':'')+(c.configured?' disabled':'')+'><div class="d-main">'+
   '<div class="d-name">'+esc(c.name)+' <span class="chip '+cls+'">'+tag+'</span> '+
   '<span class="port">:'+esc(c.port)+'</span></div>'+
   '<div class="d-ev">'+esc(ev)+'</div></div></div>';
}
function renderDiscovery(res){
 discoveryData=(res&&res.candidates)||[];
 var box=el('discover-list');
 var fresh=[],done=[];
 for(var i=0;i<discoveryData.length;i++){
  (discoveryData[i].configured?done:fresh).push(i);
 }
 var txt='扫描了 '+((res&&res.scanned_ports||[]).length)+' 个本机端口，认出 '+
         discoveryData.length+' 个服务（'+(res&&res.elapsed_ms||0)+' 毫秒）。';
 if(fresh.length)txt+=' 其中 '+fresh.length+' 个还没进配置。';
 if(done.length)txt+=' 另外 '+done.length+' 个已经在配置里。';
 if(res&&res.auto_applied)txt+=' 已自动加入 '+res.auto_applied+' 个。';
 el('discover-text').textContent=txt;
 el('discover-note').textContent=fresh.length?
   '「确定」默认勾上；「待确认」要你自己勾，加错了可以在下面改端口。':
   '这台机器上认出来的服务都已经在配置里，不用重复添加。要认新的，先把服务启动起来再点「重新扫描」。';
 if(!discoveryData.length){
  box.innerHTML='<p class="empty">没有识别到正在运行的 HTTP 服务。先把服务启动起来再点「重新扫描」，或者在下面手动填一个端口。</p>';
  return;
 }
 var h='';
 for(var i=0;i<fresh.length;i++)h+=discRow(discoveryData[fresh[i]],fresh[i]);
 var inner='';
 for(var i=0;i<done.length;i++)inner+=discRow(discoveryData[done[i]],done[i]);
 // Collapsed by default so the rows that still need a decision stay on top,
 // but the toggle lives outside the scroll box so it is always reachable.
 h+='<div id="disc-done" style="display:none">'+inner+'</div>';
 box.innerHTML=h;
 var slot=el('disc-more-slot');
 if(slot)slot.innerHTML=done.length?
   '<div class="disc-more" id="disc-more">展开 '+done.length+' 个已在配置里的</div>':'';
 var more=document.getElementById('disc-more');
 if(more)more.onclick=function(){
  var d=document.getElementById('disc-done');
  var open=d.style.display!=='none';
  d.style.display=open?'none':'block';
  more.textContent=(open?'展开 ':'收起 ')+done.length+' 个已在配置里的';
 };
}
function selectedIds(){
 var ids=[];
 for(var i=0;i<discoveryData.length;i++){
  if(discoveryData[i].configured)continue;   // already saved: nothing to add
  var box=document.getElementById('disc-chk-'+i);
  if(box&&box.checked)ids.push(discoveryData[i].id);
 }
 return ids;
}
function runDiscovery(auto){
 if(discoveryBusy||stopped)return Promise.resolve(false);
 discoveryBusy=true;
 // auto = first open with a placeholder config: scan AND write the confident
 // rows in one round trip. Otherwise the GET is read-only.
 var call=auto?post('/api/discover',{auto:true}):api('/api/discover');
 return call.then(function(res){
  discoveryBusy=false;discoveryDone=true;renderDiscovery(res);
  if(auto&&res.auto_applied){
   // Discovery runs in parallel with state(). If state() already reported a
   // dead exe or a broken config, do not overwrite that better message.
   if(!stateError)say('已自动识别并加入 '+res.auto_applied+' 个服务，不用再手填端口。');
   return refresh().then(function(){return true;});
  }
  return true;
 }).catch(function(e){
  discoveryBusy=false;discoveryDone=true;
  el('discover-text').textContent='这次没扫成，服务列表不受影响。';
  if(!auto)say('识别失败：'+explain(e),true);
  return false;   // never bury a better state() error on the first load
 });
}
function renderKpi(ov){
 ov=ov||{};
 function n(id,v){el(id).textContent=(v==null?'0':v);}
 n('kpi-total',ov.total);n('kpi-healthy',ov.healthy);
 n('kpi-attention',ov.attention);n('kpi-down',ov.down);
 n('kpi-business',ov.business);n('kpi-events',ov.event_count);
 el('kpi-total-foot').textContent='暂停 '+(ov.paused||0)+' 个 · 心跳有效 '+(ov.heartbeat_ok||0)+' 个';
 var sw=ov.last_sweep;
 el('kpi-events-foot').textContent=sw?('上次守护 '+shortTime(sw.ts)):'守护 / 恢复 / 告警';
 el('uptime').textContent='运行时长 '+Math.max(0,Math.round(ov.uptime||0))+' 秒';
 var w=ov.watch||{};
 watchRunning=!!w.running;
 el('btn-watch').textContent=watchRunning?'停止常驻守护':'开启常驻守护';
 el('watch-state').textContent=watchRunning?
   ('常驻守护：运行中 · 每 '+(w.interval||60)+' 秒 · 已跑 '+(w.runs||0)+' 轮'+(w.last_error?' · 上轮出错':'')):
   '常驻守护：未开启';
}
function kindChip(kind){
 var k=String(kind||''),cls='gray';
 if(k==='alert')cls='bad';
 else if(k==='heal')cls='ok';
 else if(k==='sweep')cls='info';
 else if(k==='heartbeat')cls='warn';
 var label={sweep:'守护检查',alert:'告警',heal:'恢复',heartbeat:'心跳'}[k]||k||'事件';
 return '<span class="chip '+cls+'">'+esc(label)+'</span>';
}
function renderServices(st){
 var svcs=st.services||[];
 el('count').textContent='('+svcs.length+')';
 var html='';
 for(var i=0;i<svcs.length;i++){
   var s=svcs[i],t=tone(s.level),num=Number(s.level_number||0);
   var label=t==='ok'?'正常':(t==='warn'?'部分正常':'未通过');
   var chipCls=t==='ok'?'ok':(t==='warn'?'warn':'bad');
   var ladder='',names=['L0 配置','L1 进程','L2 端口','L3 协议','L4 业务'];
   for(var L=0;L<5;L++){
     ladder+='<span class="'+(L<=num?'on':'')+'">'+names[L]+'</span>';
   }
   var meta='';
   meta+='<div>进程 PID：<b>'+esc((s.pids||[]).join(',')||'无')+'</b></div>';
   // The ladder codes are stable API values; the card is read by people, so
   // show the meaning instead of the enum.
   var hbMap={UP:'正常',NO_HEARTBEAT:'没上报',ALIVE_ONLY:'只有进程存活',
              PROTOCOL_ONLY:'只有协议响应',STALE:'过期',FAILING:'失败'};
   meta+='<div>心跳：<b>'+esc(hbMap[s.heartbeat]||s.heartbeat||'-')+'</b></div>';
   meta+='<div>健康检查：<b>'+esc(s.health_path||'/')+'</b></div>';
   meta+='<div>业务探针：<b>'+esc(s.canary_path||'未配置')+'</b></div>';
   if(s.hb_age!=null)meta+='<div>心跳距今：<b>'+Math.round(s.hb_age)+' 秒</b></div>';
   if(s.has_recovery)meta+='<div>恢复动作：<b>已配置</b></div>';
   var issues='',notes='';
   for(var j=0;j<(s.issues||[]).length&&j<3;j++){
     issues+='<div class="issues">'+esc(s.issues[j])+'</div>';
   }
   // Advisory only: the service passed its business probe, so this must not
   // read like a failure. Grey, one line, below the red block.
   for(var j=0;j<(s.notes||[]).length&&j<2;j++){
     notes+='<div class="note">'+esc(s.notes[j])+'</div>';
   }
   var evid='';
   if((s.evidence||[]).length){var evall=s.evidence.join(' · ');
     evid='<div class="evid" title="'+esc(evall)+'">证据：'+esc(evall)+'</div>';}
   html+='<div class="card '+t+(s.paused?' paused':'')+'" data-key="'+esc(s.key)+'">'+
     '<div class="card-top"><span class="dot"></span><div style="flex:1;min-width:0">'+
     '<div class="name">'+esc(s.name)+' <span class="port">:'+esc(s.port)+'</span></div>'+
     '<div style="margin-top:3px"><span class="chip '+chipCls+'">'+label+'</span> '+
     '<span class="chip info">'+esc(s.level)+'</span>'+
     (s.paused?' <span class="chip gray">已暂停</span>':'')+'</div></div></div>'+
     '<div class="ladder">'+ladder+'</div><div class="meta">'+meta+'</div>'+
     evid+issues+notes+
     '<div class="card-actions">'+
       '<button class="mini" data-act="check" data-key="'+esc(s.key)+'">体检这一个</button>'+

       '<button class="mini" data-act="edit" data-key="'+esc(s.key)+'">编辑</button>'+
       '<button class="mini" data-act="pause" data-key="'+esc(s.key)+'">暂停</button>'+
       '<button class="mini danger" data-act="delete" data-key="'+esc(s.key)+'">删除</button>'+
     '</div></div>';
 }
 if(!svcs.length)html='<div class="empty">还没有配置服务。用右边的「自动识别」或填一个名字和端口就行。</div>';
 el('cards').innerHTML=html;
 // Fill the service selectors once per refresh, preserving the user's choice.
 var opts='<option value="">所有服务</option>',logOpts='<option value="">选一个服务</option>';
 for(var k=0;k<svcs.length;k++){
   var o='<option value="'+esc(svcs[k].key)+'">'+esc(svcs[k].name)+' :'+esc(svcs[k].port)+'</option>';
   opts+=o;logOpts+=o;
 }
 var keep=el('tl-service').value,keepLog=el('log-service').value;
 el('tl-service').innerHTML=opts;el('tl-service').value=keep||'';
 el('log-service').innerHTML=logOpts;el('log-service').value=keepLog||'';
 el('cards').onclick=function(ev){
   var node=ev.target,act=null,key=null,hops=0;
   while(node&&hops<5){
     if(node.getAttribute){
       act=act||node.getAttribute('data-act');
       key=key||node.getAttribute('data-key');
     }
     if(act&&key)break;
     node=node.parentNode;hops++;
   }
   if(act&&key)serviceAction(act,key);
 };
}
function serviceAction(act,key){
 var row=null,list=(lastState&&lastState.services)||[];
 for(var i=0;i<list.length;i++)if(list[i].key===key)row=list[i];
 if(act==='edit'){
   if(!row){say('这个服务已经不在列表里了，先刷新。',true);return;}
   editKey=key;lastRow=row;
   el('edit-title').textContent='编辑 '+row.name;
   el('edit-sub').textContent='配置键 '+key+' · 端口 '+row.port;
   el('edit-name').value=row.name||'';
   el('edit-port').value=row.port||'';
   editIsTcp=row.health_path==='TCP';
   el('edit-health').value=editIsTcp?'':(row.health_path||'/');
   el('edit-health').placeholder=editIsTcp?'非 HTTP 服务，无路径':'/';
   el('edit-canary').value=row.canary_path||'';
   el('edit-period').value=row.heartbeat_period||60;
   el('edit-log').value='';
   el('edit-restart').checked=!!row.has_recovery;
   el('edit-restart-cmd').value=row.restart_hint||'';
   el('edit-restart-row').style.display=row.has_recovery?'flex':'none';
   el('editor').style.display='block';
   return;
 }
 if(act==='delete'&&!confirm('删除后这个服务不再被守护，配置里也会移除。确定吗？'))return;
 var routes={heartbeat:'/api/heartbeat',delete:'/api/service/action',
             pause:'/api/service/action',check:'/api/check'};
 var payload=act==='heartbeat'?{key:key,ok:true}:
             act==='check'?{key:key}:
             {key:key,action:act};
 say('正在处理…');
 post(routes[act],payload).then(function(r){
   say(r.message||'已完成');
   return refresh();
 }).catch(function(e){say('操作失败：'+explain(e),true);});
}
function renderTimeline(rows){
 var tl='';
 for(var k=0;k<(rows||[]).length;k++){
   var r=rows[k];
   tl+='<tr><td>'+stamp(r.ts)+'</td><td>'+kindChip(r.kind)+'</td><td>'+
       esc(r.service||'fleet')+(r.port?':'+esc(r.port):'')+'</td>'+
       '<td class="msg-cell">'+esc(r.message||'')+'</td></tr>';
 }
 el('timeline').innerHTML=tl;
 el('tl-empty').style.display=(rows||[]).length?'none':'block';
 el('tl-count').textContent=(rows||[]).length?('('+rows.length+' 条)'):'';
}
function loadTimeline(){
 var q='/api/history?last='+encodeURIComponent(el('tl-window').value||'24h')+
       '&limit='+encodeURIComponent(el('tl-limit').value||'20');
 if(el('tl-kind').value)q+='&kind='+encodeURIComponent(el('tl-kind').value);
 if(el('tl-service').value)q+='&service='+encodeURIComponent(el('tl-service').value);
 return api(q).then(function(d){renderTimeline(d.rows||[]);return d;})
   .catch(function(e){say('事件读取失败：'+explain(e),true);return null;});
}
function renderAlerts(ov){
 var rows=(ov&&ov.alert_rows)||[],h='';
 for(var i=rows.length-1;i>=0&&h.length<12000;i--){
   var a=rows[i];
   h+='<div class="alert-item"><span class="a-time">'+esc(a.logged_at||'-')+'</span>'+
      '<span class="a-msg"><b>'+esc(a.title||'alert')+'</b> · '+esc(a.message||'')+'</span></div>';
 }
 el('alert-list').innerHTML=h||'<div class="empty">还没有告警。服务出问题或恢复时会出现在这里。</div>';
 el('alert-count').textContent=rows.length?('('+rows.length+' 条)'):'';
}
function render(st){
 renderPresets(st);
 setConn('已连接 · '+esc(st.generated_at||''),'');
 el('cfgpath').textContent=esc(st.config_path||'-');
 renderKpi(st.overview||{});
 renderServices(st);
 renderTimeline(st.history||[]);
 renderAlerts(st.overview||{});
}
function refresh(){
 if(stopped)return Promise.resolve(false);
 if(inFlight){pendingRefresh=true;return Promise.resolve(false);}
 inFlight=true;
 return api('/api/state').then(function(st){
   inFlight=false;failures=0;stateError=false;
   lastState=st;render(st);showReconnect(false);startPoll();
   if(pendingRefresh){pendingRefresh=false;return refresh();}
   return true;
 }).catch(function(e){
   inFlight=false;stateError=true;
   return diagnose().then(function(kind){
     var msg;
     if(kind==='DOWN'){
       msg='读取失败：连不上本机服务了。程序窗口可能已被关掉，或刚才点了「停止程序」。'
          +'重新双击 fleetkeeper.exe，用新窗口里打印的网址打开本页（每次启动端口都会变，旧网址作废）。';
       setConn('服务已退出','off');
     }else{
       msg='读取失败：服务还在，但状态读取出错（'+explain(e)+'）。等几秒重试，或看程序窗口里的报错。';
       setConn('状态异常','off');
     }
     failures++;
     if(failures>=MAX_FAILURES){
       msg+=' 自动刷新已暂停：程序恢复后可点右上角「重新连接」；如果已经重启过 exe，请直接关掉这个旧标签页。';
       pausePoll();
     }
     say(msg,true);
     if(pendingRefresh){pendingRefresh=false;return refresh();}
     return false;
   });
 });
}
function boot(){
 renderSkeleton();
 // A cheap config read decides whether discovery may write. Both the state
 // read and the scan start immediately, so the first paint never waits on
 // a slow business probe.
 api('/api/config').then(function(c){
   runDiscovery(!!c.is_placeholder);
 }).catch(function(){runDiscovery(false);});
 refresh();
 loadTimeline();
}
el('btn-check').onclick=function(){
 say('正在体检…');refresh().then(function(ok){if(ok)say('体检完成。');});
};
el('btn-reconnect').onclick=function(){
 stopped=false;failures=0;
 say('正在重新连接…');
 refresh().then(function(ok){if(ok){say('已重新连上。');startPoll();}});
};
el('btn-sweep').onclick=function(){
 say('正在跑一次守护…');
 post('/api/sweep').then(function(r){
   var parts=[];
   parts.push('问题 '+((r.issues||[]).length)+' 条');
   parts.push('已恢复 '+((r.healed||[]).length)+' 个');
   parts.push('告警 '+((r.alerts||[]).length)+' 条');
   if(r.network_down)parts.push('网络断开：本次不自愈');
   var msg='守护完成：'+parts.join(' · ');
   return refresh().then(function(ok){
     // A sweep only checks processes and ports; the cards grade business
     // evidence. "0 issues" beside a red card looks like a bug, so spell
     // out that the two are answering different questions.
     if(ok&&lastState&&!(r.issues||[]).length){
       var bad=0,list=lastState.services||[];
       for(var i=0;i<list.length;i++){
         if(/^L[012]/.test((list[i]||{}).level||''))bad++;
       }
       if(bad)msg+='。守护只查进程和端口：下面还有 '+bad+' 张未通过的卡片，是业务探针没过，不用重启程序。';
     }
     if(ok)say(msg);       // a failed refresh already wrote the better message
     loadTimeline();
   });
 }).catch(function(e){say('守护失败：'+explain(e),true);});
};
el('btn-report').onclick=function(){window.open('/report','_blank');};
el('btn-export').onclick=function(){window.open('/api/config?view=full&download=1','_blank');};
el('btn-export-side').onclick=function(){window.open('/api/config?view=full&download=1','_blank');};
el('btn-stop').onclick=function(){
 if(!confirm('停止后这个页面就不能用了，确定吗？'))return;
 say('正在停止…');
 // shutdown() may kill the socket before the response is flushed, so both
 // the success and the failure branch mean the same thing to this page.
 post('/api/stop').then(stopUI,stopUI);
};
el('btn-watch').onclick=function(){
 var interval=parseInt(el('watch-interval').value,10)||60;
 say(watchRunning?'正在停止常驻守护…':'正在启动常驻守护…');
 post('/api/watch',{action:watchRunning?'stop':'start',interval:interval})
  .then(function(r){say(r.message||'已处理');return refresh();})
  .catch(function(e){say('常驻守护操作失败：'+explain(e),true);});
};
el('btn-notify-test').onclick=function(){
 say('正在发送测试通知…');
 post('/api/notify-test',{}).then(function(r){say(r.message||'已完成');})
  .catch(function(e){say('通知测试失败：'+explain(e),true);});
};
el('btn-discover').onclick=function(){
 say('正在识别本机服务…');
 runDiscovery(false).then(function(ok){
  if(ok)say('识别完成：'+discoveryData.length+' 个候选服务。勾上要加的，再点「把勾中的加进配置」。');
 });
};
el('btn-discover-add').onclick=function(){
 var ids=selectedIds();
 if(!ids.length){say('先在上面勾中要加入的服务。');return;}
 say('正在写入配置…');
 post('/api/discover',{ids:ids}).then(function(r){
  say(r.message||'已写入配置');renderDiscovery(r);return refresh();
 }).catch(function(e){say('写入失败：'+explain(e),true);});
};
el('preset-select').onchange=showPresetDetail;
el('preset-form').onsubmit=function(ev){
 ev.preventDefault();
 var name=el('preset-select').value;
 if(!isPlaceholder&&!confirm('套用场景会覆盖当前的服务列表，确定吗？'))return;
 say('正在套用场景…');
 post('/api/preset',{name:name,force:true}).then(function(r){
   say(r.message||'已套用场景');return refresh();
 }).catch(function(e){say('套用失败：'+explain(e),true);});
};
el('add').onsubmit=function(ev){
 ev.preventDefault();
 var f=ev.target,name=f.name.value.trim(),port=parseInt(f.port.value,10);
 if(!name||!port){say('名字和端口都要填。');return;}
 post('/api/service',{name:name,port:port}).then(function(r){
   say(r.message||'已保存');f.name.value='';f.port.value='';return refresh();
 }).catch(function(e){say('保存失败：'+explain(e),true);});
};
el('btn-tl-reload').onclick=function(){say('正在读取事件…');loadTimeline().then(function(d){
 if(d)say('事件已更新，共 '+((d.rows||[]).length)+' 条。');});};
el('btn-config').onclick=function(){
 api('/api/config?view=full').then(function(d){
   el('config-text').textContent=d.text||'（空配置）';say('配置已读取，敏感值已打码。');
 }).catch(function(e){say('配置读取失败：'+explain(e),true);});
};
el('btn-log').onclick=function(){
 var key=el('log-service').value;
 if(!key){say('先选一个服务。');return;}
 var q='/api/logs?service='+encodeURIComponent(key)+
       '&tail='+encodeURIComponent(el('log-tail').value||'40')+
       (el('log-raw').checked?'&raw=1':'');
 api(q).then(function(d){
   el('log-output').textContent=(d.lines||[]).join('\\n')||
     (d.error||'（日志为空）');
   say(d.error||('日志已读取，脱敏命中 '+d.redacted+' 处。'));
 }).catch(function(e){say('日志读取失败：'+explain(e),true);});
};
el('btn-edit-close').onclick=function(){el('editor').style.display='none';editKey=null;lastRow=null;};
el('btn-edit-heartbeat').onclick=function(){
 if(!editKey)return;
 post('/api/heartbeat',{key:editKey,ok:true}).then(function(r){
   say(r.message||'已写入一条心跳');
 }).catch(function(e){say('操作失败：'+explain(e),true);});
};
el('btn-edit-save').onclick=function(){
 if(!editKey){el('editor').style.display='none';return;}
 var patch={
   name:el('edit-name').value.trim(),port:parseInt(el('edit-port').value,10),
   canary_path:el('edit-canary').value.trim(),
   heartbeat_period:parseInt(el('edit-period').value,10)
 };
 if(!editIsTcp)patch.health_path=el('edit-health').value.trim();
 if(el('edit-log').value.trim())patch.log_file=el('edit-log').value.trim();
 if(el('edit-restart').checked){
   var cmd=el('edit-restart-cmd').value.trim();
   if(!cmd){say('勾了自动重启就要填重启命令，或把勾去掉。',true);return;}
   patch.restart_command=cmd;
 }else if(lastRow&&lastRow.has_recovery){
   patch.restart_command='';   // explicit clear, not a silent no-op field
 }
 say('正在保存修改…');
 post('/api/service/update',{key:editKey,patch:patch}).then(function(r){
   say(r.message||'已保存');el('editor').style.display='none';editKey=null;lastRow=null;
   return refresh();
 }).catch(function(e){say('修改失败：'+explain(e),true);});
};
el('edit-restart').onchange=function(){
 el('edit-restart-row').style.display=el('edit-restart').checked?'flex':'none';
};

// --- notify settings dialog ------------------------------------------------
var nfShown=false;
el('btn-notify-set').onclick=function(){
 get('/api/notify-settings').then(function(s){
   el('nf-url').value=s.webhook_url||'';
   el('nf-on').checked=!!s.webhook_on;
   el('notify-box').style.display='block';nfShown=true;
 }).catch(function(e){say('读取通知设置失败：'+explain(e),true);});
};
el('btn-nf-close').onclick=function(){el('notify-box').style.display='none';nfShown=false;};
el('btn-nf-save').onclick=function(){
 say('正在保存通知设置…');
 post('/api/notify-settings',{webhook_url:el('nf-url').value.trim(),
                              webhook_on:el('nf-on').checked}).then(function(r){
   say(r.message||'已保存');el('notify-box').style.display='none';nfShown=false;
 }).catch(function(e){say('保存失败：'+explain(e),true);});
};
el('btn-nf-test').onclick=function(){
 say('正在发送测试通知…');
 post('/api/notify-test',{}).then(function(r){
   say(r.message||(r.configured?'测试通知已发出':'还没配置通知地址'));
 }).catch(function(e){say('发送失败：'+explain(e),true);});
};

// --- autostart toggle ------------------------------------------------------
el('btn-autostart').onclick=function(){
 var btn=el('btn-autostart');
 get('/api/autostart').then(function(s){
   if(!s.supported){say(s.message||'这个系统暂不支持开机自启',true);return;}
   say(s.installed?(s.message+' 点「关闭开机自启」可撤掉。'):s.message);
   btn.textContent=s.installed?'关闭开机自启':'开机自启';
   btn.onclick=function(){
     say('正在处理开机自启…');
     post('/api/autostart',{action:s.installed?'remove':'install'}).then(function(r){
       say(r.message||'已完成');refreshAutostart();
     }).catch(function(e){say('操作失败：'+explain(e),true);});
   };
 }).catch(function(e){say('查询失败：'+explain(e),true);});
};
function refreshAutostart(){
 get('/api/autostart').then(function(s){
   if(s.supported)el('btn-autostart').textContent=s.installed?'关闭开机自启':'开机自启';
 }).catch(function(){});
}
startPoll();
boot();
refreshAutostart();
</script>
</body>
</html>
"""
