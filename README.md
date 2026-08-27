# todo

Local todo list: outline editor with priorities, due dates, done log and history.
Stdlib Python + vanilla JS, no dependencies. Data lives in `data/todo.db` with a
readable mirror in `data/todo.md` regenerated on every change.

## Run

Always on (user service, starts at login):

    systemctl --user enable --now todo      # once
    systemctl --user restart todo           # after editing the code

Or by hand: `python3 server.py` → http://127.0.0.1:5757

Tests: `python3 -m unittest discover -s tests -v`

## Keys

| key | action |
|---|---|
| Enter | new item below (splits at cursor); on a heading: first child |
| Backspace on empty | delete item |
| Tab / Shift+Tab | nest / un-nest (works from the checkbox, dot or menu too) |
| Alt+↑ / Alt+↓ | move up / down (hops out of / into neighbouring sections) |
| Ctrl+/ | move to section… (type to filter, Enter picks) |
| Ctrl+Z / Ctrl+Y | undo / redo (Ctrl+Shift+Z also redoes) |
| ↑ / ↓ | previous / next item |
| Ctrl+Enter | done / not done (a task with sub-tasks is done exactly when all of them are: checking the last sub-task completes it, re-opening one re-opens it, checking the parent checks them all) |
| Ctrl+Shift+1 / 2 / 3 / 4 | urgent (red) / soon (orange) / normal (yellow) / later (blue) |
| Ctrl+Shift+0 | no priority |
| Ctrl+D | due date (calendar + morning / afternoon / evening) |
| Ctrl+. | notes on the item (emails, links, details). The first line shows in grey after the title; Ctrl+. or clicking it expands an editor under the row, Esc hides it. Notes are searchable and mirrored into `todo.md` as indented lines |
| Ctrl+B | waiting on someone / something — the row gets hatched with a ⏳ chip showing who and for how long; bump or clear from the same popover. The Waiting tab lists them oldest first |
| Ctrl+Shift+H | heading ↔ task |
| Ctrl+K | search |
| Esc | close popover / unfocus |

Click the dot for the priority picker (incl. custom color), the chip for due,
`⋯` for the menu (incl. "Move to section…"), drag `⋮⋮` to reorder — drop on the
lower half of a section title to put the item inside that section.

## Phone later

Install Tailscale on laptop + phone, then run with
`--host <laptop tailscale ip>` (edit `todo.service`) and open
`http://<laptop>:5757` on the phone. Nothing is exposed to the public internet.

## Import from Google Docs

File → Download → Web page (.html), then:

    python3 import_gdoc.py export.html --date YYYY-MM-DD          # preview
    python3 import_gdoc.py export.html --date YYYY-MM-DD --load   # write (empty db only; --force to append)

## Backup

`data/todo.db` (sqlite) and `data/todo.md` (readable). Copy either anywhere.
