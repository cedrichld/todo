# Multi-day Due Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A task can be due on several days, picked with Ctrl+click / Shift+click in the calendar, and sits on Today on each of them; Today's list follows a date change without a reload.

**Architecture:** One new nullable column `due_days` (sorted ISO days, comma-separated, only for 2+ days). `due_date` stays and always equals the first day, so everything keyed on it (red tagging, sweep, old snapshots) is untouched. Server normalises; client reads days through one helper and never looks at `due_date` for display logic.

**Tech Stack:** stdlib Python + sqlite (`db.py`), vanilla JS (`static/app.js`), unittest, Playwright suite `tests/ui_playwright.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-16-multi-day-due-design.md`

## Global Constraints

- Zero new dependencies.
- A single-day task must look and behave exactly as before (existing 61 unit tests and browser checks stay green, unedited except where they assert the old `setDue` internals).
- Develop in a separate checkout; the live app (`systemctl --user restart todo`, port 5757, `data/todo.db`) is only touched in Task 5. Browser suite runs against a fixture db built with `import_gdoc.py data/gdoc-export-2026-08-26.html --db X --load`, never the live db.
- Day arithmetic in JS at local noon (`new Date(y, m, d, 12)`), never by adding 86400000 ms.

## Review Focus

1. A PATCH carrying only `due_date` (undo entries, the Today "new task" path, API scripts) on a multi-day task: a changed date makes it single-day; the same date leaves the days alone.
2. The daily red-tagging sweep re-saves `due_date`: it must not strip `due_days`.
3. One Ctrl+Z after a multi-day pick restores both columns to what they were.
4. A Shift run across a month boundary (Nov 28 → Dec 2) fills every day and reads as a run.
5. A database created before this change opens, gains the column, and its old snapshots / time travel still render.

---

### Task 1: The store holds several days

**Files:** Modify `db.py`, `export_md.py`; Test `tests/test_store.py`, `tests/test_export.py`

**Interfaces — produces:**
- Node dicts gain `due_days: str | None` — `'2026-11-17,2026-11-18,2026-11-20'`, sorted, unique, only when 2+ days. `due_date` == first day.
- `Store.update(id, due_days=<csv str | None>)`; `'due_days'` in `EDITABLE`.
- `db._days(n) -> list[str]` — every day of a node (`[]`, one, or many).

- [ ] **Step 1: failing tests** in a new `class MultiDayDue(StoreTestCase)` (`d(k)` = today + k days, ISO):
  - `test_days_are_sorted_deduped_and_first_is_due_date`: `update(due_days=f'{d(3)},{d(1)},{d(3)}')` → `due_days == f'{d(1)},{d(3)}'`, `due_date == d(1)`.
  - `test_one_day_or_none_collapses`: `due_days=d(2)` → `due_date == d(2)`, `due_days is None`; `due_days=''` → both `None`, `due_slot` `None`.
  - `test_bad_day_is_rejected`: `due_days='2026-11-17,soon'` raises `StoreError`.
  - `test_due_date_alone_makes_it_single_day_only_if_it_changes` (Review Focus 1): after `due_days=d(1),d(2)`: `update(due_date=d(1))` keeps the days; `update(due_date=d(5))` → `due_days is None`; `update(due_date=None)` → `due_days is None`.
  - `test_sweep_keeps_the_days` (Review Focus 2): write `due_date=d(1)`, `due_days=f'{d(1)},{d(2)}'` by SQL, `sweep_due()` → priority `urgent`, `due_days` unchanged.
  - `test_goes_red_once_for_the_first_day`: days `d(1),d(2),d(3)` → `urgent`; set priority `later`; `update(due_days=f'{d(1)},{d(2)},{d(3)},{d(4)}')` → still `later`.
  - `test_heading_drops_the_days`: `update(kind='heading')` → `due_days is None`.
  - `test_history_and_time_travel_carry_the_days`: the `due` history row's `new` is `f'{d(1)},{d(2)} morning'`; after a later change to `d(9)`, reconstructing the earlier day yields `due_days == f'{d(1)},{d(2)}'` (use the existing reconstruction test in the file as the pattern).
  - `test_overdue_counts_from_the_last_day`: days `d(-3),d(1)` → insights `totals.overdue == 0`; days `d(-3),d(-1)` → `1`.
  - `test_old_database_gains_the_column` (Review Focus 5): create a db whose `nodes` table lacks `due_days` (existing migrate test as pattern) → opens, `tree()` rows have `due_days is None`.
  - `tests/test_export.py::test_days_in_the_mirror`: run `2026-11-17,2026-11-18,2026-11-19` + slot → `@2026-11-17..2026-11-19/morning`; scattered → `@2026-11-17,2026-11-19`.
- [ ] **Step 2:** `python3 -m unittest discover -s tests` → the new tests fail, the rest pass.
- [ ] **Step 3: implement.** Column in `SCHEMA` + `_migrate`; `_days`; normalisation at the top of `update` (explicit `due_days` wins; `due_date` alone clears the days only when it changes the date); `_validate`; `_due_str` writes the comma list; replay parses it back; overdue uses `_days(n)[-1]`; heading side effect clears it; `export_md` tag.
- [ ] **Step 4:** full unit suite green.

### Task 2: Rows, Today and sorting read the days

**Files:** Modify `static/app.js`; Test `tests/ui_playwright.py`

**Interfaces — produces:** `daysOf(n) → string[]`; `dueDay(n, t = todayISO()) → string | null` (first day ≥ t, else the last day); `onToday(n, t)` (a picked day is t, or the last one has passed); `isOverdue(n)` (last day < today, not done).

- [ ] **Step 1: failing browser checks** (days set through `api_patch(id, {'due_days': ...})`, then reload):
  - run today..+2 → chip text `Today → <weekday of +2>`, chip has class `today`, not `overdue`; chip `title` lists three days.
  - scattered today, +2, +5 → `Today +2`.
  - days −2, today → on Today's "Due today" list; days −1, +1 → not on it and chip is not `overdue` (reads `Tomorrow`); days −3, −2 → on it, chip `overdue`.
  - Today tab badge count matches the list.
- [ ] **Step 2:** run the suite on Chromium → new checks fail.
- [ ] **Step 3: implement** the four helpers and route `chipText`, the chip classes and title, `dueBy` (and its copy in `tabCounts`), `urgencyKey` and the `kids-due` line through them.
- [ ] **Step 4:** suite green on Chromium.

### Task 3: Picking several days in the calendar

**Files:** Modify `static/app.js` (`duePicker`, `setDue` → `setDays(n, days, slot)`, heading conversion in `patchFields`), `static/style.css`; Test `tests/ui_playwright.py`

**Interfaces — consumes** Task 1's `due_days`, Task 2's `daysOf`/`dueDay`. `setDays` sends `{ due_date: days[0] ?? null, due_days: days.length > 1 ? days.join(',') : null, due_slot }` in one patch (so one undo step restores both).

Picker state: `anchor` (day a run grows from) and `base` (days as of the last non-Shift action). Click → `[day]`. Ctrl/Cmd+click → toggle. Shift+click / Shift+arrow → `base ∪ every day between anchor and target`. Space toggles the cursor day. Plain arrows move the cursor and re-anchor there. Enter: sets the cursor day as the only day only if the cursor was moved (or the picker just opened) and that day is not picked; then closes. Opens on `dueDay(n)` or today.

- [ ] **Step 1: failing browser checks:**
  - click today, Ctrl+click +2 → `due_days == today,+2`; Ctrl+click +2 again → back to one day, `due_days` null.
  - click today, Shift+click +3 → four days; Shift+click +1 → two days (the run's end moved).
  - Ctrl+click +6 then Shift+click +8 → the earlier days stay, +6..+8 added.
  - plain click on another day → only that day.
  - keyboard: open, Shift+ArrowRight ×2 → three days from the cursor; Space on a picked day removes it; Enter after that closes without changing the days.
  - one Ctrl+Z after a Shift+click restores the previous `due_date` and `due_days` (Review Focus 3).
  - a run from the 28th across the month end has one `.sel` per day after PageDown (Review Focus 4) — compute the expected days in Python.
  - picked days carry `.sel`; neighbours in a row are joined (`.sel.join-l` / `.join-r`).
  - Ctrl+Shift+H on a multi-day task clears `due_days`; Ctrl+Z brings them back.
- [ ] **Step 2:** new checks fail.
- [ ] **Step 3: implement.**
- [ ] **Step 4:** suite green on Chromium.

### Task 4: Today's list follows a date change

**Files:** Modify `static/app.js` (`openPopover`/`closePopover` gain an `onClose` hook; the due picker passes one); Test `tests/ui_playwright.py`

- [ ] **Step 1: failing browser checks:** on Today → "Due today", open the calendar on a task due today, click tomorrow: the row is still there while the calendar is open; Escape → the row is gone and the badge dropped by one, no reload. Same for a task typed into Today with Enter (pinned) and then re-dated. On "Everything else", dating a task today removes it from that list on close.
- [ ] **Step 2:** fails (this is the reported bug: the row stays until Ctrl+R).
- [ ] **Step 3: implement:** `setDays` un-pins the tasks it re-dates; when the due popover closes on a tab other than All, rebuild the view keeping focus if the focused row survives.
- [ ] **Step 4:** suite green.

### Task 5: Words, both browsers, live

**Files:** Modify `README.md` (Ctrl+D row), the in-app help table in `static/app.js`

- [ ] **Step 1:** README and help row describe Ctrl+click, Shift+click, Shift+arrows, Space.
- [ ] **Step 2:** unit suite + browser suite on Chromium and Firefox against a fresh fixture, all green.
- [ ] **Step 3:** bring the change into `~/dev/todo`, back up `data/todo.db`, `systemctl --user restart todo`, confirm `/api/tree` answers and rows carry `due_days`.
