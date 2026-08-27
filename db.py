"""SQLite store for the todo tree. Stdlib only.

Every public write runs in one transaction, appends history rows, then
regenerates the markdown mirror when `md_path` is set.
"""
import contextlib
import datetime
import json
import re
import sqlite3
import threading
import zlib

import export_md

PRIORITIES = ('urgent', 'soon', 'normal', 'later', 'none')
SLOTS = ('morning', 'afternoon', 'evening')
KINDS = ('heading', 'task')
EDITABLE = ('text', 'priority', 'color', 'due_date', 'due_slot', 'kind', 'collapsed', 'waiting_on', 'waiting_since', 'note')
TEXT_COALESCE_SECONDS = 120  # successive text edits within this window share one history row
_UNSET = object()

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id          INTEGER PRIMARY KEY,
  parent_id   INTEGER REFERENCES nodes(id),
  position    INTEGER NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('heading','task')),
  text        TEXT NOT NULL DEFAULT '',
  priority    TEXT NOT NULL DEFAULT 'none'
              CHECK (priority IN ('urgent','soon','normal','later','none')),
  color       TEXT,
  due_date    TEXT,
  due_slot    TEXT CHECK (due_slot IN ('morning','afternoon','evening')),
  collapsed   INTEGER NOT NULL DEFAULT 0,
  done_at     TEXT,
  waiting_on  TEXT,
  waiting_since TEXT,
  note        TEXT,
  auto_urgent TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  archived_at TEXT
);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent_id, position);
CREATE TABLE IF NOT EXISTS history (
  id       INTEGER PRIMARY KEY,
  node_id  INTEGER NOT NULL REFERENCES nodes(id),
  ts       TEXT NOT NULL,
  action   TEXT NOT NULL,
  field    TEXT,
  old      TEXT,
  new      TEXT,
  snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_ts ON history(ts);
CREATE TABLE IF NOT EXISTS snapshots (
  day      TEXT PRIMARY KEY,
  taken_at TEXT NOT NULL,
  nodes    BLOB NOT NULL
);
"""
RETENTION_DAYS = 365  # snapshots and history older than this are dropped when the store opens


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')


def _due_str(n):
    return ' '.join(x for x in (n.get('due_date'), n.get('due_slot')) if x)


def _s(v):
    return '' if v is None else str(v)


def _due_soon(n, today=None):
    """Open task due today, tomorrow, or already overdue."""
    if n['kind'] != 'task' or n['done_at'] or not n['due_date']:
        return False
    today = today or datetime.date.today()
    return datetime.date.fromisoformat(n['due_date']) <= today + datetime.timedelta(days=1)


class StoreError(ValueError):
    """Bad input from a caller (maps to HTTP 400)."""


class Store:
    def __init__(self, path, md_path=None):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.md_path = md_path
        self.lock = threading.RLock()
        self._prune()
        self._snapshot()

    def _migrate(self):
        """Columns added after the first release; older databases get them on open."""
        cols = {r[1] for r in self.conn.execute('PRAGMA table_info(nodes)')}
        for col in ('waiting_on', 'waiting_since', 'note', 'auto_urgent'):
            if col not in cols:
                self.conn.execute(f'ALTER TABLE nodes ADD COLUMN {col} TEXT')
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------ reads
    def get(self, node_id):
        row = self.conn.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        if row is None:
            raise StoreError(f'no node {node_id}')
        return dict(row)

    def tree(self):
        rows = self.conn.execute(
            'SELECT * FROM nodes WHERE archived_at IS NULL ORDER BY parent_id, position, id')
        return [dict(r) for r in rows]

    def history(self, q='', limit=200):
        like = f'%{q}%'
        rows = self.conn.execute(
            'SELECT * FROM history WHERE snapshot LIKE ? OR old LIKE ? OR new LIKE ? '
            'ORDER BY ts DESC, id DESC LIMIT ?', (like, like, like, limit))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ internals
    @contextlib.contextmanager
    def _tx(self):
        with self.lock:
            with self.conn:
                yield
            self._export()

    def _export(self):
        if self.md_path:
            export_md.write(self.md_path, self.tree())
        self._snapshot()

    # ------------------------------------------------------------ snapshots (one per day: the list as it was at the day's last save)
    def _snapshot(self):
        day = datetime.date.today().isoformat()
        blob = zlib.compress(json.dumps(self.tree(), separators=(',', ':')).encode())
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO snapshots(day, taken_at, nodes) VALUES (?,?,?)', (day, now_iso(), blob))

    def _prune(self):
        cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).isoformat()
        with self.conn:
            self.conn.execute('DELETE FROM snapshots WHERE day < ?', (cutoff,))
            self.conn.execute('DELETE FROM history WHERE substr(ts, 1, 10) < ?', (cutoff,))

    def snapshot_days(self):
        return [r['day'] for r in self.conn.execute('SELECT day FROM snapshots ORDER BY day')]

    def snapshot(self, day):
        """The list as it stood at the end of `day` (the latest snapshot on or before it), or None."""
        row = self.conn.execute('SELECT day, taken_at, nodes FROM snapshots WHERE day <= ? ORDER BY day DESC LIMIT 1', (day,)).fetchone()
        if row is None:
            return None
        return {'day': row['day'], 'taken_at': row['taken_at'], 'nodes': json.loads(zlib.decompress(row['nodes']))}

    # ------------------------------------------------------------ insights
    def insights(self, today=None):
        today = today or datetime.date.today()
        start = today - datetime.timedelta(days=RETENTION_DAYS - 1)
        days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(RETENTION_DAYS)]
        idx = {d: i for i, d in enumerate(days)}
        done = [0] * len(days); created = [0] * len(days); undone = [0] * len(days)
        by_hour = [0] * 24; by_dow = [0] * 7; section_done = {}
        first_day = None
        sections = {}  # node id -> top-level section text, for nodes still known
        for r in self.conn.execute("SELECT node_id, ts, action FROM history WHERE action IN ('done','undone','create','import') AND substr(ts,1,10) >= ? ORDER BY ts", (start.isoformat(),)):
            d = r['ts'][:10]
            if d not in idx:
                continue
            first_day = first_day or d
            i = idx[d]
            if r['action'] == 'done':
                done[i] += 1
                t = datetime.datetime.fromisoformat(r['ts'])
                by_hour[t.hour] += 1; by_dow[t.weekday()] += 1
                nid = r['node_id']
                if nid not in sections:
                    try:
                        p = self.path(nid)
                        sections[nid] = p[0] if p else self.get(nid)['text'] or '(top level)'
                    except StoreError:
                        sections[nid] = '(removed)'
                section_done[sections[nid]] = section_done.get(sections[nid], 0) + 1
            elif r['action'] == 'undone':
                undone[i] += 1
            else:
                created[i] += 1
        # open tasks per day: real snapshots where we have them, otherwise walked back from today's count
        snaps = {r['day']: r['nodes'] for r in self.conn.execute('SELECT day, nodes FROM snapshots WHERE day >= ?', (start.isoformat(),))}
        live = [n for n in self.tree() if n['kind'] == 'task']
        open_now = sum(1 for n in live if not n['done_at'])
        open_series = [None] * len(days)
        cur = open_now
        for i in range(len(days) - 1, -1, -1):
            d = days[i]
            if d in snaps:
                nodes = json.loads(zlib.decompress(snaps[d]))
                cur = sum(1 for n in nodes if n['kind'] == 'task' and not n['done_at'])
            open_series[i] = cur
            cur = max(0, cur - created[i] + done[i] - undone[i])
        # streaks: consecutive days with at least one completion, counted back from today (or yesterday)
        streak = 0; i = len(days) - 1
        if done[i] == 0:
            i -= 1
        while i >= 0 and done[i] > 0:
            streak += 1; i -= 1
        best = run = 0
        for v in done:
            run = run + 1 if v else 0
            best = max(best, run)
        by_section = sorted(section_done.items(), key=lambda kv: -kv[1])
        open_by_section = {}
        for n in live:
            if n['done_at']:
                continue
            p = self.path(n['id'])
            key = p[0] if p else '(top level)'
            open_by_section[key] = open_by_section.get(key, 0) + 1
        overdue = sum(1 for n in live if not n['done_at'] and n['due_date'] and n['due_date'] < today.isoformat())
        return {
            'days': days, 'done': done, 'created': created, 'open': open_series,
            'by_hour': by_hour, 'by_dow': by_dow,
            'by_section': [{'section': k, 'done': v, 'open': open_by_section.get(k, 0)} for k, v in by_section],
            'streak': streak, 'best_streak': best, 'first_day': first_day,
            'totals': {'open': open_now, 'overdue': overdue, 'waiting': sum(1 for n in live if not n['done_at'] and n['waiting_on']),
                       'done_7': sum(done[-7:]), 'done_30': sum(done[-30:]), 'done_365': sum(done),
                       'created_30': sum(created[-30:]), 'done_all_time': self.conn.execute("SELECT COUNT(*) FROM history WHERE action='done'").fetchone()[0]},
            'snapshot_days': self.snapshot_days(),
        }

    def _siblings(self, parent_id):
        rows = self.conn.execute(
            'SELECT id FROM nodes WHERE archived_at IS NULL AND parent_id IS ? '
            'ORDER BY position, id', (parent_id,))
        return [r['id'] for r in rows]

    def _renumber(self, ids):
        for i, nid in enumerate(ids):
            self.conn.execute('UPDATE nodes SET position=? WHERE id=?', (i, nid))

    def _place(self, node_id, parent_id, after_id):
        """Put node_id among parent_id's active children right after after_id (None = first)."""
        sibs = [s for s in self._siblings(parent_id) if s != node_id]
        if after_id is None:
            idx = 0
        else:
            if after_id not in sibs:
                raise StoreError(f'after_id {after_id} is not a child of {parent_id}')
            idx = sibs.index(after_id) + 1
        sibs.insert(idx, node_id)
        self._renumber(sibs)

    def _log(self, node_id, action, field=None, old=None, new=None, snapshot=None, ts=None):
        if snapshot is None:
            snapshot = self.get(node_id)['text']
        self.conn.execute(
            'INSERT INTO history(node_id, ts, action, field, old, new, snapshot) VALUES (?,?,?,?,?,?,?)',
            (node_id, ts or now_iso(), action, field, old, new, snapshot))

    def _log_text_edit(self, node_id, old, new, ts, field='text', snapshot=None):
        """Coalesce a burst of keystrokes (title or note) into one history row."""
        if snapshot is None:
            snapshot = new
        last = self.conn.execute(
            'SELECT * FROM history WHERE node_id=? ORDER BY id DESC LIMIT 1', (node_id,)).fetchone()
        if last is not None and last['action'] == 'edit' and last['field'] == field:
            age = (datetime.datetime.fromisoformat(ts) - datetime.datetime.fromisoformat(last['ts'])).total_seconds()
            if 0 <= age <= TEXT_COALESCE_SECONDS:
                self.conn.execute('UPDATE history SET new=?, snapshot=?, ts=? WHERE id=?',
                                  (new, snapshot, ts, last['id']))
                return
        self._log(node_id, 'edit', field=field, old=old, new=new, snapshot=snapshot, ts=ts)

    def _validate(self, **f):
        if 'kind' in f and f['kind'] not in KINDS:
            raise StoreError(f"bad kind {f['kind']!r}")
        if 'priority' in f and f['priority'] not in PRIORITIES:
            raise StoreError(f"bad priority {f['priority']!r}")
        if f.get('due_slot') is not None and f['due_slot'] not in SLOTS:
            raise StoreError(f"bad due_slot {f['due_slot']!r}")
        if f.get('due_date') is not None:
            try:
                datetime.date.fromisoformat(f['due_date'])
            except (TypeError, ValueError):
                raise StoreError(f"bad due_date {f['due_date']!r}") from None
        if f.get('color') is not None and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(f['color'])):
            raise StoreError(f"bad color {f['color']!r}")
        if 'text' in f and not isinstance(f['text'], str):
            raise StoreError('text must be a string')
        if f.get('waiting_on') is not None and not isinstance(f['waiting_on'], str):
            raise StoreError('waiting_on must be a string')
        if f.get('note') is not None and not isinstance(f['note'], str):
            raise StoreError('note must be a string')
        if f.get('waiting_since') is not None:
            try:
                datetime.datetime.fromisoformat(f['waiting_since'])
            except (TypeError, ValueError):
                raise StoreError(f"bad waiting_since {f['waiting_since']!r}") from None

    def _create(self, parent_id, after_id, kind, text, priority, color, due_date, due_slot, done_at, action):
        self._validate(kind=kind, priority=priority, color=color, due_date=due_date,
                       due_slot=due_slot, text=text)
        if parent_id is not None:
            self.get(parent_id)
        if after_id is _UNSET:  # default: append after the last sibling
            sibs = self._siblings(parent_id)
            after_id = sibs[-1] if sibs else None
        ts = now_iso()
        cur = self.conn.execute(
            'INSERT INTO nodes(parent_id, position, kind, text, priority, color, due_date, due_slot, '
            'done_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (parent_id, 1 << 30, kind, text, priority, color, due_date, due_slot, done_at, ts, ts))
        nid = cur.lastrowid
        self._place(nid, parent_id, after_id)
        self._log(nid, action, snapshot=text, ts=ts)
        return nid

    # ------------------------------------------------------------ writes
    def create(self, parent_id=None, after_id=_UNSET, kind='task', text='', priority='none',
               color=None, due_date=None, due_slot=None, done_at=None, action='create'):
        with self._tx():
            nid = self._create(parent_id, after_id, kind, text, priority, color,
                               due_date, due_slot, done_at, action)
        return self.get(nid)

    def update(self, node_id, **fields):
        unknown = set(fields) - set(EDITABLE)
        if unknown:
            raise StoreError(f'cannot edit {sorted(unknown)}')
        self._validate(**fields)
        with self._tx():
            old = self.get(node_id)
            ts = now_iso()
            changes = {}
            for k, v in fields.items():
                if k == 'collapsed':
                    v = 1 if v else 0
                if k == 'waiting_on':
                    v = v.strip() or None if v else None
                if k == 'note':
                    v = v if v and v.strip() else None
                if old[k] != v:
                    changes[k] = v
            if changes.get('waiting_on', old['waiting_on']) is None:
                changes['waiting_since'] = None  # nothing to wait for, nothing to count from
            elif 'waiting_on' in changes and not (changes.get('waiting_since') or old['waiting_since']):
                changes['waiting_since'] = ts
            if changes.get('waiting_since') == old['waiting_since']:
                del changes['waiting_since']
            auto = set()  # fields changed as a side effect (becoming a heading, coming due); logged differently or not at all
            if changes.get('kind') == 'heading':
                for k, v in (('priority', 'none'), ('due_date', None), ('due_slot', None), ('done_at', None),
                             ('waiting_on', None), ('waiting_since', None)):
                    if old[k] != v:
                        changes[k] = v
                        auto.add(k)
            # Coming due today or tomorrow (or overdue) tags the task red once per due date; a colour the user
            # picks afterwards sticks, and picking one first counts as the user's decision for that date.
            new = {**old, **changes}
            due_auto = False
            if new['kind'] == 'task' and _due_soon(new) and new.get('auto_urgent') != new['due_date']:
                changes['auto_urgent'] = new['due_date']
                if 'priority' not in fields and 'color' not in fields:
                    if old['priority'] != 'urgent':
                        changes['priority'] = 'urgent'
                        auto.add('priority')
                        due_auto = True
                    if old['color'] is not None:
                        changes['color'] = None
                        auto.add('color')
            elif new['due_date'] is None and old.get('auto_urgent'):
                changes['auto_urgent'] = None
            if not changes:
                return old
            snapshot = changes.get('text', old['text'])
            for k, v in changes.items():
                if k in ('collapsed', 'done_at', 'due_date', 'due_slot', 'waiting_since', 'auto_urgent'):
                    continue
                if k in auto:
                    if k == 'priority' and due_auto:
                        self._log(node_id, 'edit', field='priority', old=_s(old[k]), new='urgent (due soon)', snapshot=snapshot, ts=ts)
                    continue
                if k == 'text':
                    self._log_text_edit(node_id, old['text'], v, ts)
                elif k == 'note':
                    self._log_text_edit(node_id, _s(old['note']), _s(v), ts, field='note', snapshot=snapshot)
                else:
                    self._log(node_id, 'edit', field='waiting' if k == 'waiting_on' else k,
                              old=_s(old[k]), new=_s(v), snapshot=snapshot, ts=ts)
            if 'waiting_since' in changes and 'waiting_on' not in changes and old['waiting_on'] and 'waiting_since' not in auto:
                self._log(node_id, 'edit', field='waiting', old=old['waiting_on'], new=old['waiting_on'] + ' (bumped)',
                          snapshot=snapshot, ts=ts)
            if ('due_date' in changes or 'due_slot' in changes) and not auto & {'due_date', 'due_slot'}:
                self._log(node_id, 'edit', field='due', old=_due_str(old),
                          new=_due_str({**old, **changes}), snapshot=snapshot, ts=ts)
            sets = ', '.join(f'{k}=?' for k in changes)
            self.conn.execute(f'UPDATE nodes SET {sets}, updated_at=? WHERE id=?',
                              (*changes.values(), ts, node_id))
        return self.get(node_id)

    def _descendants_open(self, node_id):
        for kid in self._siblings(node_id):
            k = self.get(kid)
            if k['kind'] == 'task' and k['done_at'] is None:
                return True
            if self._descendants_open(kid):
                return True
        return False

    def _archive(self, node_id, ts):
        kids = self._siblings(node_id)
        self.conn.execute('UPDATE nodes SET archived_at=?, updated_at=? WHERE id=?', (ts, ts, node_id))
        self._log(node_id, 'archive', ts=ts)
        for kid in kids:
            self._archive(kid, ts)

    def _unarchive_chain(self, node_id, ts):
        n = self.get(node_id)
        if n['parent_id'] is not None:
            self._unarchive_chain(n['parent_id'], ts)
        if n['archived_at']:
            self.conn.execute('UPDATE nodes SET archived_at=NULL, updated_at=? WHERE id=?', (ts, node_id))
            sibs = [s for s in self._siblings(n['parent_id']) if s != node_id]
            self._place(node_id, n['parent_id'], sibs[-1] if sibs else None)
            self._log(node_id, 'restore', ts=ts)

    def _live_kids(self, node_id):
        return self.conn.execute('SELECT * FROM nodes WHERE parent_id=? AND archived_at IS NULL ORDER BY position, id',
                                 (node_id,)).fetchall()

    def _flip_done(self, node_id, done, ts, changed):
        self.conn.execute('UPDATE nodes SET done_at=?, updated_at=? WHERE id=?', (ts if done else None, ts, node_id))
        self._log(node_id, 'done' if done else 'undone', ts=ts)
        if not done:
            self._unarchive_chain(node_id, ts)
        changed.append(node_id)

    def set_done(self, node_id, done):
        """Mark a task done / not done. A task with sub-tasks is done exactly when all of them are:
        the change flows down to its sub-tasks and up through its task ancestors (a section
        heading stops it). The returned node carries `changed`: every id whose state flipped."""
        with self._tx():
            n = self.get(node_id)
            if n['kind'] != 'task':
                raise StoreError('only tasks can be done')
            done, ts, changed = bool(done), now_iso(), []
            if bool(n['done_at']) != done:
                self._flip_done(node_id, done, ts, changed)

            def down(nid):
                for k in self._live_kids(nid):
                    if k['kind'] == 'task' and bool(k['done_at']) != done:
                        self._flip_done(k['id'], done, ts, changed)
                    down(k['id'])
            down(node_id)
            p = n['parent_id']
            while p is not None:
                pn = self.get(p)
                if pn['kind'] != 'task':
                    break
                subs = [k for k in self._live_kids(p) if k['kind'] == 'task']
                if subs:
                    want = all(k['done_at'] for k in subs)
                    if bool(pn['done_at']) != want:
                        self._flip_done(p, want, ts, changed)
                p = pn['parent_id']
        out = self.get(node_id)
        out['changed'] = changed
        return out

    def set_done_many(self, ids, done):
        """Set exactly these tasks done / not done, with no propagation (used by undo). Returns the ids that flipped."""
        with self._tx():
            done, ts, changed = bool(done), now_iso(), []
            for nid in ids:
                n = self.get(nid)
                if n['kind'] == 'task' and bool(n['done_at']) != done:
                    self._flip_done(nid, done, ts, changed)
        return changed

    def split(self, node_id, at, text=None, parent_id=_UNSET, after_id=_UNSET):
        """Cut node text at `at`; the tail becomes a new task. `text` overrides the stored text."""
        with self._tx():
            n = self.get(node_id)
            full = n['text'] if text is None else text
            self._validate(text=full)
            at = max(0, min(int(at), len(full)))
            head, tail = full[:at], full[at:]
            if head != n['text']:
                ts = now_iso()
                self._log_text_edit(node_id, n['text'], head, ts)
                self.conn.execute('UPDATE nodes SET text=?, updated_at=? WHERE id=?', (head, ts, node_id))
            if parent_id is _UNSET:
                parent_id = n['parent_id']
            if after_id is _UNSET:
                after_id = node_id
            new_id = self._create(parent_id, after_id, 'task', tail, 'none', None, None, None, None, 'create')
        return self.get(node_id), self.get(new_id)

    def move(self, node_id, parent_id, after_id):
        with self._tx():
            n = self.get(node_id)
            p = parent_id
            while p is not None:
                if p == node_id:
                    raise StoreError('cannot move a node under itself')
                p = self.get(p)['parent_id']
            if after_id == node_id:
                raise StoreError('cannot place a node after itself')
            ts = now_iso()
            self.conn.execute('UPDATE nodes SET parent_id=?, updated_at=? WHERE id=?', (parent_id, ts, node_id))
            if n['parent_id'] != parent_id:
                self._renumber(self._siblings(n['parent_id']))
            self._place(node_id, parent_id, after_id)
            self._log(node_id, 'move', old=_s(n['parent_id']), new=_s(parent_id), ts=ts)
        return self.get(node_id)

    def delete(self, node_id, hard=False):
        """Hard-delete an empty node (promoting its children); otherwise archive the subtree.
        `hard=True` forces a hard delete (used by undo of a create)."""
        with self._tx():
            n = self.get(node_id)
            if hard or n['text'].strip() == '':
                kids = self._siblings(node_id)
                sibs = self._siblings(n['parent_id'])
                idx = sibs.index(node_id)
                self.conn.execute('UPDATE nodes SET parent_id=? WHERE parent_id=?', (n['parent_id'], node_id))
                self._renumber(sibs[:idx] + kids + sibs[idx + 1:])
                self.conn.execute('DELETE FROM history WHERE node_id=?', (node_id,))
                self.conn.execute('DELETE FROM nodes WHERE id=?', (node_id,))
                return {'id': node_id, 'hard': True}
            self._archive(node_id, now_iso())
            self._renumber(self._siblings(n['parent_id']))
            return {'id': node_id, 'hard': False}

    def restore(self, node_id, parent_id=_UNSET, after_id=_UNSET):
        """Bring an archived node (and everything archived with it) back; default place = end of its old parent."""
        with self._tx():
            n = self.get(node_id)
            if not n['archived_at']:
                return n
            ts = now_iso()
            if n['parent_id'] is not None:
                self._unarchive_chain(n['parent_id'], ts)
            stamp = n['archived_at']

            def revive(nid):
                self.conn.execute('UPDATE nodes SET archived_at=NULL, updated_at=? WHERE id=?', (ts, nid))
                self._log(nid, 'restore', ts=ts)
                kids = self.conn.execute('SELECT id FROM nodes WHERE parent_id=? AND archived_at=? ORDER BY position, id',
                                         (nid, stamp)).fetchall()
                for k in kids:
                    revive(k['id'])
                self._renumber(self._siblings(nid))
            revive(node_id)
            if parent_id is _UNSET:
                parent_id = n['parent_id']
            if after_id is _UNSET:
                sibs = [x for x in self._siblings(parent_id) if x != node_id]
                after_id = sibs[-1] if sibs else None
            if parent_id != n['parent_id']:
                self.conn.execute('UPDATE nodes SET parent_id=? WHERE id=?', (parent_id, node_id))
                self._renumber(self._siblings(n['parent_id']))
            self._place(node_id, parent_id, after_id)
        return self.get(node_id)

    def archive_done(self, before=None):
        """Archive done tasks (with their subtrees) that have no open descendants. Returns their ids."""
        with self._tx():
            sql = "SELECT id, parent_id FROM nodes WHERE archived_at IS NULL AND kind='task' AND done_at IS NOT NULL"
            args = []
            if before:
                sql += ' AND substr(done_at,1,10) < ?'
                args.append(before)
            rows = [dict(r) for r in self.conn.execute(sql, args)]
            ts = now_iso()
            ids = []
            parents = set()
            for r in rows:
                if self.get(r['id'])['archived_at'] or self._descendants_open(r['id']):
                    continue
                self._archive(r['id'], ts)
                ids.append(r['id'])
                parents.add(r['parent_id'])
            for p in parents:
                self._renumber(self._siblings(p))
        return ids

    # ------------------------------------------------------------ queries
    def path(self, node_id):
        """Texts of every ancestor, outermost first (sections and parent tasks alike)."""
        out = []
        pid = self.get(node_id)['parent_id']
        while pid is not None:
            p = self.get(pid)
            out.append(p['text'])
            pid = p['parent_id']
        out.reverse()
        return out

    def done_log(self, date_from=None, date_to=None):
        sql = 'SELECT * FROM nodes WHERE done_at IS NOT NULL'
        args = []
        if date_from:
            sql += ' AND substr(done_at,1,10) >= ?'
            args.append(date_from)
        if date_to:
            sql += ' AND substr(done_at,1,10) <= ?'
            args.append(date_to)
        sql += ' ORDER BY done_at DESC, id DESC'
        days = []
        for r in self.conn.execute(sql, args):
            n = dict(r)
            n['path'] = self.path(n['id'])
            day = n['done_at'][:10]
            if not days or days[-1]['day'] != day:
                days.append({'day': day, 'items': []})
            days[-1]['items'].append(n)
        return days

    def sweep_due(self, today=None):
        """Tag tasks whose due date has rolled into today/tomorrow (once per due date). Returns their ids."""
        today = today or datetime.date.today()
        limit = (today + datetime.timedelta(days=1)).isoformat()
        rows = self.conn.execute(
            "SELECT id, due_date FROM nodes WHERE kind='task' AND archived_at IS NULL AND done_at IS NULL "
            "AND due_date IS NOT NULL AND due_date <= ? AND (auto_urgent IS NULL OR auto_urgent != due_date)", (limit,)).fetchall()
        for r in rows:
            self.update(r['id'], due_date=r['due_date'])
        return [r['id'] for r in rows]

    def done_tree(self):
        """Every done task plus each of its ancestors (archived or not), as tree nodes."""
        out = {}
        for r in self.conn.execute("SELECT * FROM nodes WHERE done_at IS NOT NULL AND kind='task'"):
            n = dict(r)
            out[n['id']] = n
            pid = n['parent_id']
            while pid is not None:
                p = out.get(pid) or self.get(pid)
                out[pid] = p
                pid = p['parent_id']
        return sorted(out.values(), key=lambda n: (n['parent_id'] or 0, n['position'], n['id']))

    def search(self, q):
        rows = self.conn.execute(
            'SELECT * FROM nodes WHERE archived_at IS NULL AND text LIKE ? '
            'ORDER BY parent_id, position, id', (f'%{q}%',))
        return [dict(r) for r in rows]
