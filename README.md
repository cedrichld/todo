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
| Tab / Shift+Tab | nest / un-nest |
| Alt+↑ / Alt+↓ | move up / down |
| ↑ / ↓ | previous / next item |
| Ctrl+Enter | done / not done |
| Ctrl+Shift+1 / 2 / 3 / 4 | urgent (red) / soon (orange) / normal (yellow) / later (blue) |
| Ctrl+Shift+0 | no priority |
| Ctrl+D | due date (calendar + morning / afternoon / evening) |
| Ctrl+Shift+H | heading ↔ task |
| Ctrl+K | search |
| Esc | close popover / unfocus |

Click the dot for the priority picker (incl. custom color), the chip for due,
`⋯` for the menu, drag `⋮⋮` to reorder.

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
