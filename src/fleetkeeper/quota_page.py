# -*- coding: utf-8 -*-
"""The full free-quota board, served at ``/quota`` from the same port.

Why a second page instead of a bigger panel: the main dashboard is deliberately
dense (service health, evidence ladder, timeline) and answers "is the fleet OK
right now". Deciding *which* grant to go collect is a browsing task -- search,
filter, sort, forty-odd cards. Cramming both into one screen made the dashboard
worse without making the board better.

It is still one process and one port. ``/api/quota`` is the only data source,
so the panel on ``/`` and this page never disagree about the numbers.
"""

QUOTA_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>免费额度 · fleetkeeper</title>
<style>
:root{
  --bg:#f5f6fa;--card:#fff;--ink:#151a26;--sub:#697086;
  --line:#e3e6ef;--brand:#4d6bfe;--gold:#c08a12;--ok:#1a9c68;
  --radius:12px;
}
@media(prefers-color-scheme:dark){
  :root{--bg:#12151c;--card:#191d27;--ink:#e8ebf3;--sub:#9aa3b8;
        --line:#282e3b;--brand:#7b90ff;--gold:#e0ab3c;--ok:#3fce95;}
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);
  font:14px/1.55 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{max-width:1080px;margin:0 auto;padding:18px 16px 56px}

/* ---------- 顶栏 ---------- */
.top{display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.top .back{font-size:12.5px;color:var(--sub);text-decoration:none;
  border:1px solid var(--line);background:var(--card);border-radius:8px;
  padding:4px 9px;white-space:nowrap}
.top .back:hover{color:var(--brand);border-color:var(--brand)}
.top h1{font-size:16px;font-weight:700;margin:0;letter-spacing:.01em}
.top .meta{margin-left:auto;display:flex;align-items:center;gap:9px;
  font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums}
.mini{font:inherit;font-size:12px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:8px;padding:4px 10px;cursor:pointer}
.mini:hover{border-color:var(--brand);color:var(--brand)}
.mini[disabled]{opacity:.5;cursor:default}
.mini.on{background:var(--brand);border-color:var(--brand);color:#fff}

/* ---------- hero ---------- */
.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;
  flex-wrap:wrap;padding:19px 22px;border-radius:16px;color:#fff;
  background:linear-gradient(132deg,#161d33 0%,#2b3fae 60%,#4d6bfe 100%);
  box-shadow:0 16px 34px -24px rgba(29,42,120,.95)}
.amt{display:flex;align-items:baseline;gap:1px;line-height:1}
.amt small{font-size:19px;font-weight:650;opacity:.85;margin-right:2px}
.amt b{font-size:52px;font-weight:800;letter-spacing:-2px;
  font-variant-numeric:tabular-nums}
.amt .cap{font-size:12px;opacity:.78;margin-left:9px;font-weight:500}
.sub{font-size:12px;opacity:.8;margin-top:7px}
.sub .warn{color:#ffd8a8}
.stats{display:flex;gap:22px;flex-wrap:wrap}
.stats div{display:flex;flex-direction:column;gap:3px;min-width:52px}
.stats b{font-size:19px;font-weight:750;line-height:1.1;
  font-variant-numeric:tabular-nums}
.stats span{font-size:11px;opacity:.78}

/* ---------- 工具条 ---------- */
.bar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:18px 0 4px}
.tabs{display:inline-flex;gap:2px;padding:3px;background:var(--card);
  border:1px solid var(--line);border-radius:10px;max-width:100%;overflow-x:auto;
  scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs button{font:inherit;font-size:12.5px;color:var(--sub);background:none;
  border:0;border-radius:7px;padding:4px 11px;cursor:pointer;white-space:nowrap}
.tabs button.on{background:var(--brand);color:#fff;font-weight:600}
#q{font:inherit;font-size:12.5px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:9px;padding:6px 10px;min-width:150px;
  flex:1 1 150px;max-width:230px;-webkit-appearance:none}
#q:focus{outline:none;border-color:var(--brand)}
#sort{font:inherit;font-size:12.5px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:9px;padding:6px 8px;cursor:pointer}

/* ---------- 分组与卡片 ---------- */
.group{margin-top:20px}
.group h2{display:flex;align-items:baseline;gap:8px;font-size:12px;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;color:var(--sub);margin:0 0 9px}
.group h2 em{font-style:normal;font-size:11px;font-weight:500;letter-spacing:0;
  color:var(--sub);opacity:.75;text-transform:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:9px}
.cd{position:relative;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:9px 11px 9px 14px;display:flex;
  flex-direction:column;gap:3px;min-height:72px;
  transition:border-color .15s,transform .15s,box-shadow .15s}
.cd:before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:3px;
  border-radius:0 3px 3px 0;background:var(--brand)}
.cd.k-money:before{background:var(--gold)}
.cd.k-free:before{background:var(--ok)}
.cd:hover{transform:translateY(-1px);border-color:var(--brand);
  box-shadow:0 8px 20px -14px rgba(40,60,150,.75)}
.cd .nm{font-size:12.5px;font-weight:650;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden;word-break:break-word}
.cd .nm a{text-decoration:none}
.cd .nm a:hover{color:var(--brand)}
.cd .vl{margin-top:auto;font-size:17px;font-weight:750;letter-spacing:-.3px;
  font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.cd .vl small{font-size:.68em;font-weight:650;opacity:.66;margin-right:1px}
.cd .tag{font-size:10.5px;color:var(--sub);border:1px solid var(--line);
  border-radius:5px;padding:0 5px;align-self:flex-start;margin-top:2px}
.cd.done{opacity:.55}
.cd.done .vl{text-decoration:line-through}
.cd.gone{opacity:.4}
/* 现金卡：名称左、金额右，一眼扫完一列数字 */
.grid.wide{grid-template-columns:repeat(auto-fill,minmax(212px,1fr))}
.grid.wide .cd{flex-direction:row;align-items:center;gap:10px;min-height:62px}
.grid.wide .cd .top2{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.grid.wide .cd .vl{margin:0;text-align:right;font-size:19px}

.empty{margin-top:18px;padding:26px;text-align:center;color:var(--sub);
  font-size:13px;border:1px dashed var(--line);border-radius:var(--radius)}

/* ---------- 重置时间线 ---------- */
.tl{margin-top:26px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);padding:14px 16px}
.tl h2{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--sub);margin:0 0 10px;display:flex;align-items:baseline;gap:8px}
.tl h2 em{font-style:normal;font-size:11px;font-weight:500;letter-spacing:0;
  text-transform:none;opacity:.72}
.tl .row{display:flex;gap:9px;align-items:baseline;padding:5px 0;font-size:12.5px;
  border-top:1px dashed var(--line)}
.tl .row:first-of-type{border-top:0}
.tl .row time{font-variant-numeric:tabular-nums;color:var(--sub);flex:none;width:96px}
.tl .row .p{font-weight:650;flex:none;width:78px;color:var(--ink)}
.tl .row .t{color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl .row .pp{flex:none;font-size:10.5px;color:var(--gold);border:1px solid currentColor;
  border-radius:5px;padding:0 4px}
.note{font-size:11.5px;color:var(--sub);line-height:1.7;margin:18px 0 0}

@media(max-width:560px){
  .wrap{padding:14px 12px 40px}
  .bar{gap:8px}
  .tabs{width:100%;order:0}
  #only-todo{order:1;flex:none}
  #q{order:2;flex:1 1 110px;max-width:none;min-width:0}
  #sort{order:3;flex:none}
  .amt b{font-size:40px}
  .stats{gap:15px}
  .grid,.grid.wide{grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
  .grid.wide .cd{flex-direction:row;min-height:58px}
  .grid.wide .cd .vl{font-size:17px}
  .top .meta{width:100%;margin-left:0;justify-content:space-between}
}
</style>
</head>
<body>
<div class="wrap">

  <div class="top">
    <a class="back" href="/">← 控制台</a>
    <h1>免费额度</h1>
    <div class="meta"><span id="asof">正在读取…</span>
      <button class="mini" id="btn">刷新</button></div>
  </div>

  <section class="hero">
    <div>
      <div class="amt"><small>¥</small><b id="today">–</b><span class="cap">今天还能领</span></div>
      <div class="sub" id="sub">&nbsp;</div>
    </div>
    <div class="stats">
      <div><b id="s-todo">–</b><span>可领卡</span></div>
      <div><b id="s-money">–</b><span>现金卡</span></div>
      <div><b id="s-sum">–</b><span title="所有现金卡的面额之和；「今天还能领」是源站的当日口径">面额合计</span></div>
      <div><b id="s-reset">–</b><span>重置事件</span></div>
    </div>
  </section>

  <div class="bar">
    <div class="tabs" id="tabs">
      <button class="on" data-k="all">全部</button>
      <button data-k="money">现金</button>
      <button data-k="unit">额度</button>
      <button data-k="free">免费</button>
    </div>
    <button class="mini" id="only-todo" aria-pressed="false">只看可领</button>
    <input id="q" type="search" placeholder="搜产品名…" autocomplete="off">
    <select id="sort">
      <option value="worth">金额优先</option>
      <option value="name">名称 A→Z</option>
      <option value="kind">按类型</option>
    </select>
  </div>

  <div id="board"></div>
  <div class="empty" id="empty" hidden>这个筛选下没有卡片</div>

  <section class="tl" id="tl" hidden>
    <h2>额度重置 <em id="tl-note"></em></h2>
    <div id="tl-body"></div>
  </section>

  <p class="note">数据来自 aihot.news 的公开免费额度页与 /api/v1 重置流，免注册、不涉及任何账号。
  本机只缓存渲染结果，不上报任何信息。金额为源站换算值，规则以厂商页面为准。</p>

</div>

<script>
(function(){
  "use strict";
  var KIND_NAME = {money:"现金额度", unit:"积分 / Token / 算力", free:"免费试用"};
  var KIND_ORDER = {money:0, unit:1, free:2};
  var st = {data:null, kind:"all", q:"", sort:"worth", todo:false, busy:false};

  function $(id){ return document.getElementById(id); }
  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];
    });
  }
  function fmtMoney(n){
    if(n == null) return "–";
    var v = Math.round(n * 10) / 10;
    return (v % 1 === 0) ? String(v) : v.toFixed(1);
  }

  // "¥334" -> ["¥","334"]；"1.5万PU" -> ["","1.5万PU"]
  // 货币符号单独包一层，字号小一点，数字才会顶对齐
  function splitValue(v){
    var m = /^([¥$€])\s*(.+)$/.exec(v || "");
    if(m) return {cur:m[1], num:m[2]};
    return {cur:"", num:v || ""};
  }

  // 类型和状态是两根独立的轴：可以只看「现金里还没领的」
  function match(c){
    if(st.todo && c.state !== "todo") return false;
    if(st.kind !== "all" && c.kind !== st.kind) return false;
    if(st.q && (c.name || "").toLowerCase().indexOf(st.q) < 0) return false;
    return true;
  }

  function sorted(list){
    var arr = list.slice();
    if(st.sort === "name"){
      arr.sort(function(a,b){ return (a.name||"").localeCompare(b.name||"", "zh-Hans-CN"); });
    } else if(st.sort === "kind"){
      arr.sort(function(a,b){
        var d = (KIND_ORDER[a.kind]||9) - (KIND_ORDER[b.kind]||9);
        return d || ((b.worth||0) - (a.worth||0));
      });
    }
    // worth：服务端已按 现金 → 金额降序 排好，保持原序即可
    return arr;
  }

  function card(c){
    var v = splitValue(c.value);
    var val = (v.cur ? "<small>" + esc(v.cur) + "</small>" : "") + esc(v.num);
    var name = c.url
      ? '<a href="' + esc(c.url) + '" target="_blank" rel="noopener noreferrer">' + esc(c.name) + "</a>"
      : esc(c.name);
    var cls = "cd k-" + esc(c.kind) + (c.state === "done" ? " done" : c.state === "gone" ? " gone" : "");
    var tag = (c.state && c.state !== "todo")
      ? '<span class="tag">' + esc(c.status || c.state) + "</span>" : "";
    // 宽卡（现金）要一个左列容器：名称+状态竖排，金额独立右对齐
    if(c.kind === "money"){
      return '<article class="' + cls + '"><div class="top2"><span class="nm">' + name +
             "</span>" + tag + '</div><span class="vl">' + val + "</span></article>";
    }
    return '<article class="' + cls + '"><span class="nm">' + name + "</span>" +
           tag + '<span class="vl">' + val + "</span></article>";
  }

  function renderBoard(){
    var d = st.data;
    if(!d) return;
    var cards = (d.cards || []).filter(match);
    var groups = {money:[], unit:[], free:[]};
    cards.forEach(function(c){ (groups[c.kind] || groups.unit).push(c); });

    var html = "";
    ["money","unit","free"].forEach(function(k){
      var list = sorted(groups[k]);
      if(!list.length) return;
      html += '<section class="group"><h2>' + KIND_NAME[k] +
              "<em>" + list.length + " 张</em></h2><div class=\"grid" +
              (k === "money" ? " wide" : "") + '">';
      list.forEach(function(c){ html += card(c); });
      html += "</div></section>";
    });
    $("board").innerHTML = html;
    $("empty").hidden = cards.length > 0;
  }

  function renderTimeline(){
    var d = st.data, rs = (d && d.resets) || [];
    var box = $("tl"), body = $("tl-body");
    if(!rs.length){
      box.hidden = true;
      body.innerHTML = "";
      return;
    }
    box.hidden = false;
    // 有 partial 说明只拿到近期变更（全量历史被限流），得说清楚而不是假装完整
    var partial = rs.some(function(r){ return r.partial; });
    $("tl-note").textContent = partial ? "近期变更（历史被上游限流，稍后自动补全）" : "来自三个产品的公开事件流";
    var html = "";
    rs.slice(0, 12).forEach(function(r){
      html += '<div class="row"><time>' + esc((r.time || "").slice(0, 16)) + "</time>" +
              '<span class="p">' + esc(r.product || "") + "</span>" +
              '<span class="t" title="' + esc(r.title || "") + '">' + esc(r.title || r.quote || "") + "</span>" +
              (r.partial ? '<span class="pp">近期</span>' : "") + "</div>";
    });
    body.innerHTML = html;
  }

  function render(){
    var d = st.data;
    if(!d) return;
    var s = d.stats || {};
    $("today").textContent = fmtMoney(d.todayTotal);
    $("s-todo").textContent = s.todo || 0;
    $("s-money").textContent = s.money || 0;
    $("s-sum").textContent = fmtMoney(s.moneySum);
    $("s-reset").textContent = (d.resets || []).length;

    var sub = "本月已领 ¥" + fmtMoney(d.monthTotal) + " · 共 " + (s.total || 0) + " 张卡";
    if(d.error) sub += ' · <span class="warn">取数失败，显示缓存</span>';
    $("sub").innerHTML = sub;

    var asof = d.asOf ? "数据 " + String(d.asOf).replace("T", " ").slice(0, 16) : "数据时间未知";
    if(d.ageSec != null && d.ageSec > 1800) asof += " · " + Math.round(d.ageSec / 60) + " 分钟前";
    $("asof").textContent = asof;

    renderBoard();
    renderTimeline();
  }

  function load(){
    if(st.busy) return;
    st.busy = true;
    var btn = $("btn");
    btn.disabled = true;
    fetch("/api/quota", {headers:{Accept:"application/json"}, cache:"no-store"})
      .then(function(r){
        if(!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function(d){
        st.data = d;
        render();
      })
      .catch(function(e){
        // 拿不到就留着上一份，别把已经看到的数字擦掉
        if(!st.data){
          $("sub").innerHTML = '<span class="warn">连不上本机服务 · ' + esc(e.message) + "</span>";
        }
      })
      .then(function(){
        st.busy = false;
        btn.disabled = false;
      });
  }

  $("tabs").addEventListener("click", function(ev){
    var b = ev.target.closest("button");
    if(!b) return;
    st.kind = b.dataset.k;
    [].forEach.call(this.querySelectorAll("button"), function(x){
      x.classList.toggle("on", x === b);
    });
    renderBoard();
  });

  var timer = null;
  $("q").addEventListener("input", function(){
    clearTimeout(timer);
    var v = this.value.trim().toLowerCase();
    // 40 张卡不用 debounce，但连续输入时重建 DOM 会打断中文输入法，留一帧
    timer = setTimeout(function(){ st.q = v; renderBoard(); }, 120);
  });

  $("only-todo").addEventListener("click", function(){
    st.todo = !st.todo;
    this.classList.toggle("on", st.todo);
    this.setAttribute("aria-pressed", st.todo ? "true" : "false");
    renderBoard();
  });

  $("sort").addEventListener("change", function(){ st.sort = this.value; renderBoard(); });
  $("btn").addEventListener("click", load);

  load();
  // 面板和 CLI 共用同一份缓存，30 秒一次足够；页面在后台时不占带宽
  setInterval(function(){ if(!document.hidden) load(); }, 30000);
  document.addEventListener("visibilitychange", function(){ if(!document.hidden) load(); });
})();
</script>
</body>
</html>
"""
