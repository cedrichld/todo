# Multi-day due dates — short design

Approved on 2026-11-16 ("Several due days" picked from a side-by-side sketch).

## Why

A task that takes several days has one due date today. From day two it reads as overdue, so it
gets re-dated to "today" every morning just to stay tidy on the Today tab. A task should carry
every day it is planned for, and the row should say so at a glance.

Reported in the same breath: on the Today tab, a task whose date is moved off today stays listed
until the page is reloaded.

## Goals

- Pick several days for one task in the calendar: Ctrl+click adds or removes a day, Shift+click
  fills every day up to the clicked one.
- The task is on Today on each picked day, without touching it again.
- Today's list follows a date change without a reload.

Non-goals: a deadline separate from the picked days; a slot (morning/afternoon/evening) per day;
ticking a task off "for today" only.

Anti-goal: anything that changes how a single-day task looks or behaves.

## Approaches

1. **Several due days (picked).** The due date becomes a set of days. One concept, same calendar,
   matches the request.
2. Work days plus a separate deadline. More expressive ("on Today Mon–Wed, due Fri") but two kinds
   of date to set and read; not asked for.
3. Start day + end day. Cannot hold scattered days, which Ctrl+click is for. Dropped.

## Behaviour (as approved)

- **Calendar.** Click: only that day (as now). Ctrl+click: add / remove a day. Shift+click: every
  day from the last clicked day up to this one, on top of what was already picked; a second
  Shift+click moves the end of that same run. Keyboard: Shift+arrows stretch the run, Space adds /
  removes the day under the cursor, Enter and Escape as now.
- **Today tab.** Listed on each picked day only, not on the days in between. Once the last picked
  day has passed and the task is still open, it is overdue and listed (as overdue tasks are now).
- **Chip.** `Today → Thu` for a run of days, `Today +2` for scattered days (hover lists them all),
  the last day in red once overdue. It names the next day still ahead, so on Wednesday a Mon–Thu
  task reads `Today → Tomorrow`.
- **Goes red** from the day before the first picked day, once; a colour picked afterwards sticks.
- **Slot** stays one per task.
- **Today refresh.** The list is rebuilt when the calendar closes (not on every click, so the row
  does not vanish while days are still being picked).

## Builder's calls (not part of what was approved)

- Storage: a new nullable column `due_days` holding the sorted days comma-separated, set only when
  a task has two or more days. `due_date` stays and always equals the first day, so single-day
  tasks, the red-tagging sweep and old snapshots are untouched. The store normalises: `due_days`
  wins when sent; a `due_date` sent alone that changes the date makes the task single-day again.
- History and the markdown mirror write a run as `2026-11-17..2026-11-19` (mirror) and the plain
  comma list (history, so time travel can replay it).
- Sorting ("earlier due first"), the parent's "sub-tasks due" line and Insights' overdue count use
  the next day still ahead, or the last day once all have passed.
- Re-dating a task you just typed into Today un-pins it, so it leaves the list like any other.
- Enter picks the day under the cursor only when nothing was picked in that sitting (the old "arrows, then Enter" way of
  setting one date). Once days were clicked, toggled or stretched, Enter just closes and keeps them.
- Shift+click always runs from the last day clicked; Shift+arrows, after the cursor was walked away, run from where it stands.
- After review (2026-11-16): the calendar reads the task afresh on every action, since the list can be refetched while it is
  open; a row that leaves Today stops receiving keys; a selection re-dated from any of its rows refreshes Today.
