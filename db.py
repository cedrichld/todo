"""SQLite store for the todo tree. Stdlib only.

Every public write runs in one transaction, appends history rows, then
regenerates the markdown mirror when `md_path` is set.
"""
import contextlib
import datetime
import re
import sqlite3
import threading

import export_md

PRIORITIES = ('urgent', 'soon', 'normal', 'later', 'none')
SLOTS = ('morning', 'afternoon', 'evening')
KINDS = ('heading', 'task')
EDITABLE = ('text', 'priority', 'color', 'due_date', 'due_slot', 'kind', 'collapsed')
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
"""


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def _due_str(n):
    return ' '.join(x for x in (n.get('due_date'), n.get('due_slot')) if x)


def _s(v):
    return '' if v is None else str(v)


class StoreError(ValueError):
    """Bad input from a caller (maps to HTTP 400)."""


class Store:
    def __init__(self, path, md_path=None):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript(SCHEMA)
        self.md_path = md_path
        self.lock = threading.RLock()

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

    def _log_text_edit(self, node_id, old, new, ts):
        """Coalesce a burst of keystrokes into one history row."""
        last = self.conn.execute(
            'SELECT * FROM history WHERE node_id=? ORDER BY id DESC LIMIT 1', (node_id,)).fetchone()
        if last is not None and last['action'] == 'edit' and last['field'] == 'text':
            age = (datetime.datetime.fromisoformat(ts) - datetime.datetime.fromisoformat(last['ts'])).total_seconds()
            if 0 <= age <= TEXT_COALESCE_SECONDS:
                self.conn.execute('UPDATE history SET new=?, snapshot=?, ts=? WHERE id=?',
                                  (new, new, ts, last['id']))
                return
        self._log(node_id, 'edit', field='text', old=old, new=new, snapshot=new, ts=ts)

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
                if old[k] != v:
                    changes[k] = v
            auto = set()  # fields cleared as a side effect of becoming a heading; not logged
            if changes.get('kind') == 'heading':
                for k, v in (('priority', 'none'), ('due_date', None), ('due_slot', None), ('done_at', None)):
                    if old[k] != v:
                        changes[k] = v
                        auto.add(k)
            if not changes:
                return old
            snapshot = changes.get('text', old['text'])
            for k, v in changes.items():
                if k in ('collapsed', 'done_at', 'due_date', 'due_slot') or k in auto:
                    continue
                if k == 'text':
                    self._log_text_edit(node_id, old['text'], v, ts)
                else:
                    self._log(node_id, 'edit', field=k, old=_s(old[k]), new=_s(v), snapshot=snapshot, ts=ts)
            if ('due_date' in changes or 'due_slot' in changes) and not auto & {'due_date', 'due_slot'}:
                self._log(node_id, 'edit', field='due', old=_due_str(old),
                          new=_due_str({**old, **changes}), snapshot=snapshot, ts=ts)
            sets = ', '.join(f'{k}=?' for k in changes)
            self.conn.execute(f'UPDATE nodes SET {sets}, updated_at=? WHERE id=?',
                              (*changes.values(), ts, node_id))
        return self.get(node_id)
