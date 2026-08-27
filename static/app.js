'use strict';

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const pad2 = n => String(n).padStart(2, '0');
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SLOT_LABEL = { morning: 'morning', afternoon: 'afternoon', evening: 'evening' };
const PRIO_LABEL = { urgent: 'Urgent', soon: 'Soon', normal: 'Normal', later: 'Later', none: 'None' };
const PRIO_ORDER = { urgent: 0, soon: 1, normal: 2, later: 3, none: 4 };
const PRIO_COLOR = { urgent: 'var(--urgent)', soon: 'var(--soon)', normal: 'var(--normal)', later: 'var(--later)' };

function todayISO() { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; }
function parseISO(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function chipText(n) {
  if (!n.due_date) return '';
  const d = parseISO(n.due_date), diff = Math.round((d - parseISO(todayISO())) / 86400000);
  let label = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : diff > 1 && diff < 7 ? DAY[d.getDay()] : `${MON[d.getMonth()]} ${d.getDate()}`;
  if (n.due_slot) label += ' · ' + SLOT_LABEL[n.due_slot];
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
  doneBatch: (ids, done) => api.req('POST', '/api/done-batch', { ids, done }),
  move: (id, parent_id, after_id) => api.req('POST', `/api/nodes/${id}/move`, { parent_id, after_id }),
  split: (id, at, text, parent_id, after_id) => api.req('POST', `/api/nodes/${id}/split`, { at, text, parent_id, after_id }),
  del: (id, hard) => api.req('DELETE', `/api/nodes/${id}${hard ? '?hard=1' : ''}`),
  restore: (id, parent_id, after_id) => api.req('POST', `/api/nodes/${id}/restore`, parent_id === undefined ? {} : { parent_id, after_id }),
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
  noteOpen: new Set(),  // ids whose note editor is expanded (session-only, survives re-renders)
  nodes: new Map(), kids: new Map(), drag: null, query: '', current: null,
  view: localStorage.getItem('view') || 'all',
  hideDone: localStorage.getItem('hideDone') === '1',
};
function setNodes(list) { S.nodes = new Map(list.map(n => [n.id, n])); for (const n of S.nodes.values()) { n._saved = n.text; n._savedNote = n.note; } index(); }
function index() {
  S.kids = new Map();
  for (const n of S.nodes.values()) { if (!S.kids.has(n.parent_id)) S.kids.set(n.parent_id, []); S.kids.get(n.parent_id).push(n); }
  for (const a of S.kids.values()) a.sort((x, y) => x.position - y.position || x.id - y.id);
}
const kidsOf = id => S.kids.get(id) || [];
const nodeOf = el => { const x = el?.closest?.('.node'); return x ? S.nodes.get(+x.dataset.id) : null; };
function pathOf(n) { const out = []; let p = n.parent_id; while (p != null) { const pn = S.nodes.get(p); if (!pn) break; if (pn.kind === 'heading') out.unshift(pn.text); p = pn.parent_id; } return out; }
function isDescendant(n, ancestor) { let p = n.parent_id; while (p != null) { if (p === ancestor.id) return true; p = S.nodes.get(p)?.parent_id; } return false; }
function treeOrder() { const m = new Map(); let i = 0; (function walk(pid) { for (const n of kidsOf(pid)) { m.set(n.id, i++); walk(n.id); } })(null); return m; }
const hasQ = (n, q) => n.text.toLowerCase().includes(q) || (n.note || '').toLowerCase().includes(q);
function matchSet() {
  const q = S.query.trim().toLowerCase(); if (!q) return null;
  const set = new Set();
  for (const n of S.nodes.values()) if (hasQ(n, q)) { let cur = n; while (cur) { set.add(cur.id); cur = S.nodes.get(cur.parent_id); } }
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
  let rem = Math.max(0, offset), placed = false;
  for (const c of el.childNodes) {  // links are non-editable islands: the caret lands before or after them, never inside
    const len = c.textContent.length;
    if (c.nodeType === 3 && rem <= len) { range.setStart(c, rem); placed = true; break; }
    if (c.nodeType !== 3 && rem <= len) { if (rem === 0) range.setStartBefore(c); else range.setStartAfter(c); placed = true; break; }
    rem -= len;
  }
  if (!placed) { const last = el.lastChild; if (!last) range.setStart(el, 0); else if (last.nodeType === 3) range.setStart(last, last.length); else range.setStartAfter(last); }
  range.collapse(true); sel.removeAllRanges(); sel.addRange(range);
}
// Links live in the text as [label](url) (bare urls count too) and render as real anchors.
const LINK_RE = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"])/g;
function linkEl(label, href) {
  const a = document.createElement('a'); a.className = 'link'; a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.title = href;
  a.contentEditable = 'false'; a.textContent = label; return a;
}
function setText(el, text, multiline = false) {
  el.textContent = '';
  const lines = multiline ? text.split('\n') : [text];
  lines.forEach((line, i) => {
    if (i) el.appendChild(document.createElement('br'));
    let last = 0, m; LINK_RE.lastIndex = 0;
    while ((m = LINK_RE.exec(line))) {
      if (m.index > last) el.appendChild(document.createTextNode(line.slice(last, m.index)));
      el.appendChild(m[1] != null ? linkEl(m[1], m[2]) : linkEl(m[3], m[3]));
      last = m.index + m[0].length;
    }
    if (last < line.length) el.appendChild(document.createTextNode(line.slice(last)));
  });
  if (multiline && lines.length > 1 && lines.at(-1) === '') el.appendChild(document.createElement('br'));  // a trailing newline needs a second <br> to show as a line
  if (el.lastChild && el.lastChild.nodeType !== 3 && el.lastChild.nodeName !== 'BR') el.appendChild(document.createTextNode(ZW));
}
const ZW = '\u200b';
function getText(el) {
  let out = '';
  for (const c of el.childNodes) {
    if (c.nodeType === 3) out += c.data.replaceAll(ZW, '');
    else if (c.nodeName === 'BR') out += '\n';
    else if (c.classList?.contains('link')) { const href = c.getAttribute('href'), label = c.textContent; out += label === href ? href : `[${label}](${href})`; }
    else { if (/^(DIV|P)$/.test(c.nodeName) && out && !out.endsWith('\n')) out += '\n'; out += getText(c); }
  }
  return out;
}
// The browser keeps a <br> at the end of an editable block so the caret has a line to sit on; it is not a newline of the note.
function readNote(el) { const t = getText(el); return el.lastChild?.nodeName === 'BR' && t.endsWith('\n') ? t.slice(0, -1) : t; }
function textBeforeCaret(el) {
  const sel = getSelection(); if (!sel.rangeCount || !el.contains(sel.anchorNode)) return getText(el);
  const r = sel.getRangeAt(0).cloneRange(); r.setStart(el, 0);
  return getText(r.cloneContents());
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
// Where the caret is right now, in a form focusNode() can put back after a redraw.
function keepFocus() {
  const active = document.activeElement;
  if (active?.classList?.contains('note-text')) return { id: +active.closest('.node').dataset.id, note: caretOffset(active) };
  return active?.classList?.contains('text') ? { id: +active.closest('.node').dataset.id, caret: caretOffset(active) } : null;
}
function focusNode(id, caret = 'end') {
  if (caret != null && typeof caret === 'object' && 'note' in caret) return focusNote(id, caret.note);
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
  const keep = keepFocus();
  const y = window.scrollY;
  const main = $('#view');
  $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.view === S.view));
  tabCounts();
  ({ all: renderAll, today: renderToday, waiting: renderWaiting, done: renderDone, history: renderHistory })[S.view](main);
  if (S.current != null) $(`.node[data-id="${S.current}"]`)?.classList.add('current');
  window.scrollTo(0, y);
  if (keep) focusNode(keep.id, 'note' in keep ? { note: keep.note } : keep.caret);
}
function tabCounts() {
  const t = todayISO(); let today = 0, waiting = 0;
  for (const n of S.nodes.values()) if (n.kind === 'task' && !n.done_at) { if ((n.due_date && n.due_date <= t) || n.priority === 'urgent') today++; if (n.waiting_on) waiting++; }
  for (const b of $$('#tabs button')) {
    const c = { today, waiting }[b.dataset.view]; let badge = $('b', b);
    if (!c) { badge?.remove(); continue; }
    if (!badge) { badge = document.createElement('b'); b.appendChild(badge); }
    badge.textContent = c;
  }
}
function renderAll(main) {
  const match = matchSet();
  main.innerHTML = '';
  const tree = document.createElement('div'); tree.id = 'tree';
  tree.appendChild(renderChildren(null, 0, match, !!match));
  main.appendChild(tree);
  if (!S.nodes.size) { const p = document.createElement('p'); p.className = 'empty-state'; p.textContent = 'Empty list. Add a section to start.'; main.appendChild(p); }
  const add = document.createElement('button'); add.id = 'add-root'; add.textContent = 'New section';
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
    `<span class="handle" draggable="true" title="Drag to reorder"></span>` +
    `<button class="fold" tabindex="-1" title="Fold"></button>` +
    (isTask ? `<input type="checkbox" class="check" tabindex="-1" title="Done (Ctrl+Enter)">` : '') +
    (isTask ? `<button class="dot" tabindex="-1" title="Priority"></button>` : '') +
    `<span class="text" contenteditable="true" spellcheck="false"></span>` +
    `<span class="note-preview" title="Show note (Ctrl+.)"></span>` +
    (isTask ? `<button class="chip" tabindex="-1" title="Due (Ctrl+D)"></button>` : '') +
    (isTask ? `<button class="wait" tabindex="-1"></button>` : '') +
    `<button class="menu" tabindex="-1" title="More"></button>`;
  const t = $('.text', row);
  setText(t, n.text);
  if (n.kind === 'heading') t.dataset.placeholder = 'Section';
  el.appendChild(row);
  if (S.noteOpen.has(n.id)) el.appendChild(noteEditor(n));
  if (flat) {
    el.classList.add('flat');
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
  const pv = $(':scope > .row > .note-preview', el), open = S.noteOpen.has(n.id);
  if (open) pv.textContent = ''; else setText(pv, (n.note || '').split('\n')[0]);
  pv.classList.toggle('empty', !n.note && !open);
  pv.classList.toggle('open', open);
  pv.title = open ? 'Hide note (Ctrl+. or Esc)' : 'Show note (Ctrl+.)';
  const ed = $(':scope > .note > .note-text', el);
  if (ed && document.activeElement !== ed && readNote(ed) !== (n.note || '')) setText(ed, n.note || '', true);
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
  const waiting = !!n.waiting_on && !n.done_at;
  el.classList.toggle('waiting', waiting);
  const w = $(':scope > .row > .wait', el);
  if (w) {
    w.textContent = waiting ? `${n.waiting_on} · ${waitAge(n)}` : '+ waiting';
    w.classList.toggle('empty', !waiting);
    w.classList.toggle('stale', waiting && daysWaiting(n) >= WAIT_STALE_DAYS);
    w.title = waiting ? `Waiting on ${n.waiting_on} since ${n.waiting_since ? fmtDay(n.waiting_since.slice(0, 10)) : '?'} — click to bump or clear (Ctrl+B)` : 'Waiting on someone / something (Ctrl+B)';
  }
}
// Blocked todos stay todos: the row is hatched and carries who/what it waits on and for how long, so bumping is obvious.
const WAIT_STALE_DAYS = 7;
function daysWaiting(n) { return n.waiting_since ? Math.floor((Date.now() - new Date(n.waiting_since).getTime()) / 86400000) : 0; }
function waitAge(n) { const d = daysWaiting(n); return d <= 0 ? 'today' : d === 1 ? '1 day' : `${d} days`; }
function setWaiting(n, who, since, label) {
  patchFields(n, { waiting_on: who, waiting_since: since }, label || (who ? 'waiting on' : 'not waiting')).catch(showError);
  patchNodeDom(n);
  if (S.view !== 'all') render();
}
// Notes: free text under the title (emails, links, details). Collapsed to a grey first line; Ctrl+. or a click expands it.
function noteEditor(n) {
  const box = document.createElement('div'); box.className = 'note';
  const ed = document.createElement('div'); ed.className = 'note-text'; ed.contentEditable = 'true'; ed.spellcheck = false;
  ed.dataset.placeholder = 'Notes — emails, links, details you don’t want to keep in your head';
  setText(ed, n.note || '', true);
  box.appendChild(ed);
  return box;
}
function focusNote(id, at = 'end') {
  const ed = $(`.node[data-id="${id}"] > .note > .note-text`); if (!ed) return;
  if (at === 'end' && ed.lastChild?.nodeName === 'BR') {  // land on the empty last line, not before its <br>
    ed.focus(); const r = document.createRange(); r.setStart(ed, ed.childNodes.length); r.collapse(true); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
  } else setCaret(ed, at === 'end' ? ed.textContent.length : at);
  ed.scrollIntoView({ block: 'nearest' });
}
function toggleNote(n, focus = true) {
  const el = $(`.node[data-id="${n.id}"]`); if (!el) return;
  if (S.noteOpen.has(n.id)) {
    flushNote(n.id); S.noteOpen.delete(n.id);
    $(':scope > .note', el)?.remove();
    applyStyle(el, n);
    if (focus) focusNode(n.id, 'end');
    return;
  }
  S.noteOpen.add(n.id);
  const ed = noteEditor(n); el.insertBefore(ed, $(':scope > .row', el).nextSibling);
  applyStyle(el, n);
  if (focus) focusNote(n.id);
}
const noteTimers = new Map();
function onNoteInput(ta) {
  const n = nodeOf(ta); if (!n) return;
  n.note = readNote(ta) || null;
  clearTimeout(noteTimers.get(n.id));
  noteTimers.set(n.id, setTimeout(() => flushNote(n.id), 300));
}
function flushNote(id) {
  if (!noteTimers.has(id)) return;
  clearTimeout(noteTimers.get(id)); noteTimers.delete(id);
  const n = S.nodes.get(id); if (!n) return;
  const note = n.note || null;
  if (note === (n._savedNote || null)) return;
  pushTextUndo(id, n._savedNote || '', note || '', 'note'); n._savedNote = note;
  queue.run(() => api.patch(id, { note })).catch(showError);
}
function flushAll() { for (const id of [...textTimers.keys()]) flushText(id); for (const id of [...noteTimers.keys()]) flushNote(id); }
function onNoteKey(e, ta, n) {
  const ctrl = e.ctrlKey || e.metaKey;
  if (e.key === 'Escape' || (ctrl && e.key === '.')) { e.preventDefault(); closePopover(); return toggleNote(n, true); }
  if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); return runUndo(e.shiftKey ? 'redo' : 'undo'); }
  if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); return runUndo('redo'); }
  if (ctrl && e.key === 'Enter') { e.preventDefault(); return toggleDone(n); }
  if (e.key === 'Enter') { e.preventDefault(); if (!document.execCommand('insertLineBreak')) document.execCommand('insertHTML', false, '<br>'); return; }
}
function patchNodeDom(n) { const el = $(`.node[data-id="${n.id}"]`); if (el) applyStyle(el, n); }

// ---------------------------------------------------------------- text editing + saving
const textTimers = new Map();
function onInput(e) {
  const t = e.target;
  if (t.classList?.contains('note-text')) return onNoteInput(t);
  if (!t.classList?.contains('text')) return;
  const n = nodeOf(t); if (!n) return;
  n.text = getText(t);
  clearTimeout(textTimers.get(n.id));
  textTimers.set(n.id, setTimeout(() => flushText(n.id), 300));
}
function flushText(id) {
  if (!textTimers.has(id)) return;
  clearTimeout(textTimers.get(id)); textTimers.delete(id);
  const n = S.nodes.get(id); if (!n) return;
  const text = n.text;
  if (text === n._saved) return;
  pushTextUndo(id, n._saved, text); n._saved = text;
  queue.run(() => api.patch(id, { text })).catch(showError);
}
function onPaste(e) {
  const t = e.target; if (!t.classList?.contains('text') && !t.classList?.contains('note-text')) return;
  e.preventDefault();
  insertPasted(t, e.clipboardData.getData('text/plain') || '');
}
function insertPasted(t, raw) {
  const sel = getSelection(), url = raw.trim();
  if (/^https?:\/\/\S+$/.test(url) && sel.rangeCount && t.contains(sel.anchorNode)) {
    // a pasted url becomes a link at once; over highlighted words it links those words, like in Docs
    document.execCommand('insertHTML', false, linkEl(sel.isCollapsed ? url : sel.toString(), url).outerHTML + ZW);
    return;
  }
  if (t.classList.contains('note-text')) {
    const esc = raw.replace(/\r\n?/g, '\n').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    document.execCommand('insertHTML', false, esc.replaceAll('\n', '<br>'));
    return;
  }
  document.execCommand('insertText', false, raw.replace(/\s*[\r\n]+\s*/g, ' '));
}
// A structural change: flush pending text, run the write, refetch the tree, re-render, focus.
async function structural(fn, focus) {
  flushAll();
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

// ---------------------------------------------------------------- undo / redo
// Every change pushes its inverse. Ids of nodes re-created by redo are remapped through idMap.
const undoState = { stack: [], redo: [], idMap: new Map(), max: 300 };
const rid = id => { if (id == null) return null; const seen = new Set(); while (undoState.idMap.has(id) && !seen.has(id)) { seen.add(id); id = undoState.idMap.get(id); } return id; };
// A live id never redirects anywhere (sqlite may hand a re-created node the id it had before), so no self-loops or cycles.
const remap = (oldId, newId) => { undoState.idMap.delete(newId); if (oldId !== newId) undoState.idMap.set(oldId, newId); };
function pushUndo(entry) {
  entry.t = Date.now();
  const top = undoState.stack.at(-1);
  if (top && entry.type === 'patch' && top.type === 'patch' && top.id === entry.id && top.label === entry.label && !undoState.redo.length
      && entry.t - top.t < 1500 && Object.keys(top.new).join() === Object.keys(entry.new).join()) { top.new = entry.new; top.t = entry.t; return; }
  undoState.stack.push(entry);
  if (undoState.stack.length > undoState.max) undoState.stack.shift();
  undoState.redo.length = 0;
}
function pushTextUndo(id, oldText, newText, field = 'text') {
  const top = undoState.stack.at(-1);
  if (top && top.type === 'text' && top.field === field && top.id === id && !undoState.redo.length && Date.now() - top.t < 2500) { top.new = newText; top.t = Date.now(); return; }
  pushUndo({ type: 'text', field, id, old: oldText, new: newText, label: field === 'note' ? 'note' : 'typing' });
}
async function restoreAt(id, parent_id, after_id) {
  const p = rid(parent_id), a = rid(after_id);
  try { await api.restore(id, p, a); }
  catch (err) { if (!(err.status < 500)) throw err; try { await api.restore(id, p, null); } catch (e2) { await api.restore(id); } }
}
async function applyEntry(e, dir) {
  const id = rid(e.id);
  switch (e.type) {
    case 'text': {
      const v = dir === 'undo' ? e.old : e.new;
      if (e.field === 'note') { await api.patch(id, { note: v || null }); S.noteOpen.add(id); return { id, caret: { note: 'end' } }; }
      await api.patch(id, { text: v }); return { id, caret: 'end' };
    }
    case 'patch': {
      const vals = dir === 'undo' ? e.old : e.new, fields = {};
      for (const k of Object.keys(vals)) if (k !== 'done_at') fields[k] = vals[k];
      await api.patch(id, fields);
      if ('done_at' in vals) await api.done(id, !!vals.done_at);
      return { id, caret: 'end' };
    }
    case 'done': await api.doneBatch((e.changed || [e.id]).map(rid), dir === 'undo' ? !e.done : e.done); return { id, caret: 'end' };
    case 'move': { const to = dir === 'undo' ? e.from : e.to; await api.move(id, rid(to.parent_id), rid(to.after_id)); return { id, caret: 'end' }; }
    case 'create':
      if (dir === 'undo') { await api.del(id, true); return e.parent_id != null ? { id: rid(e.parent_id), caret: 'end' } : null; }
      { const r = await api.create({ parent_id: rid(e.parent_id), after_id: rid(e.after_id), kind: e.kind, text: e.text }); remap(e.id, r.id); return { id: r.id, caret: 'end' }; }
    case 'hardDelete':
      if (dir === 'undo') { const r = await api.create({ parent_id: rid(e.parent_id), after_id: rid(e.after_id), kind: e.kind, text: e.text }); remap(e.id, r.id); return { id: r.id, caret: 'end' }; }
      await api.del(id, true); return null;
    case 'split':
      if (dir === 'undo') { await api.del(rid(e.tail), true); await api.patch(id, { text: e.full }); return { id, caret: e.at }; }
      { const r = await api.split(id, e.at, e.full, rid(e.parent_id), rid(e.after_id)); remap(e.tail, r.nodes[1].id); return { id: r.nodes[1].id, caret: 0 }; }
    case 'archive':
      if (dir === 'undo') { await restoreAt(id, e.parent_id, e.after_id); return { id, caret: 'end' }; }
      await api.del(id); return null;
    case 'archiveDone':
      if (dir === 'undo') { for (const p of e.places) await restoreAt(rid(p.id), p.parent_id, p.after_id); return null; }
      { const r = await api.archiveDone(); e.places = e.places.filter(p => r.ids.includes(rid(p.id))); return null; }
  }
  return null;
}
async function runUndo(dir) {
  flushAll();
  const from = dir === 'undo' ? undoState.stack : undoState.redo, to = dir === 'undo' ? undoState.redo : undoState.stack;
  const e = from.pop();
  if (!e) return toast(dir === 'undo' ? 'Nothing to undo' : 'Nothing to redo');
  let focus = null;
  try { focus = await queue.run(() => applyEntry(e, dir)); } catch (err) { showError(err); return; }
  to.push(e);
  if (S.view !== 'all') focus = null;
  await refresh(focus);
  toast(`${dir === 'undo' ? 'Undid' : 'Redid'}: ${e.label || e.type}`);
}

// ---------------------------------------------------------------- operations
function placeOf(n) { const sibs = kidsOf(n.parent_id), i = sibs.indexOf(n); return { parent_id: n.parent_id, after_id: i > 0 ? sibs[i - 1].id : null }; }
function patchFields(n, fields, label) {
  const old = {}, nu = {};
  for (const k of Object.keys(fields)) { old[k] = n[k]; nu[k] = fields[k]; }
  if (fields.kind === 'heading') { for (const k of ['priority', 'due_date', 'due_slot', 'done_at']) old[k] = n[k]; Object.assign(n, { priority: 'none', due_date: null, due_slot: null, done_at: null }); }
  Object.assign(n, fields);
  pushUndo({ type: 'patch', id: n.id, old, new: nu, label });
  return queue.run(() => api.patch(n.id, fields));
}
function toggleDone(n) {
  if (n.kind !== 'task') return;
  const done = !n.done_at;
  n.done_at = done ? new Date().toISOString() : null; patchNodeDom(n);
  const entry = { type: 'done', id: n.id, done, changed: [n.id], label: done ? 'done' : 'not done' };
  pushUndo(entry);
  queue.run(() => api.done(n.id, done)).then(r => {
    const { changed, ...node } = r; Object.assign(n, node); patchNodeDom(n); entry.changed = changed;
    // sub-tasks / parent tasks followed along: redraw from the server's tree
    if (changed.length > 1) return refresh(keepFocus());
    if (S.hideDone || S.view !== 'all') render();
  }).catch(showError);
}
function setPriority(n, p) { if (n.kind !== 'task') return; patchFields(n, { priority: p, color: null }, 'priority').catch(showError); patchNodeDom(n); }
function setColor(n, hex) { patchFields(n, { color: hex }, 'color').catch(showError); patchNodeDom(n); }
function setDue(n, date, slot) { patchFields(n, { due_date: date, due_slot: date ? slot : null }, 'due date').catch(showError); patchNodeDom(n); }
function toggleFold(n) {
  n.collapsed = n.collapsed ? 0 : 1; render();
  queue.run(() => api.patch(n.id, { collapsed: !!n.collapsed })).catch(showError);
}
async function toggleKind(n, caret) {
  for (const id of [...textTimers.keys()]) flushText(id);
  try { await patchFields(n, { kind: n.kind === 'task' ? 'heading' : 'task' }, 'heading/task'); } catch (e) { showError(e); return; }
  await refresh({ id: n.id, caret });
}
function enterAt(n, t) {
  const text = getText(t), at = textBeforeCaret(t).length;
  let parent_id = n.parent_id, after_id = n.id;
  if (n.kind === 'heading' || (kidsOf(n.id).length && !n.collapsed)) { parent_id = n.id; after_id = null; }
  if (textTimers.has(n.id)) { clearTimeout(textTimers.get(n.id)); textTimers.delete(n.id); }
  if (text !== n._saved) { pushTextUndo(n.id, n._saved, text); n._saved = text; }  // the split carries the text
  return structural(() => api.split(n.id, at, text, parent_id, after_id), r => {
    pushUndo({ type: 'split', id: n.id, tail: r.nodes[1].id, at, full: text, parent_id, after_id, label: 'new item' });
    return { id: r.nodes[1].id, caret: 0 };
  });
}
function deleteNode(n) {
  const prev = neighborId(n.id, -1), place = placeOf(n), snap = { kind: n.kind, text: n.text };
  return structural(() => api.del(n.id), r => {
    if (r.hard) pushUndo({ type: 'hardDelete', id: n.id, ...place, ...snap, label: 'delete' });
    else { pushUndo({ type: 'archive', id: n.id, ...place, label: 'archive' }); toast(`Archived “${n.text.slice(0, 40)}${n.text.length > 40 ? '…' : ''}” — Ctrl+Z to undo`); }
    return prev != null ? { id: prev, caret: 'end' } : null;
  });
}
function moveNode(n, parent_id, after_id, caret, before) {
  const from = placeOf(n);
  if (from.parent_id === parent_id && from.after_id === after_id) return;
  return structural(async () => { if (before) await before(); return api.move(n.id, parent_id, after_id); }, () => {
    pushUndo({ type: 'move', id: n.id, from, to: { parent_id, after_id }, label: 'move' });
    return { id: n.id, caret: caret ?? 'end' };
  });
}
function indent(n, caret) {
  const sibs = kidsOf(n.parent_id), i = sibs.indexOf(n); if (i <= 0) return toast('Nothing above to nest under');
  const prev = sibs[i - 1], last = kidsOf(prev.id).at(-1);
  return moveNode(n, prev.id, last ? last.id : null, caret, prev.collapsed ? () => api.patch(prev.id, { collapsed: false }) : null);
}
function outdent(n, caret) {
  const p = S.nodes.get(n.parent_id); if (!p) return toast('Already at the top level');
  return moveNode(n, p.parent_id, p.id, caret);
}
// Alt+↑/↓: swap with a sibling; at the edge of a section, hop into the neighbouring section.
function moveVert(n, dir, caret) {
  const sibs = kidsOf(n.parent_id), i = sibs.indexOf(n), p = S.nodes.get(n.parent_id);
  const intoEnd = h => moveNode(n, h.id, kidsOf(h.id).at(-1)?.id ?? null, caret, h.collapsed ? () => api.patch(h.id, { collapsed: false }) : null);
  const intoTop = h => moveNode(n, h.id, null, caret, h.collapsed ? () => api.patch(h.id, { collapsed: false }) : null);
  if (dir < 0) {
    if (i > 0) { const prev = sibs[i - 1]; return prev.kind === 'heading' && n.kind !== 'heading' ? intoEnd(prev) : moveNode(n, n.parent_id, i >= 2 ? sibs[i - 2].id : null, caret); }
    if (!p) return;
    const ps = kidsOf(p.parent_id), pi = ps.indexOf(p), prev = ps[pi - 1];
    if (prev && prev.kind === 'heading' && n.kind !== 'heading') return intoEnd(prev);
    return moveNode(n, p.parent_id, pi >= 1 ? ps[pi - 1].id : null, caret);
  }
  if (i < sibs.length - 1) { const next = sibs[i + 1]; return next.kind === 'heading' && n.kind !== 'heading' ? intoTop(next) : moveNode(n, n.parent_id, next.id, caret); }
  if (!p) return;
  const ps = kidsOf(p.parent_id), pi = ps.indexOf(p), next = ps[pi + 1];
  if (next && next.kind === 'heading' && n.kind !== 'heading') return intoTop(next);
  return moveNode(n, p.parent_id, p.id, caret);
}
function moveToSection(n, h) { return moveNode(n, h.id, kidsOf(h.id).at(-1)?.id ?? null, 'end', h.collapsed ? () => api.patch(h.id, { collapsed: false }) : null); }
function archiveAllDone() {
  const order = treeOrder();
  const places = [...S.nodes.values()].filter(x => x.kind === 'task' && x.done_at).sort((a, b) => order.get(a.id) - order.get(b.id)).map(x => ({ id: x.id, ...placeOf(x) }));
  structural(() => api.archiveDone()).then(r => {
    if (!r) return;
    if (r.ids.length) pushUndo({ type: 'archiveDone', places: places.filter(p => r.ids.includes(p.id)), label: 'archive done' });
    toast(`Archived ${r.archived} done task${r.archived === 1 ? '' : 's'}`);
  });
}
function newRoot() {
  const last = kidsOf(null).at(-1), after_id = last ? last.id : null;
  structural(() => api.create({ parent_id: null, after_id, kind: 'heading', text: '' }), r => {
    pushUndo({ type: 'create', id: r.id, parent_id: null, after_id, kind: 'heading', text: '', label: 'new section' });
    return { id: r.id, caret: 0 };
  });
}

// ---------------------------------------------------------------- keyboard
const currentNode = () => S.current != null ? S.nodes.get(S.current) : null;
function setCurrent(id) {
  if (S.current !== id) { $('.node.current')?.classList.remove('current'); $(`.node[data-id="${id}"]`)?.classList.add('current'); }
  S.current = id;
}
function onKey(e) {
  const t = e.target;
  if (t.classList?.contains('note-text')) { const n = nodeOf(t); if (n) { S.current = n.id; onNoteKey(e, t, n); } return; }
  if (!t.classList?.contains('text')) return;
  const n = nodeOf(t); if (!n) return;
  setCurrent(n.id);
  const ctrl = e.ctrlKey || e.metaKey, outline = S.view === 'all';
  const caret = () => caretOffset(t);
  if (e.key === 'Escape') { closePopover(); t.blur(); return; }
  if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); return runUndo(e.shiftKey ? 'redo' : 'undo'); }
  if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); return runUndo('redo'); }
  if (e.key === 'Enter' && ctrl) { e.preventDefault(); return toggleDone(n); }
  if (ctrl && e.shiftKey && /^Digit[0-4]$/.test(e.code)) { e.preventDefault(); return setPriority(n, ['none', 'urgent', 'soon', 'normal', 'later'][+e.code.slice(5)]); }
  if (ctrl && !e.shiftKey && e.key.toLowerCase() === 'd') { e.preventDefault(); return openPopover($(':scope > .row > .chip', t.closest('.node')) || t, duePicker(n)); }
  if (ctrl && !e.shiftKey && e.key.toLowerCase() === 'b') { e.preventDefault(); if (n.kind === 'task') openPopover($(':scope > .row > .wait', t.closest('.node')) || t, waitPicker(n)); return; }
  if (ctrl && e.shiftKey && e.key.toLowerCase() === 'h') { e.preventDefault(); return toggleKind(n, caret()); }
  if (ctrl && e.key === '.') { e.preventDefault(); closePopover(); return toggleNote(n, true); }
  if (ctrl && e.key === '/') { e.preventDefault(); return openPopover($(':scope > .row > .menu', t.closest('.node')) || t, sectionPicker(n)); }
  if (!outline) { if (e.key === 'Enter' || e.key === 'Tab') e.preventDefault(); return; }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); return enterAt(n, t); }
  if (e.key === 'Backspace' && ctrl && e.shiftKey) { e.preventDefault(); return deleteNode(n); }
  if (e.key === 'Backspace' && t.textContent.replaceAll(ZW, '') === '') { e.preventDefault(); return deleteNode(n); }
  if (e.key === 'Tab') { e.preventDefault(); return e.shiftKey ? outdent(n, caret()) : indent(n, caret()); }
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); return moveVert(n, e.key === 'ArrowUp' ? -1 : 1, caret()); }
  if (e.key === 'ArrowUp' && !e.shiftKey && caretEdge(t).first) { const id = neighborId(n.id, -1); if (id != null) { e.preventDefault(); focusNode(id, 'end'); } return; }
  if (e.key === 'ArrowDown' && !e.shiftKey && caretEdge(t).last) { const id = neighborId(n.id, 1); if (id != null) { e.preventDefault(); focusNode(id, 0); } return; }
}
// Keys that must work even when focus is on a checkbox / button / nothing (Firefox lands there after clicks).
function onGlobalKey(e) {
  if (e.defaultPrevented) return;
  const ctrl = e.ctrlKey || e.metaKey, ae = document.activeElement, tag = ae?.tagName;
  const typing = tag === 'INPUT' || tag === 'TEXTAREA' || ae?.isContentEditable;
  if (ctrl && e.key.toLowerCase() === 'k') { e.preventDefault(); const s = $('#search'); s.focus(); s.select(); return; }
  if (e.key === 'Escape') { closePopover(); return; }
  if (typing && ae !== document.body) return;
  if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); return runUndo(e.shiftKey ? 'redo' : 'undo'); }
  if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); return runUndo('redo'); }
  if (ae && ae !== document.body && !$('#view').contains(ae)) return;  // top bar buttons keep their normal Tab behaviour
  const n = nodeOf(ae) || currentNode();
  if (!n || S.view !== 'all') return;
  if (e.key === 'Tab' && !ctrl && !e.altKey) { e.preventDefault(); closePopover(); return e.shiftKey ? outdent(n, 'end') : indent(n, 'end'); }
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); return moveVert(n, e.key === 'ArrowUp' ? -1 : 1, 'end'); }
  if (ctrl && e.key === 'Enter') { e.preventDefault(); return toggleDone(n); }
  if ((e.key === 'Delete' || e.key === 'Backspace') && !ctrl && !e.altKey) { e.preventDefault(); closePopover(); return deleteNode(n); }  // the row is selected, not being typed in
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
// "Move to section…": every heading, filterable; Enter picks the first match.
function sectionPicker(n) {
  const box = document.createElement('div'); box.className = 'menu-list section-picker';
  const input = document.createElement('input'); input.type = 'search'; input.placeholder = 'Move to section…'; input.className = 'section-filter';
  const list = document.createElement('div'); list.className = 'section-list';
  box.append(input, list);
  const order = treeOrder();
  const heads = [...S.nodes.values()].filter(h => h.kind === 'heading' && h.id !== n.id && !isDescendant(h, n)).sort((a, b) => order.get(a.id) - order.get(b.id));
  const draw = () => {
    list.innerHTML = '';
    const q = input.value.trim().toLowerCase();
    for (const h of heads) {
      const label = [...pathOf(h), h.text || '(untitled)'].join(' › ');
      if (q && !label.toLowerCase().includes(q)) continue;
      const b = document.createElement('button'); b.textContent = label;
      if (h.id === n.parent_id) b.classList.add('current');
      b.onclick = () => { closePopover(); moveToSection(n, h); };
      list.appendChild(b);
    }
    if (!list.children.length) { const p = document.createElement('div'); p.className = 'none'; p.textContent = 'No section matches'; list.appendChild(p); }
  };
  input.oninput = draw;
  input.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); $('button', list)?.click(); } else if (e.key === 'Escape') closePopover(); };
  draw();
  setTimeout(() => input.focus(), 0);
  return box;
}
function waitPicker(n) {
  const box = document.createElement('div'); box.className = 'wait-picker';
  box.innerHTML = `<div class="wait-title">Waiting on</div><input class="wait-input" placeholder="who or what — e.g. Sam: which channel?"><div class="wait-actions"></div><div class="wait-note"></div>`;
  const inp = $('.wait-input', box), acts = $('.wait-actions', box);
  inp.value = n.waiting_on || '';
  $('.wait-note', box).textContent = n.waiting_on ? `Since ${n.waiting_since ? fmtDay(n.waiting_since.slice(0, 10)) : '?'} (${waitAge(n)}). It stays on the list — bump when you chase it.` : 'The item stays a normal todo; it just shows who you are waiting for and since when.';
  const finish = () => { closePopover(); focusNode(n.id); };
  const clear = () => { setWaiting(n, null, null); finish(); };
  const set = () => { const v = inp.value.trim(); if (!v) return clear(); setWaiting(n, v, v === n.waiting_on ? n.waiting_since : new Date().toISOString()); finish(); };
  const btn = (label, fn, cls) => { const b = document.createElement('button'); b.textContent = label; b.className = cls || ''; b.onclick = fn; acts.appendChild(b); };
  btn(n.waiting_on ? 'Update' : 'Mark as waiting', set, 'primary');
  if (n.waiting_on) { btn('Bump · still waiting', () => { setWaiting(n, n.waiting_on, new Date().toISOString(), 'bump'); finish(); }); btn('Not waiting anymore', clear); }
  inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); set(); } };
  setTimeout(() => { inp.focus(); inp.select(); }, 0);
  return box;
}
function nodeMenu(n) {
  const box = document.createElement('div'); box.className = 'menu-list';
  const anchor = () => $(`.node[data-id="${n.id}"] > .row > .menu`);
  const caret = () => { const t = $(`.node[data-id="${n.id}"] > .row > .text`); return t ? t.textContent.length : 0; };
  const items = [
    ['Move to section…', 'Ctrl+/', () => openPopover(anchor() || document.body, sectionPicker(n)), true],
    ['Nest under item above', 'Tab', () => indent(n, caret())],
    ['Un-nest', 'Shift+Tab', () => outdent(n, caret())],
    ['Move up', 'Alt+↑', () => moveVert(n, -1, caret())],
    ['Move down', 'Alt+↓', () => moveVert(n, 1, caret())],
    null,
    [n.note ? (S.noteOpen.has(n.id) ? 'Hide note' : 'Show note') : 'Add note', 'Ctrl+.', () => toggleNote(n, true)],
    [n.kind === 'task' ? 'Make heading' : 'Make task', 'Ctrl+Shift+H', () => toggleKind(n, caret())],
    n.kind === 'task' ? [n.done_at ? 'Mark not done' : 'Mark done', 'Ctrl+Enter', () => toggleDone(n)] : null,
    n.kind === 'task' ? [n.waiting_on ? `Waiting on ${n.waiting_on}…` : 'Waiting on…', 'Ctrl+B', () => openPopover(anchor() || document.body, waitPicker(n)), true] : null,
    n.kind === 'task' && n.waiting_on ? ['Bump · still waiting', '', () => setWaiting(n, n.waiting_on, new Date().toISOString(), 'bump')] : null,
    n.kind === 'task' && n.waiting_on ? ['Not waiting anymore', '', () => setWaiting(n, null, null)] : null,
    null,
    [n.text.trim() ? 'Archive (keeps history)' : 'Delete', 'Del', () => deleteNode(n)],
  ];
  for (const it of items) {
    if (it === null) { box.appendChild(document.createElement('hr')); continue; }
    const [label, key, fn, keepOpen] = it;
    const b = document.createElement('button'); b.innerHTML = `<span></span><kbd></kbd>`;
    b.firstChild.textContent = label; b.lastChild.textContent = key;
    b.onclick = () => { if (!keepOpen) closePopover(); fn(); };
    box.appendChild(b);
  }
  return box;
}
function helpPanel() {
  const box = document.createElement('div'); box.className = 'help';
  const rows = [
    ['Enter', 'New item below (splits at cursor)'], ['Delete', 'Remove the selected item (click its row first, or Ctrl+Shift+Backspace while typing in it)'],
    ['Paste a link', 'Highlight words and paste a URL: they become a link'],
    ['Tab / Shift+Tab', 'Nest under the item above / un-nest'], ['Alt+↑ / Alt+↓', 'Move up / down (hops into the next section at the edge)'],
    ['Ctrl+/', 'Move to another section'], ['Drag the grip', 'Reorder; drop on a section title to move into it'],
    ['↑ / ↓', 'Previous / next item'], ['Ctrl+Enter', 'Done / not done'],
    ['Ctrl+Shift+1 2 3 4', 'Urgent / Soon / Normal / Later'], ['Ctrl+Shift+0', 'No priority'],
    ['Ctrl+D', 'Due date'], ['Ctrl+B', 'Waiting on someone / something (bump or clear from the same place)'],
    ['Ctrl+.', 'Notes on the item (emails, links, details); Esc hides them again'],
    ['Ctrl+Shift+H', 'Heading ↔ task'], ['Ctrl+Z / Ctrl+Y', 'Undo / redo'],
    ['Ctrl+K', 'Search'], ['Esc', 'Close / unfocus'],
  ];
  box.innerHTML = '<table>' + rows.map(([k, v]) => `<tr><td><kbd>${k}</kbd></td><td>${v}</td></tr>`).join('') + '</table>';
  return box;
}

// ---------------------------------------------------------------- mouse: clicks + drag
function onClick(e) {
  const a = e.target.closest('a.link');
  if (a && $('#view').contains(a)) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); return; }
  const pv = e.target.closest('.note-preview');
  if (pv && $('#view').contains(pv)) { const n = nodeOf(pv); if (n) { S.current = n.id; toggleNote(n, true); } return; }
  const btn = e.target.closest('button, .check');
  if (!btn || !$('#view').contains(btn)) return;
  const nodeEl = btn.closest('.node'); if (!nodeEl) return;
  const n = nodeOf(btn); if (!n) return;
  setCurrent(n.id);
  if (btn.classList.contains('fold')) return toggleFold(n);
  if (btn.classList.contains('dot')) return openPopover(btn, priorityPicker(n));
  if (btn.classList.contains('chip')) return openPopover(btn, duePicker(n));
  if (btn.classList.contains('wait')) return openPopover(btn, waitPicker(n));
  if (btn.classList.contains('menu')) return openPopover(btn, nodeMenu(n));
}
function onChange(e) { if (e.target.classList?.contains('check')) { const n = nodeOf(e.target); if (n) toggleDone(n); } }
function clearDropMarks() { $$('.row.drop-before, .row.drop-after, .row.drop-into').forEach(r => r.classList.remove('drop-before', 'drop-after', 'drop-into')); }
function onDragStart(e) {
  const h = e.target.closest?.('.handle'); if (!h) { e.preventDefault(); return; }
  S.drag = +h.closest('.node').dataset.id;
  h.closest('.node').classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(S.drag));
}
function onDragOver(e) {
  const row = e.target.closest?.('.row'); if (!row || S.drag == null) return;
  e.preventDefault(); e.dataTransfer.dropEffect = 'move';
  const r = row.getBoundingClientRect(), target = nodeOf(row);
  clearDropMarks();
  if (e.clientY < r.top + r.height / 2) row.classList.add('drop-before');
  else row.classList.add(target && target.kind === 'heading' ? 'drop-into' : 'drop-after');
}
function onDrop(e) {
  const row = e.target.closest?.('.row'); if (!row || S.drag == null) return;
  e.preventDefault();
  const mode = row.classList.contains('drop-before') ? 'before' : row.classList.contains('drop-into') ? 'into' : 'after';
  const target = nodeOf(row), dragged = S.nodes.get(S.drag);
  onDragEnd();
  if (!target || !dragged || target.id === dragged.id || isDescendant(target, dragged)) return;
  if (mode === 'into') return moveNode(dragged, target.id, null, 'end', target.collapsed ? () => api.patch(target.id, { collapsed: false }) : null);
  const sibs = kidsOf(target.parent_id), i = sibs.indexOf(target);
  const after = mode === 'before' ? (i > 0 ? sibs[i - 1].id : null) : target.id;
  if (after === dragged.id) return;
  moveNode(dragged, target.parent_id, after, 'end');
}
function onDragEnd() { S.drag = null; clearDropMarks(); $$('.node.dragging').forEach(el => el.classList.remove('dragging')); }

// ---------------------------------------------------------------- views
function renderToday(main) {
  main.innerHTML = '';
  const t = todayISO(), q = S.query.trim().toLowerCase(), order = treeOrder();
  const items = [...S.nodes.values()]
    .filter(n => n.kind === 'task' && !n.done_at && ((n.due_date && n.due_date <= t) || n.priority === 'urgent'))
    .filter(n => !q || hasQ(n, q))
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
function renderWaiting(main) {
  main.innerHTML = '';
  const q = S.query.trim().toLowerCase();
  const items = [...S.nodes.values()]
    .filter(n => n.kind === 'task' && n.waiting_on && !n.done_at)
    .filter(n => !q || hasQ(n, q) || n.waiting_on.toLowerCase().includes(q))
    .sort((a, b) => (a.waiting_since || '').localeCompare(b.waiting_since || ''));
  if (!items.length) { main.innerHTML = '<p class="empty-state">Nothing is waiting on anyone. Ctrl+B on a task marks who or what it waits for.</p>'; return; }
  const h = document.createElement('h2'); h.className = 'group'; h.textContent = `${items.length} waiting · oldest first`; main.appendChild(h);
  for (const n of items) {
    const el = renderNode(n, 0, null, false, true);
    const p = document.createElement('span'); p.className = 'path'; p.textContent = pathOf(n).join(' › ');
    $(':scope > .row', el).insertBefore(p, $(':scope > .row > .menu', el));
    main.appendChild(el);
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
      setText($('.log-text', row), n.text);
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
    const clip = v => (v || '∅').replace(/\s+/g, ' ').slice(0, 140);
    tr.children[3].textContent = r.action === 'edit' ? `${r.field}: ${clip(r.old)} → ${clip(r.new)}` : '';
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
  view.addEventListener('focusin', e => { const el = e.target.closest?.('.node'); if (el) setCurrent(+el.dataset.id); });
  view.addEventListener('mousedown', e => { const el = e.target.closest?.('.node'); if (el) setCurrent(+el.dataset.id); });
  view.addEventListener('focusout', e => { if (e.target.classList?.contains('text')) flushText(+e.target.closest('.node').dataset.id); if (e.target.classList?.contains('note-text')) flushNote(+e.target.closest('.node').dataset.id); });
  view.addEventListener('beforeinput', e => { if (e.inputType === 'historyUndo' || e.inputType === 'historyRedo') { e.preventDefault(); runUndo(e.inputType === 'historyUndo' ? 'undo' : 'redo'); } });
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
  document.addEventListener('keydown', onGlobalKey);
  document.addEventListener('mousedown', e => { const pop = $('#popover'); if (!pop.hidden && !pop.contains(e.target) && !e.target.closest('.dot, .chip, .menu, #help-btn')) closePopover(); });
  window.addEventListener('beforeunload', flushAll);
  refresh();
}
boot();
