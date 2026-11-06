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

function makeEnv(stateFailure, pingFailure) {
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
  // The reconnect button starts hidden in the real HTML.
  el('btn-reconnect').style.display = 'none';
  const timers = [];
  const okJson = (obj) => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(obj)
  });
  const fetch = (path) => {
    if (path === '/api/state') {
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
    return Promise.reject(new TypeError('Failed to fetch'));
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

async function boot(stateFailure, pingFailure) {
  const env = makeEnv(stateFailure, pingFailure);
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

  if (process.exitCode) {
    console.error('console error-path verification FAILED');
  } else {
    console.log('ALL CONSOLE ERROR PATHS PASS');
  }
})();
