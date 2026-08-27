'use strict';

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const pad2 = n => String(n).padStart(2, '0');
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SLOT_LABEL = { morning: 'AM', afternoon: 'PM', evening: 'eve' };
const PRIO_LABEL = { urgent: 'Urgent', soon: 'Soon', normal: 'Normal', later: 'Later', none: 'None' };
const PRIO_ORDER = { urgent: 0, soon: 1, normal: 2, later: 3, none: 4 };
const PRIO_COLOR = { urgent: 'var(--urgent)', soon: 'var(--soon)', normal: 'var(--normal)', later: 'var(--later)' };

function todayISO() { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; }
function parseISO(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function chipText(n) {
  if (!n.due_date) return '';
  const d = parseISO(n.due_date), diff = Math.round((d - parseISO(todayISO())) / 86400000);
  let label = diff >= 0 && diff < 7 ? DAY[d.getDay()] : `${MON[d.getMonth()]} ${d.getDate()}`;
  if (n.due_slot) label += ' ' + SLOT_LABEL[n.due_slot];
  return label;
}
const isOverdue = n => !!n.due_date && !n.done_at && n.due_date < todayISO();
function fmtDay(iso) { const d = parseISO(iso); const y = d.getFullYear() === new Date().getFullYear() ? '' : ` ${d.getFullYear()}`; return `${DAY[d.getDay()]}, ${MON[d.getMonth()]} ${d.getDate()}${y}`; }
function fmtTime(iso) { const d = new Date(iso); return `${MON[d.getMonth()]} ${d.getDate()}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove('show'), 2200); }
function showError(e) { console.error(e); toast(e.message || String(e)); }

// ---------------------------------------------------------------- api + serial write queue
const api = {
  async req(method, path, body) {
    const res = await fetch(path, {
      method, headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.error || res.statusText); err.status = res.status; throw err; }
    return data;
  },
  tree: () => api.req('GET', '/api/tree'),
  create: b => api.req('POST', '/api/nodes', b),
  patch: (id, b) => api.req('PATCH', `/api/nodes/${id}`, b),
  done: (id, done) => api.req('POST', `/api/nodes/${id}/done`, { done }),
  move: (id, parent_id, after_id) => api.req('POST', `/api/nodes/${id}/move`, { parent_id, after_id }),
  split: (id, at, text, parent_id, after_id) => api.req('POST', `/api/nodes/${id}/split`, { at, text, parent_id, after_id }),
  del: id => api.req('DELETE', `/api/nodes/${id}`),
  archiveDone: () => api.req('POST', '/api/archive-done', {}),
  doneLog: () => api.req('GET', '/api/done'),
  history: q => api.req('GET', `/api/history?q=${encodeURIComponent(q)}`),
};

// Writes run one at a time, in order. Network/server failures retry forever (1s, 2s, … 30s);
// a 4xx (bad request) is surfaced and not retried.
const queue = {
  chain: Promise.resolve(), pending: 0,
  run(fn) {
    this.pending++; setStatus();
    const p = this.chain.then(async () => {
      let delay = 1000;
      for (;;) {
        try { const r = await fn(); setStatus(); return r; }
        catch (e) { if (e.status && e.status < 500) throw e; setStatus(e); await sleep(delay); delay = Math.min(delay * 2, 30000); }
      }
    }).finally(() => { this.pending--; setStatus(); });
    this.chain = p.catch(() => {});
    return p;
  },
};
function setStatus(err) {
  const el = $('#status');
  el.className = err ? 'error' : queue.pending ? 'saving' : 'saved';
  el.title = err ? `not saved: ${err.message} — retrying` : queue.pending ? 'saving…' : 'saved';
}

// ---------------------------------------------------------------- state
const S = {
  nodes: new Map(), kids: new Map(), drag: null, query: '',
  view: localStorage.getItem('view') || 'all',
  hideDone: localStorage.getItem('hideDone') === '1',
};
function setNodes(list) { S.nodes = new Map(list.map(n => [n.id, n])); index(); }
function index() {
  S.kids = new Map();
  for (const n of S.nodes.values()) { if (!S.kids.has(n.parent_id)) S.kids.set(n.parent_id, []); S.kids.get(n.parent_id).push(n); }
  for (const a of S.kids.values()) a.sort((x, y) => x.position - y.position || x.id - y.id);
}
const kidsOf = id => S.kids.get(id) || [];
const nodeOf = el => S.nodes.get(+el.closest('.node').dataset.id);
function pathOf(n) { const out = []; let p = n.parent_id; while (p != null) { const pn = S.nodes.get(p); if (!pn) break; if (pn.kind === 'heading') out.unshift(pn.text); p = pn.parent_id; } return out; }
function isDescendant(n, ancestor) { let p = n.parent_id; while (p != null) { if (p === ancestor.id) return true; p = S.nodes.get(p)?.parent_id; } return false; }
function treeOrder() { const m = new Map(); let i = 0; (function walk(pid) { for (const n of kidsOf(pid)) { m.set(n.id, i++); walk(n.id); } })(null); return m; }
function matchSet() {
  const q = S.query.trim().toLowerCase(); if (!q) return null;
  const set = new Set();
  for (const n of S.nodes.values()) if (n.text.toLowerCase().includes(q)) { let cur = n; while (cur) { set.add(cur.id); cur = S.nodes.get(cur.parent_id); } }
  return set;
}

// ---------------------------------------------------------------- caret helpers
function caretOffset(el) {
  const sel = getSelection(); if (!sel.rangeCount || !el.contains(sel.anchorNode)) return el.textContent.length;
  const r = sel.getRangeAt(0).cloneRange(); r.selectNodeContents(el); r.setEnd(sel.getRangeAt(0).endContainer, sel.getRangeAt(0).endOffset);
  return r.toString().length;
}
function setCaret(el, offset) {
  el.focus();
  const range = document.createRange(), sel = getSelection();
  const node = el.firstChild;
  if (!node) range.setStart(el, 0); else range.setStart(node, Math.max(0, Math.min(offset, node.length)));
  range.collapse(true); sel.removeAllRanges(); sel.addRange(range);
}
function caretEdge(el) {
  const sel = getSelection();
  if (!sel.rangeCount || !el.textContent) return { first: true, last: true };
  const r = sel.getRangeAt(0).cloneRange(); r.collapse(true);
  const rect = r.getClientRects()[0];
  if (!rect) { const off = caretOffset(el); return { first: off === 0, last: off === el.textContent.length }; }
  const box = el.getBoundingClientRect(), lh = rect.height || 20;
  return { first: rect.top < box.top + lh * 0.6, last: rect.bottom > box.bottom - lh * 0.6 };
}
function focusNode(id, caret = 'end') {
  const t = $(`.node[data-id="${id}"] > .row > .text`);
  if (!t) return;
  setCaret(t, caret === 'end' ? t.textContent.length : caret);
  t.scrollIntoView({ block: 'nearest' });
}
const visibleNodeEls = () => $$('#view .node');
function neighborId(id, dir) {
  const els = visibleNodeEls(); const i = els.findIndex(el => +el.dataset.id === id);
  const el = els[i + dir]; return el ? +el.dataset.id : null;
}

// ---------------------------------------------------------------- rendering
function render() {
  const active = document.activeElement;
  const keep = active?.classList?.contains('text') ? { id: +active.closest('.node').dataset.id, caret: caretOffset(active) } : null;
  const y = window.scrollY;
  const main = $('#view');
  $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.view === S.view));
  ({ all: renderAll, today: renderToday, done: renderDone, history: renderHistory })[S.view](main);
  window.scrollTo(0, y);
  if (keep) focusNode(keep.id, keep.caret);
}
function renderAll(main) {
  const match = matchSet();
  main.innerHTML = '';
  const tree = document.createElement('div'); tree.id = 'tree';
  tree.appendChild(renderChildren(null, 0, match, !!match));
  main.appendChild(tree);
  if (!S.nodes.size) { const p = document.createElement('p'); p.className = 'empty-state'; p.textContent = 'Empty list. Add a section to start.'; main.appendChild(p); }
  const add = document.createElement('button'); add.id = 'add-root'; add.textContent = '+ New section';
  add.onclick = () => { const last = kidsOf(null).at(-1); structural(() => api.create({ parent_id: null, after_id: last ? last.id : null, kind: 'heading', text: '' }), r => ({ id: r.id, caret: 0 })); };
  main.appendChild(add);
}
function renderChildren(pid, depth, match, forceOpen) {
  const frag = document.createDocumentFragment();
  for (const n of kidsOf(pid)) {
    if (S.hideDone && n.done_at) continue;
    if (match && !match.has(n.id)) continue;
    frag.appendChild(renderNode(n, depth, match, forceOpen, false));
  }
  return frag;
}
function renderNode(n, depth, match, forceOpen, flat) {
  const el = document.createElement('div');
  el.className = `node ${n.kind} d${Math.min(depth, 3)}`;
  el.dataset.id = n.id;
  const isTask = n.kind === 'task';
  const row = document.createElement('div'); row.className = 'row';
  row.innerHTML =
    `<span class="handle" draggable="true" title="Drag to reorder">⋮⋮</span>` +
    `<button class="fold" tabindex="-1" title="Fold">▾</button>` +
    (isTask ? `<input type="checkbox" class="check" tabindex="-1" title="Done (Ctrl+Enter)">` : '') +
    (isTask ? `<button class="dot" tabindex="-1" title="Priority"></button>` : '') +
    `<span class="text" contenteditable="true" spellcheck="false"></span>` +
    (isTask ? `<button class="chip" tabindex="-1" title="Due (Ctrl+D)"></button>` : '') +
    `<button class="menu" tabindex="-1" title="More">⋯</button>`;
  const t = $('.text', row);
  t.textContent = n.text;
  if (n.kind === 'heading') t.dataset.placeholder = 'Section';
  el.appendChild(row);
  if (flat) {
    const p = document.createElement('span'); p.className = 'path'; p.textContent = ''; // path shown by group header
    $('.handle', row).remove(); $('.fold', row).remove();
  } else {
    const kids = document.createElement('div'); kids.className = 'kids';
    if (!n.collapsed || forceOpen) kids.appendChild(renderChildren(n.id, depth + 1, match, forceOpen));
    el.appendChild(kids);
  }
  applyStyle(el, n);
  return el;
}
function applyStyle(el, n) {
  el.dataset.prio = n.priority;
  el.classList.toggle('done', !!n.done_at);
  el.classList.toggle('collapsed', !!n.collapsed);
  el.classList.toggle('has-kids', kidsOf(n.id).length > 0);
  const t = $(':scope > .row > .text', el);
  t.style.color = n.color && !n.done_at ? n.color : '';
  const check = $(':scope > .row > .check', el); if (check) check.checked = !!n.done_at;
  const dot = $(':scope > .row > .dot', el);
  if (dot) { const c = n.color || PRIO_COLOR[n.priority] || ''; dot.style.background = c; dot.style.borderColor = c || ''; }
  const chip = $(':scope > .row > .chip', el);
  if (chip) {
    chip.textContent = chipText(n) || '+ due';  // real text: a button with only ::before content breaks the row's baseline layout
    chip.classList.toggle('empty', !n.due_date);
    chip.classList.toggle('overdue', isOverdue(n));
    chip.classList.toggle('today', n.due_date === todayISO() && !n.done_at);
  }
}
function patchNodeDom(n) { const el = $(`.node[data-id="${n.id}"]`); if (el) applyStyle(el, n); }

// ---------------------------------------------------------------- text editing + saving
const textTimers = new Map();
function onInput(e) {
  const t = e.target; if (!t.classList?.contains('text')) return;
  const n = nodeOf(t); if (!n) return;
  n.text = t.textContent;
  clearTimeout(textTimers.get(n.id));
  textTimers.set(n.id, setTimeout(() => flushText(n.id), 300));
}
function flushText(id) {
  if (!textTimers.has(id)) return;
  clearTimeout(textTimers.get(id)); textTimers.delete(id);
  const n = S.nodes.get(id); if (!n) return;
  const text = n.text;
  queue.run(() => api.patch(id, { text })).catch(showError);
}
function onPaste(e) {
  const t = e.target; if (!t.classList?.contains('text')) return;
  e.preventDefault();
  const text = (e.clipboardData.getData('text/plain') || '').replace(/\s*[\r\n]+\s*/g, ' ');
  document.execCommand('insertText', false, text);
}
// A structural change: flush pending text, run the write, refetch the tree, re-render, focus.
async function structural(fn, focus) {
  for (const id of [...textTimers.keys()]) flushText(id);
  let res;
  try { res = await queue.run(fn); } catch (e) { showError(e); return null; }
  await refresh(typeof focus === 'function' ? focus(res) : focus);
  return res;
}
async function refresh(focus) {
  try { setNodes((await api.tree()).nodes); } catch (e) { showError(e); return; }
  render();
  if (focus && focus.id != null) focusNode(focus.id, focus.caret);
}

// ---------------------------------------------------------------- operations
function toggleDone(n) {
  if (n.kind !== 'task') return;
  const done = !n.done_at;
  n.done_at = done ? new Date().toISOString() : null; patchNodeDom(n);
  queue.run(() => api.done(n.id, done)).then(r => { Object.assign(n, r); patchNodeDom(n); if (S.hideDone || S.view !== 'all') render(); }).catch(showError);
}
function setPriority(n, p) {
  if (n.kind !== 'task') return;
  n.priority = p; n.color = null; patchNodeDom(n);
  queue.run(() => api.patch(n.id, { priority: p, color: null })).catch(showError);
}
function setColor(n, hex) {
  n.color = hex; patchNodeDom(n);
  queue.run(() => api.patch(n.id, { color: hex })).catch(showError);
}
function setDue(n, date, slot) {
  n.due_date = date; n.due_slot = date ? slot : null; patchNodeDom(n);
  queue.run(() => api.patch(n.id, { due_date: n.due_date, due_slot: n.due_slot })).catch(showError);
}
function toggleFold(n) {
  n.collapsed = n.collapsed ? 0 : 1; render();
  queue.run(() => api.patch(n.id, { collapsed: !!n.collapsed })).catch(showError);
}
function toggleKind(n, caret) {
  return structural(() => api.patch(n.id, { kind: n.kind === 'task' ? 'heading' : 'task' }), { id: n.id, caret });
}
function enterAt(n, t) {
  const at = caretOffset(t), text = t.textContent;
  let parent_id = n.parent_id, after_id = n.id;
  if (n.kind === 'heading' || (kidsOf(n.id).length && !n.collapsed)) { parent_id = n.id; after_id = null; }
  textTimers.delete(n.id);  // the split carries the current text; drop the pending patch
  return structural(() => api.split(n.id, at, text, parent_id, after_id), r => ({ id: r.nodes[1].id, caret: 0 }));
}
function deleteNode(n) {
  const prev = neighborId(n.id, -1);
  return structural(() => api.del(n.id), prev != null ? { id: prev, caret: 'end' } : null);
}
function indent(n, caret) {
  const sibs = kidsOf(n.parent_id), i = sibs.indexOf(n); if (i <= 0) return;
  const prev = sibs[i - 1], last = kidsOf(prev.id).at(-1);
  return structural(async () => { if (prev.collapsed) await api.patch(prev.id, { collapsed: false }); return api.move(n.id, prev.id, last ? last.id : null); }, { id: n.id, caret });
}
function outdent(n, caret) {
  const p = S.nodes.get(n.parent_id); if (!p) return;
  return structural(() => api.move(n.id, p.parent_id, p.id), { id: n.id, caret });
}
function moveVert(n, dir, caret) {
  const sibs = kidsOf(n.parent_id), i = sibs.indexOf(n);
  let after;
  if (dir < 0) { if (i <= 0) return; after = i >= 2 ? sibs[i - 2].id : null; }
  else { if (i >= sibs.length - 1) return; after = sibs[i + 1].id; }
  return structural(() => api.move(n.id, n.parent_id, after), { id: n.id, caret });
}
function archiveAllDone() {
  structural(() => api.archiveDone()).then(r => { if (r) toast(`Archived ${r.archived} done task${r.archived === 1 ? '' : 's'}`); });
}

// ---------------------------------------------------------------- keyboard
function onKey(e) {
  const t = e.target; if (!t.classList?.contains('text')) return;
  const n = nodeOf(t); if (!n) return;
  const ctrl = e.ctrlKey || e.metaKey, outline = S.view === 'all';
  const caret = () => caretOffset(t);
  if (e.key === 'Escape') { closePopover(); t.blur(); return; }
  if (e.key === 'Enter' && ctrl) { e.preventDefault(); return toggleDone(n); }
  if (ctrl && e.shiftKey && /^Digit[0-4]$/.test(e.code)) { e.preventDefault(); return setPriority(n, ['none', 'urgent', 'soon', 'normal', 'later'][+e.code.slice(5)]); }
  if (ctrl && !e.shiftKey && e.key.toLowerCase() === 'd') { e.preventDefault(); return openPopover($(':scope > .row > .chip', t.closest('.node')) || t, duePicker(n)); }
  if (ctrl && e.shiftKey && e.key.toLowerCase() === 'h') { e.preventDefault(); return toggleKind(n, caret()); }
  if (!outline) { if (e.key === 'Enter' || e.key === 'Tab') e.preventDefault(); return; }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); return enterAt(n, t); }
  if (e.key === 'Backspace' && t.textContent === '') { e.preventDefault(); return deleteNode(n); }
  if (e.key === 'Tab') { e.preventDefault(); return e.shiftKey ? outdent(n, caret()) : indent(n, caret()); }
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); return moveVert(n, e.key === 'ArrowUp' ? -1 : 1, caret()); }
  if (e.key === 'ArrowUp' && !e.shiftKey && caretEdge(t).first) { const id = neighborId(n.id, -1); if (id != null) { e.preventDefault(); focusNode(id, 'end'); } return; }
  if (e.key === 'ArrowDown' && !e.shiftKey && caretEdge(t).last) { const id = neighborId(n.id, 1); if (id != null) { e.preventDefault(); focusNode(id, 0); } return; }
}

// ---------------------------------------------------------------- popovers
function openPopover(anchor, content) {
  const pop = $('#popover');
  pop.innerHTML = ''; pop.appendChild(content); pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  let left = r.left + window.scrollX, top = r.bottom + window.scrollY + 4;
  pop.style.left = '0px'; pop.style.top = '0px';
  const w = pop.offsetWidth; if (left + w > window.scrollX + window.innerWidth - 8) left = Math.max(8, window.scrollX + window.innerWidth - w - 8);
  pop.style.left = left + 'px'; pop.style.top = top + 'px';
  pop.dataset.for = anchor.closest('.node')?.dataset.id || '';
}
function closePopover() { const pop = $('#popover'); pop.hidden = true; pop.innerHTML = ''; }
function priorityPicker(n) {
  const box = document.createElement('div'); box.className = 'picker';
  for (const p of ['urgent', 'soon', 'normal', 'later']) {
    const b = document.createElement('button'); b.className = 'swatch'; b.dataset.prio = p;
    b.innerHTML = `<i></i><span>${PRIO_LABEL[p]}</span><kbd>Ctrl+Shift+${PRIO_ORDER[p] + 1}</kbd>`;
    b.onclick = () => { setPriority(n, p); closePopover(); };
    box.appendChild(b);
  }
  const custom = document.createElement('label'); custom.className = 'swatch';
  custom.innerHTML = `<input type="color" value="${n.color || '#8844cc'}"><span>Custom color</span>`;
  $('input', custom).oninput = e => setColor(n, e.target.value);
  box.appendChild(custom);
  const clear = document.createElement('button'); clear.className = 'swatch'; clear.innerHTML = '<i></i><span>None</span><kbd>Ctrl+Shift+0</kbd>';
  clear.onclick = () => { setPriority(n, 'none'); closePopover(); };
  box.appendChild(clear);
  return box;
}
function duePicker(n) {
  const box = document.createElement('div'); box.className = 'due';
  const start = n.due_date ? parseISO(n.due_date) : new Date();
  const ym = { y: start.getFullYear(), m: start.getMonth() };
  const draw = () => {
    box.innerHTML = '';
    const head = document.createElement('div'); head.className = 'cal-head';
    head.innerHTML = `<button class="prev" title="Previous month">‹</button><span>${MON[ym.m]} ${ym.y}</span><button class="next" title="Next month">›</button>`;
    $('.prev', head).onclick = () => { if (--ym.m < 0) { ym.m = 11; ym.y--; } draw(); };
    $('.next', head).onclick = () => { if (++ym.m > 11) { ym.m = 0; ym.y++; } draw(); };
    box.appendChild(head);
    const grid = document.createElement('div'); grid.className = 'cal-grid';
    for (const d of ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']) { const s = document.createElement('span'); s.className = 'dow'; s.textContent = d; grid.appendChild(s); }
    const pad = (new Date(ym.y, ym.m, 1).getDay() + 6) % 7;
    for (let i = 0; i < pad; i++) grid.appendChild(document.createElement('span'));
    const days = new Date(ym.y, ym.m + 1, 0).getDate();
    for (let d = 1; d <= days; d++) {
      const iso = `${ym.y}-${pad2(ym.m + 1)}-${pad2(d)}`;
      const b = document.createElement('button'); b.textContent = d;
      b.className = 'day' + (iso === todayISO() ? ' today' : '') + (iso === n.due_date ? ' sel' : '');
      b.onclick = () => { setDue(n, iso, n.due_slot); draw(); };
      grid.appendChild(b);
    }
    box.appendChild(grid);
    const slots = document.createElement('div'); slots.className = 'slots';
    for (const s of ['morning', 'afternoon', 'evening']) {
      const b = document.createElement('button'); b.textContent = s[0].toUpperCase() + s.slice(1);
      b.className = n.due_slot === s ? 'sel' : '';
      b.onclick = () => { setDue(n, n.due_date || todayISO(), n.due_slot === s ? null : s); draw(); };
      slots.appendChild(b);
    }
    const clear = document.createElement('button'); clear.className = 'clear'; clear.textContent = 'Clear';
    clear.onclick = () => { setDue(n, null, null); closePopover(); };
    slots.appendChild(clear);
    box.appendChild(slots);
  };
  draw();
  return box;
}
function nodeMenu(n) {
  const box = document.createElement('div'); box.className = 'menu-list';
  const caret = () => { const t = $(`.node[data-id="${n.id}"] > .row > .text`); return t ? t.textContent.length : 0; };
  const items = [
    ['Indent', 'Tab', () => indent(n, caret())],
    ['Outdent', 'Shift+Tab', () => outdent(n, caret())],
    ['Move up', 'Alt+↑', () => moveVert(n, -1, caret())],
    ['Move down', 'Alt+↓', () => moveVert(n, 1, caret())],
    null,
    [n.kind === 'task' ? 'Make heading' : 'Make task', 'Ctrl+Shift+H', () => toggleKind(n, caret())],
    n.kind === 'task' ? [n.done_at ? 'Mark not done' : 'Mark done', 'Ctrl+Enter', () => toggleDone(n)] : null,
    null,
    [n.text.trim() ? 'Archive (keeps history)' : 'Delete', 'Backspace on empty', () => deleteNode(n)],
  ];
  for (const it of items) {
    if (it === null) { box.appendChild(document.createElement('hr')); continue; }
    const [label, key, fn] = it;
    const b = document.createElement('button'); b.innerHTML = `<span></span><kbd></kbd>`;
    b.firstChild.textContent = label; b.lastChild.textContent = key;
    b.onclick = () => { closePopover(); fn(); };
    box.appendChild(b);
  }
  return box;
}
function helpPanel() {
  const box = document.createElement('div'); box.className = 'help';
  const rows = [
    ['Enter', 'New item below (splits at cursor)'], ['Backspace on empty', 'Delete item'],
    ['Tab / Shift+Tab', 'Nest / un-nest'], ['Alt+↑ / Alt+↓', 'Move item up / down'],
    ['↑ / ↓', 'Previous / next item'], ['Ctrl+Enter', 'Done / not done'],
    ['Ctrl+Shift+1 2 3 4', 'Urgent / Soon / Normal / Later'], ['Ctrl+Shift+0', 'No priority'],
    ['Ctrl+D', 'Due date'], ['Ctrl+Shift+H', 'Heading ↔ task'], ['Ctrl+K', 'Search'], ['Esc', 'Close / unfocus'],
  ];
  box.innerHTML = '<table>' + rows.map(([k, v]) => `<tr><td><kbd>${k}</kbd></td><td>${v}</td></tr>`).join('') + '</table>';
  return box;
}

// ---------------------------------------------------------------- mouse: clicks + drag
function onClick(e) {
  const btn = e.target.closest('button, .check');
  if (!btn || !$('#view').contains(btn)) return;
  const nodeEl = btn.closest('.node'); if (!nodeEl) return;
  const n = nodeOf(btn); if (!n) return;
  if (btn.classList.contains('fold')) return toggleFold(n);
  if (btn.classList.contains('dot')) return openPopover(btn, priorityPicker(n));
  if (btn.classList.contains('chip')) return openPopover(btn, duePicker(n));
  if (btn.classList.contains('menu')) return openPopover(btn, nodeMenu(n));
}
function onChange(e) { if (e.target.classList?.contains('check')) { const n = nodeOf(e.target); if (n) toggleDone(n); } }
function clearDropMarks() { $$('.row.drop-before, .row.drop-after').forEach(r => r.classList.remove('drop-before', 'drop-after')); }
function onDragStart(e) {
  const h = e.target.closest?.('.handle'); if (!h) { e.preventDefault(); return; }
  S.drag = +h.closest('.node').dataset.id;
  h.closest('.node').classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(S.drag));
}
function onDragOver(e) {
  const row = e.target.closest?.('.row'); if (!row || S.drag == null) return;
  e.preventDefault(); e.dataTransfer.dropEffect = 'move';
  const r = row.getBoundingClientRect();
  clearDropMarks(); row.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
}
function onDrop(e) {
  const row = e.target.closest?.('.row'); if (!row || S.drag == null) return;
  e.preventDefault();
  const before = row.classList.contains('drop-before');
  const target = nodeOf(row), dragged = S.nodes.get(S.drag);
  onDragEnd();
  if (!target || !dragged || target.id === dragged.id || isDescendant(target, dragged)) return;
  const sibs = kidsOf(target.parent_id), i = sibs.indexOf(target);
  const after = before ? (i > 0 ? sibs[i - 1].id : null) : target.id;
  if (after === dragged.id) return;
  structural(() => api.move(dragged.id, target.parent_id, after));
}
function onDragEnd() { S.drag = null; clearDropMarks(); $$('.node.dragging').forEach(el => el.classList.remove('dragging')); }

// ---------------------------------------------------------------- views
function renderToday(main) {
  main.innerHTML = '';
  const t = todayISO(), q = S.query.trim().toLowerCase(), order = treeOrder();
  const items = [...S.nodes.values()]
    .filter(n => n.kind === 'task' && !n.done_at && ((n.due_date && n.due_date <= t) || n.priority === 'urgent'))
    .filter(n => !q || n.text.toLowerCase().includes(q))
    .sort((a, b) => order.get(a.id) - order.get(b.id));
  if (!items.length) { main.innerHTML = '<p class="empty-state">Nothing due today and nothing urgent.</p>'; return; }
  const groups = new Map();
  for (const n of items) { const key = pathOf(n).join(' › ') || '(top level)'; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(n); }
  for (const [key, list] of groups) {
    const h = document.createElement('h2'); h.className = 'group'; h.textContent = key; main.appendChild(h);
    list.sort((a, b) => (a.due_date || '9999').localeCompare(b.due_date || '9999') || PRIO_ORDER[a.priority] - PRIO_ORDER[b.priority]);
    for (const n of list) main.appendChild(renderNode(n, 0, null, false, true));
  }
}
async function renderDone(main) {
  main.innerHTML = '<p class="empty-state">Loading…</p>';
  let days;
  try { days = (await api.doneLog()).days; } catch (e) { showError(e); return; }
  if (S.view !== 'done') return;
  main.innerHTML = '';
  const q = S.query.trim().toLowerCase();
  let any = false;
  for (const day of days) {
    const items = day.items.filter(n => !q || n.text.toLowerCase().includes(q));
    if (!items.length) continue;
    any = true;
    const h = document.createElement('h2'); h.className = 'group'; h.textContent = `${fmtDay(day.day)} · ${items.length}`; main.appendChild(h);
    for (const n of items) {
      const row = document.createElement('div'); row.className = 'log-row';
      row.innerHTML = `<input type="checkbox" checked title="Mark not done"><span class="log-text"></span><span class="path"></span><span class="log-time"></span>`;
      $('.log-text', row).textContent = n.text;
      $('.path', row).textContent = n.path.join(' › ');
      $('.log-time', row).textContent = n.done_at.slice(11, 16);
      $('input', row).onchange = () => queue.run(() => api.done(n.id, false)).then(() => refresh()).catch(showError);
      main.appendChild(row);
    }
  }
  if (!any) main.innerHTML = '<p class="empty-state">Nothing done yet.</p>';
}
async function renderHistory(main) {
  main.innerHTML = '<p class="empty-state">Loading…</p>';
  let rows;
  try { rows = (await api.history(S.query.trim())).rows; } catch (e) { showError(e); return; }
  if (S.view !== 'history') return;
  main.innerHTML = '';
  if (!rows.length) { main.innerHTML = '<p class="empty-state">No history.</p>'; return; }
  const table = document.createElement('table'); table.className = 'history';
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td class="h-time"></td><td class="h-action"></td><td class="h-text"></td><td class="h-detail"></td>';
    tr.children[0].textContent = fmtTime(r.ts);
    tr.children[1].textContent = r.action;
    tr.children[2].textContent = r.snapshot;
    tr.children[3].textContent = r.action === 'edit' ? `${r.field}: ${r.old || '∅'} → ${r.new || '∅'}` : '';
    table.appendChild(tr);
  }
  main.appendChild(table);
}

// ---------------------------------------------------------------- top bar + boot
function setView(v) { S.view = v; localStorage.setItem('view', v); closePopover(); render(); }
function boot() {
  const view = $('#view');
  view.addEventListener('keydown', onKey);
  view.addEventListener('input', onInput);
  view.addEventListener('paste', onPaste);
  view.addEventListener('click', onClick);
  view.addEventListener('change', onChange);
  view.addEventListener('focusout', e => { if (e.target.classList?.contains('text')) flushText(+e.target.closest('.node').dataset.id); });
  view.addEventListener('dragstart', onDragStart);
  view.addEventListener('dragover', onDragOver);
  view.addEventListener('drop', onDrop);
  view.addEventListener('dragend', onDragEnd);
  $$('#tabs button').forEach(b => b.onclick = () => setView(b.dataset.view));
  const hide = $('#hide-done'); hide.checked = S.hideDone;
  hide.onchange = () => { S.hideDone = hide.checked; localStorage.setItem('hideDone', hide.checked ? '1' : '0'); render(); };
  $('#archive-done').onclick = archiveAllDone;
  $('#help-btn').onclick = e => openPopover(e.target, helpPanel());
  const search = $('#search');
  let st; search.oninput = () => { clearTimeout(st); st = setTimeout(() => { S.query = search.value; render(); }, 150); };
  search.onkeydown = e => { if (e.key === 'Escape') { search.value = ''; S.query = ''; render(); search.blur(); } };
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); search.focus(); search.select(); }
    else if (e.key === 'Escape') closePopover();
  });
  document.addEventListener('mousedown', e => { const pop = $('#popover'); if (!pop.hidden && !pop.contains(e.target) && !e.target.closest('.dot, .chip, .menu, #help-btn')) closePopover(); });
  window.addEventListener('beforeunload', () => { for (const id of [...textTimers.keys()]) flushText(id); });
  refresh();
}
boot();
