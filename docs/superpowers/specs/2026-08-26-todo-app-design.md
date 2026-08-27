# Todo app — design spec (2026-08-26)

## Goal

Replace a Google Doc todo list with a local web app that keeps the Doc's feel
(type-anywhere outline, colors for priority, nested sections) and adds what
the Doc cannot: checkboxes, created/completed timestamps, a done log, a change
history, due dates, and a Today view. Laptop-only for v1, phone later via
Tailscale with no app changes.

## Non-goals (v1)

- Multi-user, accounts, auth, public hosting
- Rich text inside an item (bold runs, links); items are plain text
- Recurring tasks, reminders, notifications
- Mobile-specific UI (it should merely not break on a phone)

## Constraints

- Ubuntu 24.04, Python 3.12, disk is tight → **zero dependencies**: Python
  stdlib only (`http.server`, `sqlite3`), vanilla HTML/JS/CSS, no build step.
- Bound to `127.0.0.1` only.
- Data must remain readable without the app (markdown mirror on every save).

## Architecture

```
~/dev/todo/
  server.py            # stdlib HTTP server: static files + JSON API + sqlite
  static/index.html
  static/app.js        # outline editor, views, keyboard handling
  static/style.css
  import_gdoc.py       # one-shot: Google Docs HTML export → nodes (dry-run + load)
  export_md.py         # markdown mirror (also called by server on every write)
  tests/               # unittest
  data/todo.db         # sqlite (gitignored)
  data/todo.md         # markdown mirror, regenerated on every write (gitignored)
  todo.service         # systemd --user unit
```

`python3 server.py` serves `http://127.0.0.1:5757`. Port overridable with
`--port`, bind address with `--host` (for Tailscale later).

## Data model (sqlite)

```sql
CREATE TABLE nodes (
  id          INTEGER PRIMARY KEY,
  parent_id   INTEGER REFERENCES nodes(id),   -- NULL = root
  position    INTEGER NOT NULL,               -- order among siblings
  kind        TEXT NOT NULL CHECK (kind IN ('heading','task')),
  text        TEXT NOT NULL DEFAULT '',
  priority    TEXT NOT NULL DEFAULT 'none'
              CHECK (priority IN ('urgent','soon','normal','later','none')),
  color       TEXT,                            -- optional custom hex, overrides priority color
  due_date    TEXT,                            -- ISO date YYYY-MM-DD
  due_slot    TEXT CHECK (due_slot IN ('morning','afternoon','evening')),
  collapsed   INTEGER NOT NULL DEFAULT 0,
  done_at     TEXT,                            -- ISO datetime, NULL = open
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  archived_at TEXT                             -- NULL = visible
);
CREATE TABLE history (
  id       INTEGER PRIMARY KEY,
  node_id  INTEGER NOT NULL,
  ts       TEXT NOT NULL,
  action   TEXT NOT NULL,   -- create | edit | done | undone | move | archive | delete | import
  field    TEXT,            -- for edit: text | priority | color | due | kind
  old      TEXT,
  new      TEXT,
  snapshot TEXT NOT NULL    -- node text at the time, so history stays readable after edits
);
```

Priority colors (CSS): `urgent` red, `soon` orange, `normal` yellow, `later`
blue, `none` default text. `color` (custom hex) wins over `priority` for
rendering only; priority still drives sorting/Today view.

Heading size is derived from depth: depth 0 → H1 style, depth 1 → H2, deeper →
H3. Headings have no checkbox, priority, or due date.

Delete is soft for tasks with history (`archived_at`) so history stays
consistent; an empty freshly-created node deleted via Backspace is hard-deleted
with its history rows.

## HTTP API (JSON)

| method | path | purpose |
|---|---|---|
| GET  | `/api/tree` | all non-archived nodes as a flat list ordered by (parent, position) |
| POST | `/api/nodes` | create `{parent_id, after_id?, kind, text}` → node |
| PATCH| `/api/nodes/{id}` | update any of `text, priority, color, due_date, due_slot, kind, collapsed` |
| POST | `/api/nodes/{id}/done` | body `{done: bool}` |
| POST | `/api/nodes/{id}/move` | body `{parent_id, after_id|null}` |
| POST | `/api/nodes/{id}/split` | body `{at: int}` → creates sibling with the tail text, returns both |
| DELETE | `/api/nodes/{id}` | archive (soft) or hard-delete if empty and never edited |
| POST | `/api/archive-done` | archive all done nodes (optionally `before` date) |
| GET  | `/api/done?from=&to=` | done nodes grouped by day |
| GET  | `/api/history?q=&limit=` | history rows newest first, with node snapshot |
| GET  | `/api/search?q=` | text search over non-archived nodes |

Every write: one sqlite transaction, a history row, then regenerate
`data/todo.md`. Responses return the updated node(s) so the client can
reconcile without refetching the tree.

## Frontend

Single page. State = flat node map + children index, rendered as a tree of
`div.node` rows. Each row: fold arrow (if children) · checkbox (tasks) ·
priority dot · `contenteditable` text span · due chip · `⋯` menu.

### Keyboard (when a text span is focused)

| key | action |
|---|---|
| Enter | split at caret → new sibling below, focus it |
| Backspace on empty | delete node, focus previous visible node at end |
| Tab / Shift+Tab | indent under previous sibling / outdent after parent |
| Alt+↑ / Alt+↓ | swap with previous / next sibling |
| ↑ / ↓ at line edge | move focus to previous / next visible node |
| Ctrl+Enter | toggle done |
| Ctrl+1 / 2 / 3 / 4 | urgent / soon / normal / later; Ctrl+0 → none |
| Ctrl+D | open due popover |
| Ctrl+Shift+H | toggle heading ↔ task |
| Ctrl+K | focus search |
| Escape | close popover / blur |

Mouse: fold arrow, checkbox, priority dot → picker (4 presets + custom `<input
type=color>` + clear), due chip → popover, `⋯` menu (indent, outdent, move up,
move down, heading/task, archive, delete), drag handle for reorder.

### Due popover

Month calendar (prev/next month, today highlighted, click a day) + three
buttons Morning / Afternoon / Evening + Clear. Chip renders as `Thu AM`,
`Wed PM`, `Wed eve`, or `Aug 30` when no slot; overdue (date < today and not
done) → red chip; today → bold chip.

### Views (top bar tabs)

- **All** — the outline. Toggle **Hide done**. Button **Archive done**.
- **Today** — tasks due today or overdue, plus all `urgent` tasks, grouped
  under their nearest heading path (e.g. `Lab › Racer - Conf 2026`). Done
  toggle works here too.
- **Done** — done tasks grouped by completion day, newest first, with the
  heading path.
- **History** — history rows newest first, text filter, showing
  `time · action · snapshot · old → new`.

### Saving

Edits are sent immediately (structural ops) or debounced 300 ms (text). A
status dot in the top bar shows saving / saved / error; on error the client
keeps retrying and never drops the local edit.

## Import (`import_gdoc.py`)

Input: the Google Docs HTML export (`text/html`). Output: nodes.

- `h1`/`h2` → headings at depth 0/1 under the previous h1. Bold `p` lines that
  act as labels (`Later:`, `Long term:`) → depth-appropriate heading.
- `li` nesting (from the Docs `lst-kix_*-N` class suffix) → task depth under
  the current heading, up to 3 levels.
- Colors: `#ff0000` bold → `urgent`; `#ff9900` → `soon`; `#b45f06` (brown),
  `#bf9000` (dark yellow) → `normal`; all other non-black colors
  (`#cccccc`, `#d60fd6`, `#783f04`, …) → `none` with `color` set to the hex.
- Day tags in parentheses: `(Wed night)` → `2026-08-26` evening; `(Thurs)` →
  `2026-08-27`; `(Thurs, Fri)` → `2026-08-27`, text keeps ` (Fri)`;
  `(Wed night?)` → same as `(Wed night)`. Day names resolve to the next
  occurrence on or after the doc date 2026-08-26. Tag is stripped from text.
- `✅` in text or strikethrough → `done_at` = import time; `✅` removed from text.
  `🔄` left in text.
- Dropped: the `=====` divider heading, empty list items, the title paragraph
  (`TODO LIST Aug 26/26`), and the five trailing prose paragraphs about
  autograding (not tasks).
- Every imported node gets a history row `import`.

`--dry-run` prints the tree with priority/due/done markers for review;
`--load` writes to the db (refuses if the db already has nodes unless
`--force`).

## Markdown mirror (`data/todo.md`)

Headings as `#`/`##`/`###`, tasks as `- [ ]` / `- [x]` with 2-space nesting,
priority as a trailing `!urgent` style tag, due as `@2026-08-27/morning`,
done items get `(done 2026-08-27)`. Regenerated on every write; never read
back by the app.

## Running

`todo.service` (systemd user unit): `ExecStart=/usr/bin/python3
%h/dev/todo/server.py`, `Restart=on-failure`, enabled with
`systemctl --user enable --now todo`. Bookmark `http://127.0.0.1:5757`.

Phone later: install Tailscale on both devices, run with `--host <tailscale
ip>` (or `0.0.0.0` behind Tailscale only), open `http://<laptop>:5757`.

## Testing

- `tests/test_api.py` — spins the server on a temp db: create / split /
  patch / move / done / archive / hard-delete rules / history rows / markdown
  regeneration / Today & Done queries.
- `tests/test_import.py` — parser against a fixture built from the real
  export: nesting, color → priority/custom mapping, day-tag parsing (incl.
  `night`, `?`, `Thurs, Fri`), ✅/strike → done, dropped content.
- Frontend: verified manually in a browser against the keyboard table above.
