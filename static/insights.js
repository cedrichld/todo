// Insights: a year of activity (heatmap + charts) and a slider that shows the list as it was on any past day.
// Loaded before app.js; only touches its globals at call time.
const INS = { range: 90, day: null, data: null, past: null, table: new Set() };
const RAMP = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-5)'];
const NS = 'http://www.w3.org/2000/svg';
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'class') el.className = v; else if (k.startsWith('on')) el[k] = v; else if (k === 'text') el.textContent = v; else el.setAttribute(k, v); }
  for (const k of kids.flat()) if (k != null) el.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  return el;
}
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'text') el.textContent = v; else if (k.startsWith('on')) el[k] = v; else el.setAttribute(k, v); }
  for (const k of kids.flat()) if (k != null) el.appendChild(k);
  return el;
}
const nice = n => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K' : String(n);
const longDay = iso => { const d = parseISO(iso); return `${DAY[d.getDay()]}, ${MON[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; };
const shortDay = iso => { const d = parseISO(iso); return `${MON[d.getMonth()]} ${d.getDate()}`; };
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const sum = a => a.reduce((x, y) => x + y, 0);

// ---- tooltip (one for the whole page; values lead, labels follow)
function tip(evt, rows) {
  const t = $('#tip'); t.textContent = '';
  for (const [v, l, color] of rows) {
    const r = h('div', { class: 'tip-row' });
    if (color) r.appendChild(h('i', { style: `background:${color}` }));
    r.appendChild(h('b', { text: v })); if (l) r.appendChild(h('span', { text: l }));
    t.appendChild(r);
  }
  t.hidden = false;
  const x = Math.min(evt.clientX + 14, innerWidth - t.offsetWidth - 8), y = evt.clientY - t.offsetHeight - 12;
  t.style.left = x + 'px'; t.style.top = (y < 8 ? evt.clientY + 18 : y) + 'px';
}
function hideTip() { $('#tip').hidden = true; }

// ---- pieces
function card(title, sub, body, table) {
  const c = h('section', { class: 'viz' }, h('header', {}, h('h3', { text: title }), sub ? h('p', { text: sub }) : null));
  c.appendChild(body);
  if (table) {
    const key = title, btn = h('button', { class: 'viz-table-btn', text: INS.table.has(key) ? 'Hide table' : 'Table' });
    const wrap = h('div', { class: 'viz-table' }); wrap.hidden = !INS.table.has(key);
    const [head, rows] = table;
    wrap.appendChild(h('table', {}, h('thead', {}, h('tr', {}, head.map(x => h('th', { text: x })))), h('tbody', {}, rows.map(r => h('tr', {}, r.map(x => h('td', { text: String(x) })))))));
    btn.onclick = () => { wrap.hidden = !wrap.hidden; INS.table[wrap.hidden ? 'delete' : 'add'](key); btn.textContent = wrap.hidden ? 'Table' : 'Hide table'; };
    c.appendChild(h('footer', {}, btn)); c.appendChild(wrap);
  }
  return c;
}
function statRow(d) {
  const T = d.totals, prev7 = sum(d.done.slice(-14, -7)), delta = T.done_7 - prev7;
  const weeks = []; for (let i = 12; i >= 1; i--) weeks.push(sum(d.done.slice(d.done.length - 7 * i, d.done.length - 7 * (i - 1))));
  const spark = sv('svg', { viewBox: '0 0 96 26', class: 'spark' });
  const mx = Math.max(1, ...weeks);
  weeks.forEach((v, i) => spark.appendChild(sv('rect', { x: i * 8, y: 26 - Math.max(2, v / mx * 24), width: 6, height: Math.max(2, v / mx * 24), rx: 1.5, class: i === weeks.length - 1 ? 'now' : '' })));
  const tile = (label, value, note, extra) => h('div', { class: 'stat' }, h('span', { class: 'stat-label', text: label }), h('strong', { text: value }), note ? h('span', { class: 'stat-note', text: note }) : null, extra);
  return h('div', { class: 'stats' },
    tile('Done this week', nice(T.done_7), `${delta >= 0 ? '+' : '−'}${Math.abs(delta)} vs the week before`, spark),
    tile('Open now', nice(T.open), `${plural(T.overdue, 'overdue')} · ${T.waiting} waiting`),
    tile('Streak', plural(d.streak, 'day'), `best ${plural(d.best_streak, 'day')}`),
    tile('Done this year', nice(T.done_365), `${T.created_30} added in the last 30 days`));
}
function heatmap(d) {
  const cell = 11, gap = 2, step = cell + gap, first = parseISO(d.days[0]), lead = (first.getDay() + 6) % 7;  // Monday-first rows
  const cols = Math.ceil((lead + d.days.length) / 7), W = cols * step + 30, H = 7 * step + 18;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'heat', width: W, height: H });
  const mx = Math.max(1, ...d.done);
  let lastMonth = -1, lastLabelCol = -9;
  d.days.forEach((day, i) => {
    const k = lead + i, col = Math.floor(k / 7), row = k % 7, v = d.done[i], dt = parseISO(day);
    const level = v === 0 ? 0 : Math.max(1, Math.ceil(v / mx * 4));
    const r = sv('rect', { x: 30 + col * step, y: 16 + row * step, width: cell, height: cell, rx: 2.5, class: 'cell' + (day === INS.day ? ' picked' : ''), fill: level ? RAMP[level] : 'var(--chip)' });
    r.onpointerenter = e => tip(e, [[plural(v, 'task'), 'done · ' + longDay(day)]]); r.onpointerleave = hideTip;
    r.onclick = () => pickDay(day);
    svg.appendChild(r);
    if (dt.getMonth() !== lastMonth) {
      lastMonth = dt.getMonth();
      if (col - lastLabelCol >= 3 && col * step + 30 < W - 24) { svg.appendChild(sv('text', { x: 30 + col * step, y: 10, class: 'ax', text: MON[dt.getMonth()] })); lastLabelCol = col; }
    }
  });
  [['Mon', 0], ['Wed', 2], ['Fri', 4]].forEach(([l, r]) => svg.appendChild(sv('text', { x: 0, y: 16 + r * step + 9, class: 'ax', text: l })));
  const legend = h('div', { class: 'heat-legend' }, h('span', { text: 'Less' }), ...RAMP.slice(1).map(c => h('i', { style: `background:${c}` })), h('span', { text: 'More' }), h('span', { class: 'hint', text: '· click a day to see the list as it was' }));
  return h('div', { class: 'heat-wrap' }, svg, legend);
}
function rangeRow() {
  return h('div', { class: 'view-switch viz-range' }, ...[[30, '30 days'], [90, '90 days'], [365, 'Year']].map(([n, l]) => h('button', { class: INS.range === n ? 'on' : '', text: l, onclick: () => { INS.range = n; renderInsights($('#view')); } })));
}
function axisTicks(max, n = 3) {  // clean steps (1/2/5 × 10^k) giving at least two gridlines and at most ~n+1
  const mag = 10 ** Math.floor(Math.log10(Math.max(1, max))), cands = [10, 5, 2, 1, .5, .2, .1].map(x => x * mag);
  const s = cands.find(c => Math.floor(max / c) >= Math.max(2, n - 1)) || cands.at(-1);
  const out = []; for (let v = s; v <= max; v += s) out.push(+v.toFixed(6)); return { step: s, ticks: out };
}
function lineChart(days, values, W, color, label) {
  const H = 170, L = 34, R = 14, T = 14, B = 26, pw = W - L - R, ph = H - T - B, max = Math.max(1, ...values) * 1.15;
  const x = i => L + (values.length === 1 ? pw / 2 : i / (values.length - 1) * pw), y = v => T + ph - v / max * ph;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', width: W, height: H });
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  svg.appendChild(sv('path', { d: `M${pts.join('L')}L${x(values.length - 1)},${y(0)}L${x(0)},${y(0)}Z`, fill: color, opacity: .08 }));
  const { ticks } = axisTicks(max);
  for (const v of ticks) { svg.appendChild(sv('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: 'grid' })); svg.appendChild(sv('text', { x: L - 6, y: y(v) + 3.5, class: 'ax r', text: nice(v) })); }
  svg.appendChild(sv('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'axis' }));
  svg.appendChild(sv('path', { d: `M${pts.join('L')}`, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  const n = values.length, every = Math.max(1, Math.round(n / 5));
  for (let i = 0; i < n; i += every) svg.appendChild(sv('text', { x: x(i), y: H - 8, class: 'ax c', text: shortDay(days[i]) }));
  const last = n - 1;
  svg.appendChild(sv('circle', { cx: x(last), cy: y(values[last]), r: 5.5, fill: 'var(--paper)' }));
  svg.appendChild(sv('circle', { cx: x(last), cy: y(values[last]), r: 4, fill: color }));
  svg.appendChild(sv('text', { x: x(last) - 8, y: y(values[last]) - 9, class: 'val r', text: nice(values[last]) }));
  const cross = sv('line', { class: 'cross', y1: T, y2: y(0), visibility: 'hidden' }), dot = sv('circle', { r: 4, fill: color, visibility: 'hidden' }), ring = sv('circle', { r: 5.5, fill: 'var(--paper)', visibility: 'hidden' });
  svg.appendChild(cross); svg.appendChild(ring); svg.appendChild(dot);
  const hit = sv('rect', { x: L, y: T, width: pw, height: ph + B, fill: 'transparent' });
  hit.onpointermove = e => { const r = svg.getBoundingClientRect(), i = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left - L) / pw)) * (n - 1)); cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(values[i])); ring.setAttribute('cx', x(i)); ring.setAttribute('cy', y(values[i])); [cross, dot, ring].forEach(el => el.setAttribute('visibility', 'visible')); tip(e, [[nice(values[i]), `${label} · ${longDay(days[i])}`, color]]); };
  hit.onpointerleave = () => { [cross, dot, ring].forEach(el => el.setAttribute('visibility', 'hidden')); hideTip(); };
  svg.appendChild(hit);
  return svg;
}
function columns(labels, values, W, color, tipLabel, opts = {}) {
  const H = opts.height || 150, L = 34, R = 10, T = 12, B = 24, pw = W - L - R, ph = H - T - B, max = Math.max(1, ...values) * 1.12;
  const n = values.length, slot = pw / n, bw = Math.min(24, Math.max(2, slot - 2)), y = v => T + ph - v / max * ph;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', width: W, height: H });
  const { ticks } = axisTicks(max, 2);
  for (const v of ticks) { svg.appendChild(sv('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: 'grid' })); svg.appendChild(sv('text', { x: L - 6, y: y(v) + 3.5, class: 'ax r', text: nice(v) })); }
  svg.appendChild(sv('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'axis' }));
  values.forEach((v, i) => {
    const cx = L + slot * i + slot / 2, x0 = cx - bw / 2, top = y(v), hgt = y(0) - top, r = Math.min(4, bw / 2, hgt);
    const d = v > 0 ? `M${x0},${y(0)}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + bw - r}Q${x0 + bw},${top} ${x0 + bw},${top + r}V${y(0)}Z` : '';
    const bar = sv('path', { d, fill: color, class: 'bar' });
    const hit = sv('rect', { x: L + slot * i, y: T, width: slot, height: ph + B, fill: 'transparent' });
    hit.onpointerenter = e => { bar.classList.add('hot'); tip(e, [[nice(v), `${tipLabel} · ${opts.tipName ? opts.tipName(i) : labels[i]}`, color]]); };
    hit.onpointermove = e => tip(e, [[nice(v), `${tipLabel} · ${opts.tipName ? opts.tipName(i) : labels[i]}`, color]]);
    hit.onpointerleave = () => { bar.classList.remove('hot'); hideTip(); };
    svg.appendChild(bar); svg.appendChild(hit);
  });
  const every = opts.every || Math.max(1, Math.round(n / 6));
  labels.forEach((l, i) => { if (i % every === 0) svg.appendChild(sv('text', { x: L + slot * i + slot / 2, y: H - 8, class: 'ax c', text: l })); });
  if (opts.peak) { const i = values.indexOf(Math.max(...values)); if (values[i] > 0) svg.appendChild(sv('text', { x: L + slot * i + slot / 2, y: y(values[i]) - 5, class: 'val c', text: nice(values[i]) })); }
  return svg;
}
function hbars(rows, W, color) {
  const rh = 26, L = Math.min(180, W * .38), R = 44, H = rows.length * rh + 8, max = Math.max(1, ...rows.map(r => r.value)), pw = W - L - R;
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', width: W, height: H });
  rows.forEach((r, i) => {
    const y0 = 4 + i * rh + 4, w = r.value / max * pw, bh = 18, rr = Math.min(4, w);
    const lab = sv('text', { x: L - 10, y: y0 + 13, class: 'lab r', text: r.label.length > 26 ? r.label.slice(0, 25) + '…' : r.label });
    const d = w > 0 ? `M${L},${y0}H${L + w - rr}Q${L + w},${y0} ${L + w},${y0 + rr}V${y0 + bh - rr}Q${L + w},${y0 + bh} ${L + w - rr},${y0 + bh}H${L}Z` : '';
    const bar = sv('path', { d, fill: color, class: 'bar' });
    svg.appendChild(lab); svg.appendChild(bar);
    svg.appendChild(sv('text', { x: L + w + 6, y: y0 + 13, class: 'val', text: nice(r.value) + (r.open ? ` · ${r.open} open` : '') }));
    const hit = sv('rect', { x: 0, y: y0 - 4, width: W, height: rh, fill: 'transparent' });
    hit.onpointerenter = e => { bar.classList.add('hot'); tip(e, [[plural(r.value, 'task'), 'done · ' + r.label, color], [String(r.open), 'open now']]); };
    hit.onpointerleave = () => { bar.classList.remove('hot'); hideTip(); };
    svg.appendChild(hit);
  });
  return svg;
}
function weekly(days, values) {  // fold a daily series into calendar weeks (Monday start), keeping the week's first day as label
  const out = [], labs = []; let acc = 0, start = null;
  days.forEach((d, i) => { const dow = (parseISO(d).getDay() + 6) % 7; if (dow === 0 && start !== null) { out.push(acc); labs.push(start); acc = 0; start = null; } if (start === null) start = d; acc += values[i]; });
  if (start !== null) { out.push(acc); labs.push(start); }
  return [labs, out];
}

// ---- time travel
async function pickDay(day) {
  INS.day = day;
  $$('.heat .cell.picked').forEach(c => c.classList.remove('picked'));
  const slider = $('#past-slider'); if (slider) { const i = INS.data.days.indexOf(day); if (i >= 0) slider.value = i; }
  const box = $('#past'); if (!box) return;
  box.replaceChildren(h('p', { class: 'empty-state', text: 'Loading…' }));
  let snap;
  try { snap = await api.snapshot(day); } catch (e) { showError(e); return; }
  if (INS.day !== day || !$('#past')) return;
  renderPast($('#past'), day, snap);
  $$('.heat .cell').forEach(c => { /* mark the chosen cell */ });
}
function renderPast(box, day, snap) {
  box.replaceChildren();
  if (!snap.day) { box.appendChild(h('p', { class: 'empty-state', text: `Nothing saved for ${longDay(day)} yet — the list is remembered from the day it was first opened with this version.` })); return; }
  const past = new Map(snap.nodes.map(n => [n.id, n])), live = S.nodes;
  let doneSince = 0, gone = 0, added = 0, openThen = 0;
  for (const n of past.values()) { if (n.kind === 'task' && !n.done_at) openThen++; const l = live.get(n.id); if (!l) gone++; else if (n.kind === 'task' && !n.done_at && l.done_at) doneSince++; }
  for (const n of live.values()) if (!past.has(n.id) && n.kind === 'task') added++;
  const when = snap.taken_at ? ` (saved ${fmtTime(snap.taken_at)})` : '';
  box.appendChild(h('p', { class: 'past-summary' },
    h('b', { text: longDay(snap.day) }), snap.day !== day ? ` — the last save before ${shortDay(day)}` : '', when + ' · ',
    `${plural(openThen, 'open task')} then · since: `, h('mark', { class: 'done', text: `${doneSince} completed` }), ', ', h('mark', { class: 'added', text: `${added} added` }), ', ', h('mark', { class: 'gone', text: `${gone} removed` })));
  const saved = [S.nodes, S.kids];
  S.nodes = past; index();
  const tree = h('div', { class: 'past-tree filtered' });
  try { tree.appendChild(renderChildren(null, 0, null, true)); } finally { [S.nodes, S.kids] = saved; }
  for (const el of $$('.node', tree)) {
    const id = +el.dataset.id, n = past.get(id), l = live.get(id);
    $(':scope > .row > .text', el).contentEditable = 'false';
    const chk = $(':scope > .row > .check', el); if (chk) chk.disabled = true;
    const mark = !l ? ['gone', 'removed since'] : n.kind === 'task' && !n.done_at && l.done_at ? ['done', 'done since'] : null;
    if (mark) { el.classList.add('since-' + mark[0]); $(':scope > .row', el).insertBefore(h('span', { class: 'since ' + mark[0], text: mark[1] }), $(':scope > .row > .menu', el)); }
  }
  box.appendChild(tree);
}

// ---- the page
async function renderInsights(main) {
  const first = !INS.data;
  if (first) main.innerHTML = '<p class="empty-state">Counting…</p>'; else main.classList.add('stale');
  try { INS.data = await api.insights(); } catch (e) { showError(e); return; } finally { main.classList.remove('stale'); }
  if (S.view !== 'insights') return;
  const d = INS.data, W = Math.min(880, Math.max(320, main.clientWidth - 12)), half = Math.floor((W - 24) / 2);
  main.replaceChildren();
  main.appendChild(statRow(d));
  main.appendChild(card('A year of finished tasks', `${plural(d.totals.done_365, 'task')} completed in the last 365 days`, heatmap(d)));
  const R = INS.range, days = d.days.slice(-R), open = d.open.slice(-R), done = d.done.slice(-R);
  main.appendChild(rangeRow());
  main.appendChild(card('Open tasks', `how much is on the list, day by day — ${nice(open[open.length - 1])} today`, lineChart(days, open, W, 'var(--viz-blue)', 'open'),
    [['Day', 'Open tasks'], days.map((x, i) => [x, open[i]])]));
  if (R > 120) { const [wl, wv] = weekly(days, done); main.appendChild(card('Completed per week', `${plural(sum(done), 'task')} in the last ${R} days`, columns(wl.map(shortDay), wv, W, 'var(--viz-green)', 'done', { tipName: i => 'week of ' + longDay(wl[i]), peak: true }), [['Week of', 'Done'], wl.map((x, i) => [x, wv[i]])])); }
  else main.appendChild(card('Completed per day', `${plural(sum(done), 'task')} in the last ${R} days`, columns(days.map(shortDay), done, W, 'var(--viz-green)', 'done', { tipName: i => longDay(days[i]), peak: true }), [['Day', 'Done'], days.map((x, i) => [x, done[i]])]));
  const secs = d.by_section.slice(0, 7); if (d.by_section.length > 7) secs.push({ section: 'Other', done: sum(d.by_section.slice(7).map(s => s.done)), open: sum(d.by_section.slice(7).map(s => s.open)) });
  const grid = h('div', { class: 'viz-grid' });
  grid.appendChild(card('By section', 'where the finished work went (last 365 days)', secs.length ? hbars(secs.map(s => ({ label: s.section, value: s.done, open: s.open })), half, 'var(--viz-green)') : h('p', { class: 'empty-state', text: 'Nothing finished yet.' }), [['Section', 'Done', 'Open now'], secs.map(s => [s.section, s.done, s.open])]));
  const hours = d.by_hour.map((_, i) => `${i}h`);
  grid.appendChild(card('By hour of day', 'when tasks get ticked off', columns(hours, d.by_hour, half, 'var(--viz-green)', 'done', { every: 4, height: 130, peak: true }), [['Hour', 'Done'], hours.map((x, i) => [x, d.by_hour[i]])]));
  main.appendChild(grid);
  const dows = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  main.appendChild(card('By weekday', 'which days carry the work', columns(dows, d.by_dow, W, 'var(--viz-green)', 'done', { every: 1, height: 120, peak: true }), [['Weekday', 'Done'], dows.map((x, i) => [x, d.by_dow[i]])]));
  // time travel
  const past = h('section', { class: 'viz past' }, h('header', {}, h('h3', { text: 'Back in time' }), h('p', { text: 'Slide to a day (or click one in the heatmap) to see the list exactly as it was — read-only, with what changed since.' })));
  const firstSnap = d.snapshot_days[0], minI = firstSnap ? Math.max(0, d.days.indexOf(firstSnap)) : d.days.length - 1;
  const slider = h('input', { type: 'range', id: 'past-slider', min: minI, max: d.days.length - 1, value: INS.day ? d.days.indexOf(INS.day) : d.days.length - 1 });
  const lab = h('span', { class: 'past-day', text: INS.day ? longDay(INS.day) : 'today' });
  slider.oninput = () => { lab.textContent = longDay(d.days[+slider.value]); };
  slider.onchange = () => pickDay(d.days[+slider.value]);
  past.appendChild(h('div', { class: 'past-ctl' }, h('span', { class: 'ax', text: firstSnap ? shortDay(firstSnap) : '' }), slider, h('span', { class: 'ax', text: 'today' }), lab));
  past.appendChild(h('div', { id: 'past' }));
  main.appendChild(past);
  if (INS.day) pickDay(INS.day);
}
