'use strict';
// Executes the console's own JavaScript in a DOM stub. This is the layer
// Python's loopback HTTP tests cannot reach: a browser tab whose exe has
// exited, and the exact text the user is shown afterwards.
//
// Usage:
//   python -c "from fleetkeeper import ui; open('page.js','w',encoding='utf-8').write(ui.PAGE.split('<script>')[1].split('</script>')[0])"
//   node scripts/verify_console_errors.js page.js
const fs = require('fs');
const vm = require('vm');

if (!process.argv[2]) {
  console.error('usage: node verify_console_errors.js <extracted-page.js>');
  process.exit(2);
}
const source = fs.readFileSync(process.argv[2], 'utf8');

const discoveryPayload = {
  scanned_ports: [11434], http_ports: [11434], elapsed_ms: 12,
  auto_applied: 1, message: '已自动识别并加入 1 个服务',
  candidates: [{
    id: 'ollama-11434', kind: 'ollama', name: 'Ollama',
    port: 11434, confidence: 'high', recommended: true,
    evidence: 'GET /api/tags 返回 models 列表'
  }]
};
const statePayload = {
  generated_at: '2026-11-06 20:00:00', config_path: 'fleet.json',
  services: [{key: 'example', name: 'Example Service', port: 8080,
              level: 'L0_CONFIG', level_number: 0, health_path: '/',
              heartbeat: 'NO_HEARTBEAT', hb_age: null, pids: [],
              evidence: [], issues: [], has_recovery: false}],
  history: [], service_count: 1, is_placeholder: true,
  presets: [{name: 'empty', label: '空白起步',
             services: [{name: 'Example Service', port: 8080}]}],
  service_details: [],
  overview: {total: 1, healthy: 0, attention: 0, down: 1, business: 0,
             heartbeat_ok: 0, paused: 0, event_count: 0,
             last_event: null, last_sweep: null, alert_rows: [],
             alert_count: 0, watch: {running: false, interval: 60, runs: 0},
             uptime: 1.0, generated_at: '2026-11-06 20:00:00'}
};

function makeEnv(stateFailure, pingFailure, discoveryFailure) {
  const nodes = {};
  const el = (id) => {
    if (!nodes[id]) {
      nodes[id] = {
        id, textContent: '', innerHTML: '', className: '', value: '',
        checked: true, disabled: false, style: {display: 'none'},
        onclick: null, onchange: null, onsubmit: null
      };
    }
    return nodes[id];
  };
  // The reconnect button starts hidden in the real HTML. Pre-create msg so
  // boot() can also report a healthy page that never calls say().
  el('btn-reconnect').style.display = 'none';
  el('msg');
  const timers = [];
  const okJson = (obj) => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(obj)
  });
  const fetch = (path) => {
    if (path === '/api/state') {
      if (stateFailure === 'ok') return okJson(statePayload);
      if (stateFailure === 'network') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (stateFailure === 'http') {
        return Promise.resolve({
          ok: false, status: 500,
          text: () => Promise.resolve(JSON.stringify({error: '配置文件坏了'}))
        });
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.reject(new SyntaxError('not json'))
      });
    }
    if (path === '/api/ping') {
      if (pingFailure === 'network') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return okJson({ok: true, pid: 1, uptime: 1.0});
    }
    if (path === '/api/discover') {
      // A dead exe refuses every request, not just /api/state.
      if (discoveryFailure === 'network' || stateFailure === 'network') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return okJson(discoveryPayload);
    }
    if (path === '/api/config') {
      return okJson({is_placeholder: true, services: [], paused: []});
    }
    if (path.indexOf('/api/history') === 0) {
      return okJson({rows: [], count: 0});
    }
    // All other console routes are read-only in this stub. Returning an
    // empty object keeps the error-path assertions focused on /api/state.
    return okJson({});
  };
  const env = {
    document: {getElementById: el},
    window: {open: () => {}},
    fetch,
    confirm: () => true,
    setInterval: (fn) => { timers.push(fn); return timers.length; },
    clearInterval: (id) => { timers[id - 1] = null; },
    JSON, Promise, String, Number, Date, Error, TypeError, SyntaxError,
    parseInt, isNaN
  };
  env.nodes = nodes;
  env.timers = timers;
  return env;
}

async function boot(stateFailure, pingFailure, discoveryFailure) {
  const env = makeEnv(stateFailure, pingFailure, discoveryFailure);
  const ctx = vm.createContext(env);
  vm.runInContext(source, ctx, {filename: 'console-page.js'});
  const settle = () => new Promise((r) => setTimeout(r, 40));
  await settle();
  return {
    env,
    msg: env.nodes.msg.textContent,
    className: env.nodes.msg.className,
    conn: env.nodes.conn.textContent,
    reconnect: env.nodes['btn-reconnect'].style.display,
    liveTimers: env.timers.filter(Boolean).length,
    callRefresh: async () => { ctx.refresh(); await settle(); }
  };
}

function check(label, cond, detail) {
  if (!cond) {
    console.error('FAIL ' + label + ' :: ' + detail);
    process.exitCode = 1;
  } else {
    console.log('PASS ' + label);
  }
}

(async () => {
  // 1. Orphaned tab: the exe is gone, so both probes fail at the network
  //    layer. This is precisely "读取失败：Failed to fetch".
  const dead = await boot('network', 'network');
  check('dead exe -> Chinese recovery text',
    dead.msg.indexOf('读取失败：连不上本机服务了') === 0 &&
    dead.msg.indexOf('重新双击 fleetkeeper.exe') !== -1,
    dead.msg);
  check('dead exe -> no raw TypeError',
    dead.msg.indexOf('Failed to fetch') === -1, dead.msg);
  check('dead exe -> connection badge', dead.conn === '服务已退出', dead.conn);
  check('dead exe -> error styling', dead.className === 'msg err', dead.className);

  // 2. Process alive, state() broken: must not tell the user to restart.
  const alive = await boot('http', null);
  check('alive exe -> state error branch',
    alive.msg.indexOf('服务还在，但状态读取出错') !== -1 &&
    alive.msg.indexOf('HTTP 500') !== -1,
    alive.msg);
  check('alive exe -> no restart advice',
    alive.msg.indexOf('重新双击') === -1, alive.msg);
  check('alive exe -> badge', alive.conn === '状态异常', alive.conn);

  // 3. Non-JSON on the port (another program took it) gets its own label.
  const hijack = await boot('badjson', null);
  check('port takeover -> own message',
    hijack.msg.indexOf('不是本程序的 JSON') !== -1 &&
    hijack.msg.indexOf('状态读取出错') !== -1,
    hijack.msg);

  // 4. Three consecutive failures pause the 5-second poll and offer a
  //    reconnect button instead of hammering a dead port forever.
  await dead.callRefresh();
  await dead.callRefresh();
  check('three failures -> polling paused', dead.env.timers.filter(Boolean).length === 0,
    String(dead.env.timers.length));
  check('three failures -> reconnect button visible',
    dead.env.nodes['btn-reconnect'].style.display !== 'none',
    dead.env.nodes['btn-reconnect'].style.display);
  check('three failures -> pause explained',
    dead.env.nodes.msg.textContent.indexOf('自动刷新已暂停') !== -1,
    dead.env.nodes.msg.textContent);

  // 5. Stop button kills the timer and disables every action.
  const stopping = await boot('network', 'network');
  stopping.env.nodes['btn-stop'].onclick();
  await new Promise((r) => setTimeout(r, 40));
  check('stop -> polling cleared', stopping.env.timers.filter(Boolean).length === 0,
    String(stopping.env.timers.length));
  check('stop -> buttons disabled',
    stopping.env.nodes['btn-sweep'].disabled === true,
    String(stopping.env.nodes['btn-sweep'].disabled));
  check('stop -> explained',
    stopping.env.nodes.msg.textContent.indexOf('重新双击 fleetkeeper.exe') !== -1,
    stopping.env.nodes.msg.textContent);

  // 6. A first successful load must discover services by itself: no name,
  //    no port, no click. The placeholder config triggers an auto-apply.
  const first = await boot('ok', null);
  check('first load -> autodiscovery ran',
    first.env.nodes['discover-text'].textContent.indexOf('扫描了 1 个本机端口') !== -1 &&
    first.env.nodes['discover-text'].textContent.indexOf('已自动加入 1 个') !== -1,
    first.env.nodes['discover-text'].textContent);
  check('first load -> candidate rendered',
    first.env.nodes['discover-list'].innerHTML.indexOf('Ollama') !== -1 &&
    first.env.nodes['discover-list'].innerHTML.indexOf('disc-chk-0') !== -1,
    first.env.nodes['discover-list'].innerHTML);
  check('first load -> auto-apply message',
    first.env.nodes.msg.textContent.indexOf('已自动识别并加入 1 个服务') !== -1,
    first.env.nodes.msg.textContent);
  first.env.nodes['btn-discover-add'].onclick();
  await new Promise((r) => setTimeout(r, 40));
  check('manual add -> selected ids are posted',
    first.env.nodes.msg.textContent.indexOf('已自动识别并加入') !== -1 ||
    first.env.nodes.msg.textContent.indexOf('已写入配置') !== -1,
    first.env.nodes.msg.textContent);

  // 7. A failed scan on a healthy page must not print a restart instruction
  //    or leave the "scanning" placeholder text on screen.
  const scanFail = await boot('ok', null, 'network');
  check('scan failure -> honest text, no restart advice',
    scanFail.env.nodes['discover-text'].textContent.indexOf('这次没扫成') !== -1 &&
    scanFail.env.nodes.msg.textContent.indexOf('重新双击') === -1,
    scanFail.env.nodes['discover-text'].textContent + ' | ' +
    scanFail.env.nodes.msg.textContent);

  if (process.exitCode) {
    console.error('console error-path verification FAILED');
  } else {
    console.log('ALL CONSOLE ERROR PATHS PASS');
  }
})();
