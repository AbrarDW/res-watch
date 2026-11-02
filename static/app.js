/* res-watch — frontend: process list, selection, live stream, charts. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const LS = {
  get(k, d) { try { const v = localStorage.getItem('rw.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('rw.' + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
};
const R = window.RWCharts;
const HIST = 60, MAX_SEL = 8;
const DEFAULT_FILTERS = {user: 'mine', hideSystem: true, gpuOnly: false, minCpu: 0,
  status: {running: true, sleeping: true, zombie: false, other: true}};
const CLEARED_FILTERS = {user: 'all', hideSystem: false, gpuOnly: false, minCpu: 0,
  status: {running: true, sleeping: true, zombie: true, other: true}};

/* ---------------------------------------------------------------- state */
const S = {
  meta: null, procs: [], byPid: new Map(),
  side: LS.get('side', 'apps'), search: '', sort: LS.get('sort', {col: 'cpu', dir: -1}),
  filters: mergeFilters(LS.get('filters', null)),
  expanded: new Set(LS.get('expanded', [])), showAll: new Set(), popover: false,
  sel: [],
  tab: LS.get('tab', 'overview'), cpuView: LS.get('cpuView', 'bars'), gpuView: LS.get('gpuView', '2d'),
  children: LS.get('children', true), normalize: LS.get('normalize', false), every: LS.get('every', 1),
  paused: false, autoRotate: false, theme: LS.get('theme', 'auto'),
  sys: null, times: [], gpuHist: [], details: {}, ws: null, connected: false,
};
function mergeFilters(f) {
  const base = JSON.parse(JSON.stringify(DEFAULT_FILTERS));
  if (!f) return base;
  return Object.assign(base, f, {status: Object.assign(base.status, f.status || {})});
}
let C = R.colors();
const charts = {};

/* ------------------------------------------------------------- helpers */
function fmtBytes(b, digits = 1) {
  if (b == null) return '–';
  const u = ['B', 'K', 'M', 'G', 'T'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (i >= 3 ? b.toFixed(digits) : Math.round(b)) + (i ? ' ' + u[i] : ' B');
}
const fmtRate = b => b == null ? '–' : fmtBytes(b) + '/s';
const fmtPct = (v, d = 1) => v == null ? '–' : (v >= 100 ? Math.round(v) : v.toFixed(d)) + '%';
function fmtAge(started) {
  if (!started) return '–';
  let s = Math.max(0, Date.now() / 1000 - started);
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}
const cores = () => (S.meta && S.meta.cores) || 1;
const cpuVal = v => v == null ? null : (S.normalize ? v / cores() : v);
const cpuUnit = () => S.normalize ? '% of all cores' : '% of one core';
const pushN = (arr, v) => { arr.push(v); if (arr.length > HIST) arr.shift(); };
const entKey = (kind, id) => `${kind}:${id}`;
const selFind = (kind, id) => S.sel.find(e => e.kind === kind && String(e.id) === String(id));
const statusGroup = s => s === 'running' ? 'running' : s === 'zombie' ? 'zombie'
  : ['sleeping', 'disk-sleep', 'idle', 'waiting', 'parked', 'waking'].includes(s) ? 'sleeping' : 'other';
function slotColor(slot) { return C.s[slot % C.s.length]; }
function api(path, opts) {
  return fetch(path, Object.assign({credentials: 'same-origin'}, opts)).then(r => {
    if (r.status === 401) { location.href = 'login'; throw new Error('auth'); }
    if (!r.ok) throw new Error(r.status);
    return r.json();
  });
}

/* ------------------------------------------------------------ filters */
function passes(p) {
  const f = S.filters;
  if (f.user === 'mine' && S.meta && p.user !== S.meta.user) return false;
  if (f.user === 'system' && !p.system) return false;
  if (f.hideSystem && f.user !== 'system' && (p.system || p.kernel)) return false;
  if (f.gpuOnly && !(p.gpu > 0 || p.gmem > 0)) return false;
  if (f.minCpu > 0 && (p.cpu || 0) < f.minCpu) return false;
  if (!f.status[statusGroup(p.status)]) return false;
  if (S.search) {
    const q = S.search;
    const hay = `${p.pid} ${p.name} ${p.app} ${p.cmd || ''}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}
function activeFilterChips() {
  const f = S.filters, out = [];
  if (f.user !== 'all') out.push(['user', `User: ${f.user === 'mine' ? 'Mine' : 'System'}`]);
  if (f.hideSystem && f.user !== 'system') out.push(['hideSystem', 'Hide system']);
  if (f.gpuOnly) out.push(['gpuOnly', 'Uses GPU']);
  if (f.minCpu > 0) out.push(['minCpu', `CPU ≥ ${f.minCpu}%`]);
  const off = Object.entries(f.status).filter(([, v]) => !v).map(([k]) => k);
  if (off.length && !(off.length === 1 && off[0] === 'zombie')) out.push(['status', `Hide ${off.join(', ')}`]);
  return out;
}
function saveFilters() { LS.set('filters', S.filters); }

/* ------------------------------------------------------------ sidebar */
function sortRows(rows, getters) {
  const {col, dir} = S.sort;
  const g = getters[col] || getters.cpu;
  return rows.sort((a, b) => {
    const x = g(a), y = g(b);
    if (typeof x === 'string' || typeof y === 'string') return dir * String(x).localeCompare(String(y));
    return dir * ((x ?? -1) - (y ?? -1));
  });
}
function th(label, col, cls = '') {
  const on = S.sort.col === col;
  return `<th class="${cls} ${on ? 'sorted' : ''}" data-sort="${col}">${label}${on ? (S.sort.dir < 0 ? ' ▾' : ' ▴') : ''}</th>`;
}
const cpuCell = v => v == null ? '<td class="denied">n/a</td>'
  : `<td class="bar-cell">${v.toFixed(1)}<span class="b" style="width:${Math.min(60, v / 2)}px"></span></td>`;
const gpuCell = g => `<td class="${g == null ? 'faint' : ''}">${g == null ? '–' : Math.round(g)}</td>`;
const memCell = (b, denied) => b == null ? `<td class="${denied ? 'denied' : 'faint'}">${denied ? 'n/a' : '–'}</td>` : `<td>${fmtBytes(b)}</td>`;
const chk = () => '<span class="chk"></span>';
const lock = p => p.denied ? '🔒 ' : '';

function renderSidebar() {
  document.querySelectorAll('#sidemode button').forEach(b => b.classList.toggle('on', b.dataset.mode === S.side));
  const chips = activeFilterChips();
  $('#fchips').innerHTML = chips.map(([k, l]) => `<span class="fchip">${esc(l)} <span class="x" data-clear="${k}" title="Remove">✕</span></span>`).join('')
    + (chips.length ? '<button class="linkbtn" data-clear="all">Clear all</button>' : '');
  $('#fcount').textContent = chips.length;
  $('#fcount').classList.toggle('hidden', !chips.length);
  $('#fbtn').classList.toggle('on', S.popover);
  renderPopover();

  const rows = S.procs.filter(passes);
  const t = $('#ptable');
  if (S.side === 'apps') renderApps(t, rows);
  else if (S.side === 'tree') renderTree(t, rows);
  else renderProcs(t, rows);
  $('#sortinfo').textContent = `Sort: ${({cpu: 'CPU', rss: 'memory', gpu: 'GPU', name: 'name', pid: 'PID', count: 'processes'})[S.sort.col] || S.sort.col}`;
}

function renderProcs(t, rows) {
  $('#sidecount').textContent = `${rows.length} / ${S.procs.length} shown`;
  sortRows(rows, {cpu: p => p.cpu, rss: p => p.rss, gpu: p => p.gpu, name: p => p.name.toLowerCase(), pid: p => p.pid, app: p => p.app});
  t.innerHTML = `<thead><tr><th class="l" style="width:22px"></th>${th('PID', 'pid', 'l')}${th('Name', 'name', 'l')}${th('App', 'app', 'l')}`
    + `${th('CPU %', 'cpu')}${th('Mem', 'rss')}${th('GPU', 'gpu')}</tr></thead><tbody>`
    + rows.slice(0, 1500).map(p => {
      const e = selFind('pid', p.pid) || selFind('app', p.app);
      return `<tr class="${e ? 'sel' : ''}" style="${e ? `--c:${slotColor(e.slot)}` : ''}" data-kind="pid" data-id="${p.pid}" title="${esc(p.cmd)}">
        <td class="l">${chk()}</td><td class="l mono faint">${p.pid}</td><td class="l name">${lock(p)}${esc(p.name)}</td>
        <td class="l"><span class="appbadge">${esc(p.app)}</span></td>${cpuCell(p.cpu)}${memCell(p.rss, p.denied)}${gpuCell(p.gpu)}</tr>`;
    }).join('') + '</tbody>';
}

function aggregateApps(rows) {
  const m = new Map();
  for (const p of rows) {
    let a = m.get(p.app);
    if (!a) m.set(p.app, a = {key: p.app, name: p.app_name || p.app, count: 0, cpu: 0, rss: 0, gpu: null, members: []});
    a.count++; a.cpu += p.cpu || 0; a.rss += p.rss || 0;
    if (p.gpu != null) a.gpu = (a.gpu || 0) + p.gpu;
    a.members.push(p);
  }
  return [...m.values()];
}

function renderApps(t, rows) {
  const apps = aggregateApps(rows);
  $('#sidecount').textContent = `${apps.length} apps`;
  sortRows(apps, {cpu: a => a.cpu, rss: a => a.rss, gpu: a => a.gpu, name: a => a.name.toLowerCase(), count: a => a.count, pid: a => a.count});
  let h = `<thead><tr><th class="l" style="width:22px"></th>${th('App', 'name', 'l')}${th('#', 'count')}${th('CPU %', 'cpu')}${th('Mem', 'rss')}${th('GPU %', 'gpu')}</tr></thead><tbody>`;
  for (const a of apps.slice(0, 500)) {
    const ae = selFind('app', a.key);
    const part = !ae && a.members.some(p => selFind('pid', p.pid));
    const pe = part ? S.sel.find(e => e.kind === 'pid' && a.members.some(p => p.pid === e.id)) : null;
    const color = ae ? slotColor(ae.slot) : pe ? slotColor(pe.slot) : '';
    const open = S.expanded.has(a.key);
    h += `<tr class="${ae ? 'sel' : part ? 'part' : ''}" style="${color ? `--c:${color}` : ''}" data-kind="app" data-id="${esc(a.key)}">
      <td class="l">${chk()}</td>
      <td class="l name" style="max-width:190px" title="${esc(a.name)}"><span class="twist" data-twist="${esc(a.key)}">${open ? '▾' : '▸'}</span><span class="avatar">${esc((a.name[0] || '?').toUpperCase())}</span>${esc(a.name)}</td>
      <td><span class="count">${a.count}</span></td>${cpuCell(a.cpu)}<td>${fmtBytes(a.rss)}</td>${gpuCell(a.gpu)}</tr>`;
    if (open) {
      const mem = a.members.slice().sort((x, y) => (y.cpu || 0) - (x.cpu || 0));
      const lim = S.showAll.has(a.key) ? mem.length : 8;
      for (const p of mem.slice(0, lim)) {
        const pe2 = selFind('pid', p.pid);
        const c2 = pe2 ? slotColor(pe2.slot) : color;
        h += `<tr class="member ${ae || pe2 ? 'sel' : ''}" style="${c2 ? `--c:${c2}` : ''}" data-kind="pid" data-id="${p.pid}" title="${esc(p.cmd)}">
          <td class="l">${chk()}</td><td class="l name"><span class="mono faint">${p.pid}</span> ${lock(p)}${esc(p.name)}</td><td></td>
          <td>${p.cpu == null ? 'n/a' : p.cpu.toFixed(1)}</td>${memCell(p.rss, p.denied)}${gpuCell(p.gpu)}</tr>`;
      }
      if (mem.length > lim) h += `<tr class="more" style="${color ? `--c:${color}` : ''}"><td></td><td class="l" colspan="5"><button class="linkbtn" data-more="${esc(a.key)}">… ${mem.length - lim} more processes</button></td></tr>`;
    }
  }
  t.innerHTML = h + '</tbody>';
}

function renderTree(t, rows) {
  $('#sidecount').textContent = `${rows.length} / ${S.procs.length} shown`;
  const by = new Map(rows.map(p => [p.pid, p])), kids = new Map();
  for (const p of rows) {
    const parent = by.has(p.ppid) && p.ppid !== p.pid ? p.ppid : 0;
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent).push(p);
  }
  for (const list of kids.values()) list.sort((a, b) => a.pid - b.pid);
  let h = `<thead><tr><th class="l" style="width:22px"></th><th class="l">Process tree</th>${th('CPU %', 'cpu')}${th('Mem', 'rss')}</tr></thead><tbody>`;
  let n = 0;
  const walk = (pid, prefix, depth) => {
    const list = kids.get(pid) || [];
    list.forEach((p, i) => {
      if (n++ > 2000) return;
      const last = i === list.length - 1;
      const pfx = depth === 0 ? '' : prefix + (last ? '└─ ' : '├─ ');
      const e = selFind('pid', p.pid) || selFind('app', p.app);
      h += `<tr class="${e ? 'sel' : ''}" style="${e ? `--c:${slotColor(e.slot)}` : ''}" data-kind="pid" data-id="${p.pid}" title="${esc(p.cmd)}">
        <td class="l">${chk()}</td><td class="l name" style="max-width:300px"><span class="tree-pfx">${pfx}</span><span class="mono faint">${p.pid}</span> ${lock(p)}${esc(p.name)}</td>
        ${cpuCell(p.cpu)}${memCell(p.rss, p.denied)}</tr>`;
      walk(p.pid, depth === 0 ? '' : prefix + (last ? '   ' : '│  '), depth + 1);
    });
  };
  walk(0, '', 0);
  t.innerHTML = h + '</tbody>';
}

function renderPopover() {
  const pop = $('#popover');
  pop.classList.toggle('hidden', !S.popover);
  if (!S.popover) return;
  const f = S.filters;
  const radio = v => `<label><input type="radio" name="fuser" value="${v}" ${f.user === v ? 'checked' : ''}> ${v[0].toUpperCase() + v.slice(1)}</label>`;
  const st = k => `<label><input type="checkbox" data-status="${k}" ${f.status[k] ? 'checked' : ''}> ${k}</label>`;
  pop.innerHTML = `<h4>Filters</h4>
    <div class="prow"><span>User</span><div>${radio('mine')}${radio('all')}${radio('system')}</div></div>
    <div class="prow full"><label><input type="checkbox" id="f-hide" ${f.hideSystem ? 'checked' : ''}> Hide kernel threads &amp; system services</label></div>
    <div class="prow full"><label><input type="checkbox" id="f-gpu" ${f.gpuOnly ? 'checked' : ''}> Only processes using the GPU</label></div>
    <div class="prow"><span>Min CPU</span><div class="slider"><input type="range" id="f-min" min="0" max="50" step="1" value="${f.minCpu}" style="flex:1"><span class="num" id="f-minv">${f.minCpu}%</span></div></div>
    <div class="prow"><span>Status</span><div>${st('running')}${st('sleeping')}${st('zombie')}${st('other')}</div></div>
    <div class="pfoot"><button class="btn" data-pop="reset">Reset</button><button class="btn primary" data-pop="done">Done</button></div>`;
}

/* ---------------------------------------------------------- selection */
function addSel(kind, id) {
  if (selFind(kind, id)) return;
  if (S.sel.length >= MAX_SEL) { toast(`Up to ${MAX_SEL} items can be charted at once.`); return; }
  const used = new Set(S.sel.map(e => e.slot));
  let slot = 0;
  while (used.has(slot)) slot++;
  const p = kind === 'pid' ? S.byPid.get(+id) : null;
  const e = {kind, id: kind === 'pid' ? +id : id, slot, name: p ? p.name : id, last: null, gone: false, goneAt: null,
    h: {cpu: [], rss: [], ior: [], iow: [], gpu: [], gmem: [], cores: []}};
  for (let i = 0; i < S.times.length; i++) for (const k in e.h) e.h[k].push(null);
  S.sel.push(e);
}
function removeSel(kind, id) {
  const i = S.sel.findIndex(e => e.kind === kind && String(e.id) === String(id));
  if (i < 0) return;
  S.sel.splice(i, 1);
}
function toggleSel(kind, id) {
  if (selFind(kind, id)) removeSel(kind, id); else addSel(kind, id);
  selectionChanged();
}
function selectionChanged() {
  subscribe();
  renderSidebar();
  renderMain();
  if (innerWidth <= 768 && S.sel.length) setMView('charts');
}
function subscribe() {
  if (!S.ws || S.ws.readyState !== 1) return;
  S.ws.send(JSON.stringify({type: 'subscribe', children: S.children, every: S.every,
    entities: S.sel.map(e => ({kind: e.kind, id: e.id}))}));
}

/* ------------------------------------------------------------- stream */
let retry = 0;
function connect() {
  const url = new URL('ws', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  S.ws = ws;
  ws.onopen = () => { retry = 0; setConn(true); subscribe(); };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'hello') { S.meta = Object.assign(S.meta || {}, msg); }
    else if (msg.type === 'tick') onTick(msg);
  };
  ws.onclose = ev => {
    setConn(false);
    if (ev.code === 4401) { location.href = 'login'; return; }
    setTimeout(connect, Math.min(10000, 500 * 2 ** retry++));
  };
}
function setConn(ok) {
  S.connected = ok;
  const c = $('#conn');
  c.classList.toggle('ok', ok);
  c.title = ok ? 'Live' : 'Reconnecting…';
}

function onTick(msg) {
  S.sys = msg.system;
  renderTopbar();
  if (S.paused) return;
  pushN(S.times, msg.ts);
  (msg.system.gpus || []).forEach((g, i) => {
    const h = S.gpuHist[i] || (S.gpuHist[i] = {SM: [], MEM: [], ENC: [], DEC: []});
    pushN(h.SM, g.util); pushN(h.MEM, g.mem_util); pushN(h.ENC, g.enc); pushN(h.DEC, g.dec);
  });
  const byId = new Map(msg.entities.map(e => [e.id, e]));
  let relabel = false;  // chip/legend text changed (first data, app size, exit) -> rebuild the view
  for (const e of S.sel) {
    const d = byId.get(entKey(e.kind, e.id));
    if (d && (!e.last || e.last.count !== d.count || e.gone !== !!d.gone)) relabel = true;
    if (d && !d.gone) {
      e.last = d; e.gone = false; e.name = d.name || e.name;
      pushN(e.h.cpu, d.cpu); pushN(e.h.rss, d.rss); pushN(e.h.ior, d.ior); pushN(e.h.iow, d.iow);
      pushN(e.h.gpu, d.gpu); pushN(e.h.gmem, d.gmem); pushN(e.h.cores, d.per_core);
    } else {
      if (d && d.gone && !e.gone) { e.gone = true; e.goneAt = new Date(); }
      for (const k in e.h) pushN(e.h[k], null);
    }
  }
  if (relabel && !S.popover) renderMain(); else updateMain();
}

/* ------------------------------------------------------------- topbar */
const ringCls = v => v > 90 ? 'crit' : v > 70 ? 'warn' : '';
function meter(label, value, pct, extra = '') {
  return `<div class="meter ${extra}"><div class="ring ${ringCls(pct)}" style="--v:${pct || 0}"></div>
    <div><div class="meter-label">${esc(label)}</div><div class="meter-value num">${value}</div></div></div>`;
}
function renderTopbar() {
  const s = S.sys;
  if (!s) return;
  let h = meter('CPU', fmtPct(s.cpu, 0), s.cpu) + meter('RAM', `${fmtBytes(s.ram_used)} / ${fmtBytes(s.ram_total, 0)}`, s.ram_pct, 'optional');
  (s.gpus || []).forEach(g => {
    const mem = g.mem_total ? ` · ${fmtBytes(g.mem_used)}/${fmtBytes(g.mem_total, 0)}` : '';
    h += meter(`GPU${g.index} · ${g.name}`, `${fmtPct(g.util, 0)}${mem}`, g.util);
  });
  $('#meters').innerHTML = h;
  $('#corestrip').innerHTML = s.per_core.map((v, i) => `<i title="C${i} ${v}%" style="height:${Math.max(6, v)}%"></i>`).join('');
}

/* --------------------------------------------------------------- main */
function disposeCharts() {
  for (const k in charts) { try { charts[k].dispose(); } catch (e) { /* already gone */ } delete charts[k]; }
}
function chart(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  if (!charts[id]) charts[id] = echarts.init(el, null, {renderer: 'canvas'});
  return charts[id];
}
function setChart(id, opt, replace = false) { const c = chart(id); if (c) c.setOption(opt, replace); }
addEventListener('resize', () => { for (const k in charts) charts[k].resize(); });

function label(e) { return e.kind === 'app' ? `${e.name} ×${e.last ? e.last.count : '…'}` : `${e.name} ${e.id}`; }
function dot(e) { return `<span class="dot ${e.kind === 'app' ? 'sq' : ''}"></span>`; }

function selectionBar() {
  return `<div class="focusbar">
    <span class="faint">Selected</span>
    ${S.sel.map((e, i) => `<span class="chip" style="--c:${slotColor(e.slot)}">${dot(e)}${e.kind === 'app' ? '▣ ' : ''}${esc(e.name)} <span class="faint mono">${e.kind === 'app' ? '×' + (e.last ? e.last.count : '…') : e.id}</span>${e.gone ? ' <span class="badge gone">exited</span>' : ''}<span class="x" data-remove="${i}" title="Remove">×</span></span>`).join('')}
    <span style="flex:1"></span>
    <button class="btn" data-act="clear">Clear</button>
  </div>
  <div class="tabs">${[['overview', 'Overview'], ['cpu', 'CPU per core'], ['gpu', 'GPU'], ['details', 'Details']]
    .map(([k, l]) => `<button class="tab ${k === S.tab ? 'active' : ''}" data-tab="${k}">${l}</button>`).join('')}</div>`;
}
const legend = items => `<div class="legend">${items.map(([l, c, cls]) => `<span><i class="${cls || ''}" style="--c:${c}"></i>${esc(l)}</span>`).join('')}</div>`;
const selLegend = () => legend(S.sel.map(e => [label(e), slotColor(e.slot)]));

function renderMain() {
  disposeCharts();
  C = R.colors();
  const main = $('#main');
  if (!S.sel.length) {
    main.innerHTML = `<div class="empty"><div>
      <div class="ill"><svg width="40" height="40" viewBox="0 0 40 40" fill="none" stroke="${C.s[0]}" stroke-width="2.5" stroke-linecap="round"><path d="M6 32h28"/><path d="M10 26l7-8 6 5 9-12"/></svg></div>
      <h2>No process selected</h2>
      <p>Tick one or more apps or processes on the left to see live CPU, per-core, GPU, memory and disk charts.</p></div></div>`;
    return;
  }
  const gpuOk = S.meta && S.meta.gpu && S.meta.gpu.available;
  let h = selectionBar();
  if (S.tab === 'overview') {
    h += `<div class="toolbar"><label><input type="checkbox" data-opt="children" ${S.children ? 'checked' : ''}> Include child processes</label>
      <label><input type="checkbox" data-opt="normalize" ${S.normalize ? 'checked' : ''}> Normalise CPU by cores</label>
      <span style="flex:1"></span>${selLegend()}</div>
      <div class="grid g2">
        <div class="card"><div class="card-h"><h3>CPU</h3><span class="sub">${cpuUnit()} · last ${HIST} samples</span></div><div class="chart" id="c-cpu"></div></div>
        ${gpuOk ? '<div class="card"><div class="card-h"><h3>GPU</h3><span class="sub">utilisation</span></div><div class="chart" id="c-gpu"></div></div>'
          : `<div class="card"><div class="card-h"><h3>GPU</h3></div><div class="note" style="margin-top:8px">GPU metrics unavailable. ${esc(S.meta && S.meta.gpu ? S.meta.gpu.reason : '')}</div></div>`}
        <div class="card"><div class="card-h"><h3>Memory</h3><span class="sub">resident set size${S.sel.some(e => e.kind === 'app') ? ' (sum for apps)' : ''}</span></div><div class="chart" id="c-mem"></div></div>
        <div class="card"><div class="card-h"><h3>Disk I/O</h3><span class="sub">solid = read, dashed = write</span></div><div class="chart" id="c-io"></div></div>
      </div>
      <div class="grid g2" id="statcards"></div>`;
  } else if (S.tab === 'cpu') {
    const supported = S.meta ? S.meta.percore_supported : true;
    const overlap = S.sel.some(e => e.kind === 'pid' && S.sel.some(a => a.kind === 'app' && S.byPid.get(e.id) && S.byPid.get(e.id).app === a.id));
    h += `<div class="toolbar">
      <div class="seg">${[['bars', 'Bars'], ['heatmap', 'Heatmap'], ['3d', '3D']].map(([k, l]) => `<button class="${k === S.cpuView ? 'on' : ''}" data-cpuview="${k}">${l}</button>`).join('')}</div>
      <span class="faint" id="cpuinfo"></span><span style="flex:1"></span>
      ${S.cpuView === 'heatmap' ? '<span class="ramp">0% <b></b> 100%</span>' : legend([...S.sel.map(e => [label(e), slotColor(e.slot)]), ...(S.cpuView === 'bars' ? [['whole system', '', 'ghost']] : [])])}
      ${S.cpuView === '3d' ? `<button class="btn ${S.autoRotate ? 'primary' : ''}" data-act="rotate">⟲ Auto-rotate</button><button class="btn" data-act="resetview">Reset view</button>` : ''}
    </div>
    ${supported ? '' : '<div class="note">Per-process usage per core is available on Linux only. Showing whole-system usage per core.</div>'}
    ${overlap ? '<div class="note">A selected process is also part of a selected app, so its CPU time is counted in both.</div>' : ''}
    ${S.cpuView === 'heatmap'
      ? S.sel.map(e => `<div class="card"><div class="card-h"><h3><span style="color:${slotColor(e.slot)}">${e.kind === 'app' ? '■' : '●'}</span> ${esc(label(e))}</h3>
          <span class="sub">usage per core over the last ${HIST} samples</span></div>
          <div class="chart" id="c-heat${e.slot}" style="height:${Math.max(150, Math.min(420, cores() * 16 + 50))}px"></div></div>`).join('')
      : `<div class="card"><div class="card-h"><h3>${S.cpuView === 'bars' ? 'Usage per logical core — now' : '3D · core × time × usage'}</h3>
          <span class="sub">${S.cpuView === '3d' ? 'stacked by selection · drag to rotate · scroll to zoom' : 'stacked by selection · thread CPU time credited to the core it last ran on'}</span></div>
          <div class="chart ${S.cpuView === '3d' ? 'xl' : 'tall'}" id="c-core"></div></div>`}
    <div class="card"><div class="card-h"><h3>Cores</h3><span class="sub" id="coresub"></span></div><div class="cores" id="cores"></div></div>`;
  } else if (S.tab === 'gpu') {
    if (!gpuOk) {
      h += `<div class="card"><div class="card-h"><h3>GPU metrics unavailable</h3></div>
        <p class="muted" style="margin:0 0 8px">${esc(S.meta && S.meta.gpu ? S.meta.gpu.reason : 'No GPU detected.')}</p>
        <p class="faint" style="margin:0">NVIDIA: install the driver and <span class="mono">pip install -r requirements-gpu.txt</span>. AMD: install ROCm with the <span class="mono">amdsmi</span> Python package. In Docker, run with the NVIDIA Container Toolkit (<span class="mono">--gpus all</span>).</p></div>`;
    } else {
      const devs = (S.sys && S.sys.gpus) || [];
      h += `<div class="toolbar"><div class="seg"><button class="${S.gpuView === '2d' ? 'on' : ''}" data-gpuview="2d">2D</button><button class="${S.gpuView === '3d' ? 'on' : ''}" data-gpuview="3d">3D</button></div>
        <span class="faint">${esc(S.meta.gpu.backend)}${S.meta.gpu.driver ? ' · driver ' + esc(S.meta.gpu.driver) : ''}</span><span style="flex:1"></span>${selLegend()}</div>
        ${devs.map((g, i) => `<div class="grid g-gpu">
          <div class="card gpu-dev"><div class="card-h"><h3>GPU${g.index} · ${esc(g.name)}</h3></div>
            <div class="chart" id="c-gauge${i}" style="height:150px"></div><div id="gpurows${i}"></div></div>
          <div class="card"><div class="card-h"><h3>${S.gpuView === '3d' ? '3D · engine × time × utilisation' : 'Engine utilisation'}</h3><span class="sub">SM · memory controller · encoder · decoder</span></div>
            <div class="chart" id="c-eng${i}" style="height:290px"></div></div></div>`).join('')}
        <div class="grid g2">
          <div class="card"><div class="card-h"><h3>GPU memory by selection</h3><span class="sub">stacked</span></div><div class="chart" id="c-gmem"></div></div>
          <div class="card"><div class="card-h"><h3>GPU utilisation by selection</h3><span class="sub">now</span></div><div class="chart" id="c-gutil"></div></div>
        </div>`;
    }
  } else {
    h += '<div id="details"></div>';
  }
  main.innerHTML = h;
  if (S.tab === 'details') loadDetails();
  updateMain();
}

function updateMain() {
  if (!S.sel.length) return;
  const t = S.times;
  if (S.tab === 'overview') {
    const cpuS = [], memS = [], ioS = [], gpuS = [];
    S.sel.forEach((e, i) => {
      const c = slotColor(e.slot), nm = label(e);
      cpuS.push({name: nm, data: e.h.cpu.map(cpuVal), color: c, area: i === 0});
      memS.push({name: nm, data: e.h.rss, color: c, area: i === 0});
      gpuS.push({name: nm, data: e.h.gpu, color: c, area: i === 0});
      ioS.push({name: nm + ' read', data: e.h.ior, color: c}, {name: nm + ' write', data: e.h.iow, color: c, dashed: true});
    });
    setChart('c-cpu', R.line(C, t, cpuS, {unit: '%'}), true);
    setChart('c-gpu', R.line(C, t, gpuS, {unit: '%', max: 100}), true);
    setChart('c-mem', R.line(C, t, memS, {fmt: v => fmtBytes(v)}), true);
    setChart('c-io', R.line(C, t, ioS, {fmt: v => fmtRate(v)}), true);
    const sc = $('#statcards');
    if (sc) sc.innerHTML = S.sel.map(e => {
      const d = e.last || {};
      return `<div class="card ${e.gone ? 'exited' : ''}"><div class="card-h"><h3><span style="color:${slotColor(e.slot)}">${e.kind === 'app' ? '■' : '●'}</span> ${esc(e.name)} <span class="faint mono">${e.kind === 'app' ? '×' + (d.count ?? '…') : e.id}</span></h3>
        ${e.gone ? `<span class="badge gone">EXITED ${e.goneAt ? e.goneAt.toLocaleTimeString() : ''}</span>` : `<span class="badge ok">${esc(e.kind === 'app' ? 'running' : d.status || '…')}</span>`}</div>
        <div class="stats">
          <div class="stat"><div class="k">CPU</div><div class="v num">${fmtPct(cpuVal(d.cpu))}</div></div>
          <div class="stat"><div class="k">Memory</div><div class="v num">${fmtBytes(d.rss)}</div></div>
          <div class="stat"><div class="k">GPU</div><div class="v num">${fmtPct(d.gpu, 0)}</div></div>
          <div class="stat"><div class="k">Threads</div><div class="v num">${d.threads ?? '–'}</div></div>
        </div><div class="faint mono ellipsis" style="margin-top:6px">${esc(e.kind === 'app' ? `${d.count ?? 0} processes · ${(d.users || []).join(', ')}` : d.cmd || '')}</div>
        ${e.gone ? `<button class="btn" style="margin-top:8px" data-removeid="${esc(entKey(e.kind, e.id))}">Remove</button>` : ''}</div>`;
    }).join('');
  } else if (S.tab === 'cpu') {
    updateCpuTab();
  } else if (S.tab === 'gpu') {
    updateGpuTab();
  } else if (S.tab === 'details') {
    renderDetails();
  }
}

function coreRows(e, n) {
  const rows = Array.from({length: n}, () => []);
  e.h.cores.forEach(arr => { for (let c = 0; c < n; c++) rows[c].push(arr ? arr[c] : null); });
  return rows;
}

function updateCpuTab() {
  if (!S.sel.length || !S.sys) return;
  const sysCores = S.sys.per_core, n = sysCores.length;
  const zero = sysCores.map(() => 0);
  const now = e => (e.last && e.last.per_core) || zero;
  const info = $('#cpuinfo');
  const threads = S.sel.reduce((s, e) => s + ((e.last && e.last.threads) || 0), 0);
  if (info) info.textContent = `${S.sel.length} selected · ${threads} thread${threads === 1 ? '' : 's'} on ${n} logical cores`;
  if (S.cpuView === 'bars') {
    setChart('c-core', R.coreBars(C, sysCores, S.sel.map(e => ({name: label(e), color: slotColor(e.slot), values: now(e)}))), true);
  } else if (S.cpuView === 'heatmap') {
    S.sel.forEach(e => setChart('c-heat' + e.slot, R.heat(C, S.times, coreRows(e, n)), true));
  } else {
    setChart('c-core', R.bars3d(C, S.times, S.sel.map(e => ({name: label(e), color: slotColor(e.slot), rows: coreRows(e, n)})),
      {autoRotate: S.autoRotate}), false);
  }
  const rows = S.sel.map(e => coreRows(e, n));
  const sub = $('#coresub');
  if (sub) sub.textContent = `share of each core taken by the selection · one line per item, last ${HIST} samples`;
  const el = $('#cores');
  if (el) el.innerHTML = sysCores.map((sv, i) => {
    const parts = S.sel.map(e => ({v: now(e)[i] || 0, c: slotColor(e.slot)}));
    const tot = parts.reduce((s, p) => s + p.v, 0);
    const lead = parts.reduce((a, p) => p.v > a.v ? p : a, {v: -1, c: C.s[0]});
    const seg = parts.filter(p => p.v > 0).map(p => `<i style="width:${Math.min(100, p.v)}%;background:${p.c}"></i>`).join('');
    return `<div class="core ${tot > 50 ? 'hot' : ''}" style="--c:${lead.c}" title="${esc(S.sel.map((e, k) => `${label(e)}: ${parts[k].v.toFixed(1)}%`).join('\n'))}">
      <div class="t"><span>C${i}</span><span>sys ${Math.round(sv)}%</span></div>
      <div class="v num">${tot.toFixed(tot < 10 ? 1 : 0)}%</div>
      <div class="segbar">${seg}</div>
      <div class="spark">${R.sparkMulti(rows.map((r, k) => ({values: r[i], color: slotColor(S.sel[k].slot)})))}</div></div>`;
  }).join('');
}

function updateGpuTab() {
  const devs = (S.sys && S.sys.gpus) || [];
  if (!(S.meta && S.meta.gpu && S.meta.gpu.available)) return;
  devs.forEach((g, i) => {
    setChart('c-gauge' + i, R.gauge(C, g.util, 'utilisation'));
    const rows = $('#gpurows' + i);
    if (rows) rows.innerHTML = `<div class="rows"><span class="muted">Memory</span><span class="num">${fmtBytes(g.mem_used)} / ${fmtBytes(g.mem_total)}</span></div>
      <div class="meterbar"><i style="width:${g.mem_total ? g.mem_used / g.mem_total * 100 : 0}%"></i></div>
      <div class="rows"><span class="muted">Temperature</span><span class="num">${g.temp != null ? g.temp + ' °C' : '–'}</span>
      <span class="muted">Power</span><span class="num">${g.power != null ? Math.round(g.power) + (g.power_limit ? ' / ' + Math.round(g.power_limit) : '') + ' W' : '–'}</span>
      <span class="muted">Clock</span><span class="num">${g.clock != null ? g.clock + ' MHz' : '–'}</span>
      <span class="muted">Fan</span><span class="num">${g.fan != null ? g.fan + '%' : '–'}</span></div>`;
    const h = S.gpuHist[i];
    if (h) setChart('c-eng' + i, S.gpuView === '3d' ? R.surface3d(C, S.times, h) : R.lanes(C, S.times, h, C.s[0]), S.gpuView !== '3d');
  });
  setChart('c-gmem', R.line(C, S.times, S.sel.map(e => ({name: label(e), data: e.h.gmem, color: slotColor(e.slot), stack: 'm', area: true})),
    {fmt: v => fmtBytes(v)}), true);
  setChart('c-gutil', R.hbars(C, S.sel.map(e => ({label: label(e), value: (e.last && e.last.gpu) || 0, color: slotColor(e.slot)}))), true);
}

/* ------------------------------------------------------------ details */
function loadDetails() {
  for (const e of S.sel) {
    if (e.kind === 'pid' && !S.details[e.id]) {
      S.details[e.id] = {};
      api(`api/process/${e.id}`).then(d => { S.details[e.id] = d; renderDetails(); }).catch(() => { S.details[e.id] = {missing: true}; });
    }
  }
  renderDetails();
}

function appHead(e) {
  const d = e.last || {}, col = slotColor(e.slot);
  return `<div class="card ${e.gone ? 'exited' : ''}"><div class="card-h"><h3><span class="avatar" style="background:${col};color:#fff">${esc((e.name[0] || '?').toUpperCase())}</span>${esc(e.name)} <span class="faint">· app · ${d.count ?? 0} processes</span></h3>${e.gone ? '<span class="badge gone">EXITED</span>' : '<span class="badge ok">running</span>'}</div>
    <div class="stats stats6">
      <div class="stat"><div class="k">CPU</div><div class="v num">${fmtPct(cpuVal(d.cpu))}</div><div class="d">${fmtPct(d.cpu == null ? null : d.cpu / cores())} of all cores</div></div>
      <div class="stat"><div class="k">Memory</div><div class="v num">${fmtBytes(d.rss)}</div><div class="d">${fmtPct(d.mem)} of RAM (sum of RSS)</div></div>
      <div class="stat"><div class="k">GPU</div><div class="v num">${fmtPct(d.gpu, 0)}</div><div class="d">${d.gmem ? fmtBytes(d.gmem) + ' VRAM' : '–'}</div></div>
      <div class="stat"><div class="k">Processes</div><div class="v num">${d.count ?? 0}</div><div class="d">${esc((d.users || []).join(', '))}</div></div>
      <div class="stat"><div class="k">Threads</div><div class="v num">${d.threads ?? '–'}</div><div class="d">across members</div></div>
      <div class="stat"><div class="k">Disk I/O</div><div class="v num">${fmtRate((d.ior || 0) + (d.iow || 0))}</div><div class="d">read ${fmtRate(d.ior)} · write ${fmtRate(d.iow)}</div></div>
    </div><div class="faint" style="margin-top:8px">Oldest member started ${fmtAge(d.started)} ago${e.gone ? ` · <button class="btn" data-removeid="${esc(entKey(e.kind, e.id))}">Remove</button>` : ''}</div></div>`;
}

function pidCard(e) {
  const d = e.last || {}, col = slotColor(e.slot), x = S.details[e.id] || {};
  const kv = [['PID', e.id], ['App', d.app || x.app || '–'], ['User', d.user || x.username || '–'], ['Status', d.status || x.status || '–'],
    ['Uptime', fmtAge(d.started || x.started)], ['Parent PID', d.ppid ?? x.ppid ?? '–'], ['Threads', d.threads ?? x.num_threads ?? '–'],
    ['CPU', fmtPct(cpuVal(d.cpu))], ['Memory', fmtBytes(d.rss)], ['Open files', x.num_fds ?? '–'], ['Nice', x.nice ?? '–'],
    ['Executable', x.exe || d.exe || '–'], ['Working dir', x.cwd || '–'], ['Command', x.cmdline ? x.cmdline.join(' ') : d.cmd || '–']];
  return `<div class="card ${e.gone ? 'exited' : ''}"><div class="card-h"><h3><span style="color:${col}">●</span> ${esc(e.name)} <span class="faint mono">${e.id}</span></h3>
    ${e.gone ? `<span class="badge gone">EXITED ${e.goneAt ? e.goneAt.toLocaleTimeString() : ''}</span>` : `<span class="badge ok">${esc(d.status || 'running')}</span>`}</div>
    <dl class="kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd class="${['Executable', 'Working dir', 'Command'].includes(k) ? 'mono' : ''}">${esc(v)}</dd>`).join('')}</dl>
    ${d.denied ? '<p class="faint" style="margin:8px 0 0">🔒 Some fields are hidden: this process belongs to another user. Run res-watch with more privileges to see them.</p>' : ''}
    ${e.gone ? `<button class="btn" style="margin-top:10px" data-removeid="${esc(entKey(e.kind, e.id))}">Remove</button>` : ''}</div>`;
}

function renderDetails() {
  const box = $('#details');
  if (!box) return;
  const sig = S.sel.map(e => entKey(e.kind, e.id)).join('|');
  if (box.dataset.for !== sig) {        // build the layout once per selection; ticks only refresh contents
    box.dataset.for = sig;
    const apps = S.sel.filter(e => e.kind === 'app'), pids = S.sel.filter(e => e.kind === 'pid');
    box.innerHTML = apps.map(e => `<section class="dsec"><div id="dhead${e.slot}"></div>
        <div class="grid g2">
          <div class="card"><div class="card-h"><h3>CPU by member process</h3><span class="sub">${cpuUnit()} · now</span></div><div class="chart" id="c-mcpu${e.slot}" style="height:280px"></div></div>
          <div class="card"><div class="card-h"><h3>Memory by member process</h3><span class="sub">RSS · treemap</span></div><div class="chart" id="c-mtree${e.slot}" style="height:280px"></div></div>
        </div></section>`).join('')
      + (pids.length ? `<div class="grid g2">${pids.map(e => `<div id="dpid${e.slot}"></div>`).join('')}</div>` : '');
  }
  for (const e of S.sel) {
    if (e.kind === 'pid') {
      const el = $('#dpid' + e.slot);
      if (el) el.innerHTML = pidCard(e);
      continue;
    }
    const head = $('#dhead' + e.slot);
    if (head) head.innerHTML = appHead(e);
    const d = e.last || {}, col = slotColor(e.slot), mem = d.members || [];
    const items = mem.slice(0, 8).map(m => ({label: `${m.name} ${m.pid}`, value: +(cpuVal(m.cpu) || 0).toFixed(2), color: col}));
    if (mem.length > 8) items.push({label: `${mem.length - 8} others`, value: +mem.slice(8).reduce((s, m) => s + (cpuVal(m.cpu) || 0), 0).toFixed(2), color: C.text3});
    setChart('c-mcpu' + e.slot, R.hbars(C, items), true);
    const byMem = mem.slice().sort((a, b) => (b.rss || 0) - (a.rss || 0));
    const tm = byMem.slice(0, 10).map(m => ({label: `${m.name} ${m.pid}`, value: m.rss || 0}));
    if (byMem.length > 10) tm.push({label: `${byMem.length - 10} others`, value: byMem.slice(10).reduce((s, m) => s + (m.rss || 0), 0), other: true});
    setChart('c-mtree' + e.slot, R.treemap(C, tm, col, v => fmtBytes(v)), true);
  }
}

/* ------------------------------------------------------------- events */
function toast(msg) {
  let t = $('#toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2600);
}
function setMView(v) {
  $('#body').dataset.mview = v;
  document.querySelectorAll('#mtabs button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  if (v === 'charts') setTimeout(() => { for (const k in charts) charts[k].resize(); }, 0);
}

function bindEvents() {
  $('#sidemode').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    S.side = b.dataset.mode; LS.set('side', S.side);
    if (S.side === 'apps' && S.sort.col === 'pid') S.sort = {col: 'cpu', dir: -1};
    renderSidebar();
  });
  let st;
  $('#search').addEventListener('input', e => { clearTimeout(st); st = setTimeout(() => { S.search = e.target.value.trim().toLowerCase(); renderSidebar(); }, 120); });
  $('#fbtn').addEventListener('click', () => { S.popover = !S.popover; renderSidebar(); });
  $('#fchips').addEventListener('click', e => {
    const k = e.target.dataset.clear; if (!k) return;
    const f = S.filters;
    if (k === 'all') S.filters = JSON.parse(JSON.stringify(CLEARED_FILTERS));
    else if (k === 'user') f.user = 'all';
    else if (k === 'hideSystem') f.hideSystem = false;
    else if (k === 'gpuOnly') f.gpuOnly = false;
    else if (k === 'minCpu') f.minCpu = 0;
    else if (k === 'status') f.status = {running: true, sleeping: true, zombie: true, other: true};
    saveFilters(); renderSidebar();
  });
  const pop = $('#popover');
  pop.addEventListener('input', e => {
    const f = S.filters, t = e.target;
    if (t.name === 'fuser') f.user = t.value;
    else if (t.id === 'f-hide') f.hideSystem = t.checked;
    else if (t.id === 'f-gpu') f.gpuOnly = t.checked;
    else if (t.id === 'f-min') { f.minCpu = +t.value; $('#f-minv').textContent = t.value + '%'; }
    else if (t.dataset.status) f.status[t.dataset.status] = t.checked;
    saveFilters();
    // re-render the list and chips but keep the popover (and slider focus) intact
    const keep = S.popover; S.popover = false; renderSidebar(); S.popover = keep;
    pop.classList.toggle('hidden', !keep); $('#fbtn').classList.toggle('on', keep);
  });
  pop.addEventListener('click', e => {
    const a = e.target.dataset.pop; if (!a) return;
    if (a === 'reset') S.filters = JSON.parse(JSON.stringify(DEFAULT_FILTERS));
    if (a === 'done') S.popover = false;
    saveFilters(); renderSidebar();
  });
  document.addEventListener('click', e => {
    if (S.popover && !e.target.closest('#popover') && !e.target.closest('#fbtn')) { S.popover = false; renderSidebar(); }
  });

  $('#ptable').addEventListener('click', e => {
    const s = e.target.closest('[data-sort]');
    if (s) {
      const col = s.dataset.sort;
      S.sort = S.sort.col === col ? {col, dir: -S.sort.dir} : {col, dir: ['name', 'app', 'pid'].includes(col) ? 1 : -1};
      LS.set('sort', S.sort); renderSidebar(); return;
    }
    const tw = e.target.closest('[data-twist]');
    if (tw) {
      const k = tw.dataset.twist;
      S.expanded.has(k) ? S.expanded.delete(k) : S.expanded.add(k);
      LS.set('expanded', [...S.expanded].slice(-50)); renderSidebar(); return;
    }
    const more = e.target.closest('[data-more]');
    if (more) { S.showAll.add(more.dataset.more); renderSidebar(); return; }
    const row = e.target.closest('tr[data-kind]');
    if (row) toggleSel(row.dataset.kind, row.dataset.id);
  });

  $('#main').addEventListener('click', e => {
    const t = e.target;
    const rm = t.closest('[data-remove]');
    if (rm) { const x = S.sel[+rm.dataset.remove]; removeSel(x.kind, x.id); selectionChanged(); return; }
    const rid = t.closest('[data-removeid]');
    if (rid) { const [k, ...rest] = rid.dataset.removeid.split(':'); removeSel(k, rest.join(':')); selectionChanged(); return; }
    const tab = t.closest('[data-tab]');
    if (tab) { S.tab = tab.dataset.tab; LS.set('tab', S.tab); renderMain(); return; }
    const cv = t.closest('[data-cpuview]');
    if (cv) { S.cpuView = cv.dataset.cpuview; LS.set('cpuView', S.cpuView); renderMain(); return; }
    const gv = t.closest('[data-gpuview]');
    if (gv) { S.gpuView = gv.dataset.gpuview; LS.set('gpuView', S.gpuView); renderMain(); return; }
    const act = t.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a === 'clear') { S.sel = []; selectionChanged(); }
      else if (a === 'rotate') { S.autoRotate = !S.autoRotate; renderMain(); }
      else if (a === 'resetview') { renderMain(); }
    }
  });
  $('#main').addEventListener('change', e => {
    const o = e.target.dataset.opt; if (!o) return;
    S[o] = e.target.checked; LS.set(o, S[o]);
    if (o === 'children') { subscribe(); }
    renderMain();
  });

  const every = $('#every');
  every.addEventListener('change', () => { S.every = +every.value; LS.set('every', S.every); subscribe(); });
  $('#pause').addEventListener('click', () => {
    S.paused = !S.paused;
    $('#pause').textContent = S.paused ? '▶ Resume' : '⏸ Pause';
    $('#pause').classList.toggle('primary', S.paused);
  });
  $('#theme').addEventListener('click', () => {
    S.theme = {auto: 'light', light: 'dark', dark: 'auto'}[S.theme] || 'auto';
    LS.set('theme', S.theme); applyTheme(); renderMain(); renderTopbar();
  });
  $('#logout').addEventListener('click', () => {
    fetch('api/logout', {method: 'POST', credentials: 'same-origin'}).finally(() => { location.href = 'login'; });
  });
  $('#mtabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setMView(b.dataset.v); });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (S.theme === 'auto') renderMain(); });
}
function applyTheme() {
  if (S.theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = S.theme;
  $('#theme').textContent = 'Theme: ' + {auto: 'auto', light: 'light', dark: 'dark'}[S.theme];
}

/* --------------------------------------------------------------- boot */
function refreshProcs() {
  return api('api/processes').then(list => {
    S.procs = list;
    S.byPid = new Map(list.map(p => [p.pid, p]));
    renderSidebar();
  }).catch(() => { /* retried on the next interval */ });
}

async function boot() {
  applyTheme();
  bindEvents();
  setMView(innerWidth <= 768 ? 'list' : 'charts');
  try {
    const me = await api('api/me');
    if (me.auth) $('#logout').classList.remove('hidden');
    S.meta = await api('api/meta');
  } catch (e) { return; }
  $('#host').textContent = S.meta.hostname ? '· ' + S.meta.hostname : '';
  document.title = `res-watch · ${S.meta.hostname}`;
  const ev = $('#every');
  ev.innerHTML = [1, 2, 5].map(n => `<option value="${n}" ${n === S.every ? 'selected' : ''}>${+(n * S.meta.interval).toFixed(1)} s</option>`).join('');
  S.sys = await api('api/system').catch(() => null);
  renderTopbar();
  await refreshProcs();
  renderMain();
  connect();
  setInterval(refreshProcs, 3000);
}
boot();
