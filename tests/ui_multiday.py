"""Browser checks for multi-day due dates (not run by unittest discover).

Run it like tests/ui_playwright.py, against a server on a throwaway db, never the live one:

    python3 server.py --db /tmp/ui.db --port 5799 &
    ~/.venvs/ml/bin/python tests/ui_multiday.py

Env: UI_PORT, BROWSER=chromium|firefox, UI_SHOTS. It makes its own section and tasks through the API,
then clicks and types like a user would and checks the API after each step.
"""
import asyncio, datetime, json, os, sys, tempfile, urllib.request
from playwright.async_api import async_playwright

SP = os.environ.get('UI_SHOTS', tempfile.gettempdir())  # where screenshots go
URL = f"http://127.0.0.1:{os.environ.get('UI_PORT', '5799')}/"
TODAY = datetime.date.today()
DAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def d(k):
    return (TODAY + datetime.timedelta(days=k)).isoformat()


def days(*ks):
    return ','.join(d(k) for k in ks)


def label(iso):
    """What the chip calls a day: Today, Tomorrow, a weekday within the week, else 'Nov 26'."""
    x = datetime.date.fromisoformat(iso); diff = (x - TODAY).days
    return 'Today' if diff == 0 else 'Tomorrow' if diff == 1 else DAY[x.weekday()] if 1 < diff < 7 else f'{MON[x.month - 1]} {x.day}'


def long_day(iso):
    x = datetime.date.fromisoformat(iso)
    return f'{DAY[x.weekday()]}, {MON[x.month - 1]} {x.day}'


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(), method=method, headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req))


def tree():
    return {n['id']: n for n in call('GET', 'api/tree')['nodes']}


def due(id):
    n = tree()[id]
    return (n['due_date'], n['due_days'])


def make(text, parent=None, kind='task', **fields):
    n = call('POST', 'api/nodes', {'parent_id': parent, 'text': text, 'kind': kind})
    return (call('PATCH', f"api/nodes/{n['id']}", fields) if fields else n)['id']


checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail != '' and not cond else ''))


async def main():
    h = make('Multi-day', kind='heading')
    A1 = make('run from today', h, due_days=days(0, 1, 2))
    A2 = make('scattered', h, due_days=days(0, 2, 5))
    A3 = make('started two days ago', h, due_days=days(-2, 0))
    A4 = make('between its days', h, due_days=days(-1, 1))
    A5 = make('past its last day', h, due_days=days(-3, -2))
    A6 = make('run from tomorrow', h, due_days=days(1, 2, 3), due_slot='morning')
    A7 = make('far run', h, due_days=days(10, 11, 12))
    P = make('parent', h); K = make('kid between its days', P, due_days=days(-1, 1))
    S2 = make('sort: later', h, due_days=days(-5, 3)); S1 = make('sort: sooner', h, due_date=d(1))
    B1 = make('pick me', h); B2 = make('keys', h); B3 = make('month end', h); B4 = make('becomes a heading', h, due_days=days(11, 12))
    B5 = make('goes red, then back', h, priority='later')
    C1 = make('leaves today', h, due_date=d(0)); C2 = make('stays today', h, due_date=d(0)); C3 = make('undated', h)
    async with async_playwright() as p:
        b = await getattr(p, os.environ.get('BROWSER', 'chromium')).launch()
        pg = await b.new_page(viewport={'width': 1100, 'height': 1300})
        pg.set_default_timeout(8000)
        page_errors, console_errors = [], []
        pg.on('pageerror', lambda e: page_errors.append(str(e)))
        pg.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
        await pg.goto(URL); await pg.wait_for_selector('.node')
        await pg.evaluate("localStorage.setItem('autoSort', '0')"); await pg.reload(); await pg.wait_for_selector('.node')  # rows stay where they were made

        chip = lambda id: pg.locator(f'.node[data-id="{id}"] > .row > .chip')
        async def chip_text(id): return await chip(id).text_content()
        async def chip_cls(id): return ((await chip(id).get_attribute('class')) or '').split()
        popover_open = lambda: pg.locator('#popover .cal-grid').is_visible()

        # ---------------------------------------------------------------- the row chip
        t = await chip_text(A1); check('a run from today reads "Today → last day"', t == f'Today → {label(d(2))}', t)
        c = await chip_cls(A1); check('...is marked as today, not overdue', 'today' in c and 'overdue' not in c, c)
        t = await chip(A1).get_attribute('title') or ''; check('hovering the chip lists every day', all(long_day(x) in t for x in (d(0), d(1), d(2))), t)
        t = await chip_text(A2); check('scattered days read "Today +2"', t == 'Today +2', t)
        t = await chip_text(A3); check('on its last day it just reads Today', t == 'Today', t)
        t = await chip_text(A4); c = await chip_cls(A4); check('between two of its days it names the next one and is not overdue', t == 'Tomorrow' and 'overdue' not in c and 'today' not in c, (t, c))
        x = datetime.date.fromisoformat(d(-2)); t = await chip_text(A5); c = await chip_cls(A5)
        check('past its last day it is overdue, showing that day', t == f'{MON[x.month - 1]} {x.day}' and 'overdue' in c, (t, c))
        t = await chip_text(A6); check('a run from tomorrow keeps its slot', t == f'Tomorrow → {label(d(3))} · morning', t)
        t = await chip_text(A7); check('a far run reads "date → date"', t == f'{label(d(10))} → {label(d(12))}', t)
        t = await pg.locator(f'.node[data-id="{P}"] > .row > .kids-due').text_content(); check("a parent's sub-task line names the next day ahead", 'Tomorrow' in t, t)
        await pg.click('#sort-btn'); await pg.wait_for_timeout(2500)
        order = await pg.evaluate("[...document.querySelectorAll('.node')].map(e => +e.dataset.id)")
        check('sorting goes by the next day ahead, not a day already past', order.index(S1) < order.index(S2), (order.index(S1), order.index(S2)))
        await pg.click('#sort-btn'); await pg.wait_for_timeout(400)

        # ---------------------------------------------------------------- the Today tab
        await pg.click('#tabs button[data-view="today"]'); await pg.wait_for_timeout(500)
        async def listed(id): return await pg.locator(f'#view .node[data-id="{id}"]:not(.ctx)').count() == 1
        async def badge(): return int(await pg.locator('#tabs button[data-view="today"] b').text_content())
        on = [await listed(x) for x in (A1, A2, A3, A5)]; check('Today lists a task on each picked day, and once its last day has passed', all(on), on)
        off = [await listed(x) for x in (A4, A6, A7, K)]; check('...but not between its days or before the first', not any(off), off)
        rows = await pg.locator('#view .node.task:not(.ctx)').count(); n = await badge(); check('the Today count matches the list', n == rows, (n, rows))

        # ---------------------------------------------------------------- picking days with the mouse
        await pg.click('#tabs button[data-view="all"]'); await pg.wait_for_timeout(400)
        async def cell(iso):  # the calendar button for a day, flipping months until it is on screen
            for _ in range(4):
                c = pg.locator(f'#popover .day[data-iso="{iso}"]')
                if await c.count(): return c
                first = await pg.locator('#popover .day').first.get_attribute('data-iso')
                await pg.click('#popover .prev' if iso < first else '#popover .next'); await pg.wait_for_timeout(150)
            raise AssertionError(f'no calendar day {iso}')
        async def open_cal(id):  # an undated row hides its chip until hovered: go through its title
            await pg.locator(f'.node[data-id="{id}"] > .row > .text').click(); await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300)
        async def pick(iso, *mods):
            await (await cell(iso)).click(modifiers=list(mods)); await pg.wait_for_timeout(400)
        await open_cal(B1)
        await pick(d(0)); check('a plain click picks that one day', due(B1) == (d(0), None), due(B1))
        await pick(d(2), 'Control'); check('Ctrl+click adds a day', due(B1) == (d(0), days(0, 2)), due(B1))
        check('...the calendar stays open with both days marked', await popover_open() and await pg.locator('#popover .day.sel').count() == 2, await pg.locator('#popover .day.sel').count())
        await pick(d(2), 'Control'); check('Ctrl+click on a picked day removes it', due(B1) == (d(0), None), due(B1))
        await pick(d(0)); await pick(d(3), 'Shift'); check('Shift+click fills every day up to it', due(B1) == (d(0), days(0, 1, 2, 3)), due(B1))
        await pick(d(1), 'Shift'); check('a second Shift+click moves the end of that run', due(B1) == (d(0), days(0, 1)), due(B1))
        await pick(d(6), 'Control'); await pick(d(8), 'Shift'); check('Shift+click after Ctrl+click keeps the earlier days', due(B1) == (d(0), days(0, 1, 6, 7, 8)), due(B1))
        joined = await pg.evaluate("""() => { const c = [...document.querySelectorAll('#popover .day')], sel = e => e && e.classList.contains('sel'); let ok = true, joins = 0;
          c.forEach((e, i) => { const r = sel(e) && sel(c[i + 1]) && i % 7 !== 6, l = sel(e) && sel(c[i - 1]) && i % 7 !== 0; joins += r;
            if (e.classList.contains('join-r') !== r || e.classList.contains('join-l') !== l) ok = false; });
          return [ok, joins]; }""")
        check('neighbouring picked days are joined into one bar', joined[0] and (joined[1] > 0 or len({datetime.date.fromisoformat(d(k)).isocalendar()[1] for k in (6, 7, 8)}) == 3), joined)
        t = await chip_text(B1); check('the row follows along while picking', t == 'Today +4', t)
        await pg.screenshot(path=f'{SP}/ui-multiday-picker.png')
        await pick(d(4)); check('a plain click goes back to one day', due(B1) == (d(4), None), due(B1))
        await pg.wait_for_timeout(1700)  # changes less than 1.5 s apart share one undo step (as date clicks always have); this one stands alone
        await pick(d(6), 'Shift'); await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)
        check('Escape closes the calendar, keeping the days', not await popover_open() and due(B1) == (d(4), days(4, 5, 6)), due(B1))
        await pg.keyboard.press('Control+z'); await pg.wait_for_timeout(600); check('one Ctrl+Z takes the run back', due(B1) == (d(4), None), due(B1))
        await pg.keyboard.press('Control+Shift+z'); await pg.wait_for_timeout(600); check('...and redo brings it back', due(B1) == (d(4), days(4, 5, 6)), due(B1))
        await open_cal(B1); await pick(d(8), 'Control'); await pick(d(9), 'Control'); await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)
        await pg.keyboard.press('Control+z'); await pg.wait_for_timeout(600); check('days picked in quick succession undo as one step', due(B1) == (d(4), days(4, 5, 6)), due(B1))

        await open_cal(B5); await pick(d(0)); await pick(d(1), 'Shift'); await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)
        n = tree()[B5]; check('a run starting today turns the task red', (n['due_days'], n['priority']) == (days(0, 1), 'urgent'), (n['due_days'], n['priority']))
        # two steps: the click that made it due today (and red), then the Shift+click that stretched it
        await pg.keyboard.press('Control+z'); await pg.wait_for_timeout(700); n = tree()[B5]
        check('Ctrl+Z takes back the stretch', (n['due_date'], n['due_days'], n['priority']) == (d(0), None, 'urgent'), (n['due_date'], n['due_days'], n['priority']))
        await pg.keyboard.press('Control+z'); await pg.wait_for_timeout(700); n = tree()[B5]; t = await pg.locator('#toast').text_content()
        check('Ctrl+Z again takes back the date and the red it brought', (n['due_date'], n['due_days'], n['priority'], n['auto_urgent']) == (None, None, 'later', None) and t.startswith('Undid'), (n['due_date'], n['priority'], t))
        await pg.keyboard.press('Control+Shift+z'); await pg.keyboard.press('Control+Shift+z'); await pg.wait_for_timeout(900); n = tree()[B5]
        check('...and redo puts both back', (n['due_days'], n['priority'], n['auto_urgent']) == (days(0, 1), 'urgent', d(0)), (n['due_days'], n['priority']))

        # ---------------------------------------------------------------- picking days from the keyboard
        cursor = lambda: pg.locator('#popover .day.cursor').get_attribute('data-iso')
        await pg.locator(f'.node[data-id="{B2}"] > .row > .text').click(); await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300)
        await pg.keyboard.press('Shift+ArrowRight'); await pg.keyboard.press('Shift+ArrowRight'); await pg.wait_for_timeout(500)
        check('Shift+arrows stretch a run from the cursor', due(B2) == (d(0), days(0, 1, 2)) and await cursor() == d(2), (due(B2), await cursor()))
        await pg.keyboard.press('Space'); await pg.wait_for_timeout(400); check('Space removes the day under the cursor', due(B2) == (d(0), days(0, 1)), due(B2))
        await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        check('Enter then closes without re-picking that day', not await popover_open() and due(B2) == (d(0), days(0, 1)) and await pg.evaluate('document.activeElement.textContent') == 'keys', due(B2))
        await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300); check('reopening starts on the next day ahead', await cursor() == d(0), await cursor())
        await pg.keyboard.press('ArrowDown'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        check('arrows then Enter still pick a single day', due(B2) == (d(7), None), due(B2))
        await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300)
        await pg.keyboard.press('Space'); await pg.wait_for_timeout(400); check('Space on the only day clears the date', due(B2) == (None, None), due(B2))
        await pg.keyboard.press('Space'); await pg.wait_for_timeout(400); check('Space again picks it', due(B2) == (d(7), None), due(B2))
        for k in ('ArrowRight', 'ArrowRight', 'Shift+ArrowRight'): await pg.keyboard.press(k)
        await pg.wait_for_timeout(400); check('after moving away, Shift+arrow adds a run from there', due(B2) == (d(7), days(7, 9, 10)), due(B2))
        await pg.keyboard.press('Shift+ArrowLeft'); await pg.wait_for_timeout(400); check('Shift+arrow back shrinks it', due(B2) == (d(7), days(7, 9)), due(B2))
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)

        # ---------------------------------------------------------------- a run across the month end
        nxt = (TODAY.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        span = [(nxt + datetime.timedelta(days=k)).isoformat() for k in range(-3, 2)]
        await open_cal(B3)
        await pick(span[0]); await pick(span[-1], 'Shift'); check('a Shift run across the month end fills every day', due(B3) == (span[0], ','.join(span)), due(B3))
        marked = "(span) => { const c = [...document.querySelectorAll('#popover .day')]; return c.filter(e => span.includes(e.dataset.iso)).every(e => e.classList.contains('sel')) && c.filter(e => e.classList.contains('sel')).every(e => span.includes(e.dataset.iso)); }"
        ok1 = await pg.evaluate(marked, span); await cell(span[-1]); await pg.click('#popover .next'); await pg.click('#popover .prev'); await pg.wait_for_timeout(200); ok2 = await pg.evaluate(marked, span)
        check('...and both months show it marked', ok1 and ok2 and await pg.locator('#popover .day.sel').count() >= 2, (ok1, ok2))
        t = await chip_text(B3); check('...and the chip reads it as one run', ' → ' in t and t.endswith(label(span[-1])), t)
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)

        # ---------------------------------------------------------------- headings carry no days
        await pg.locator(f'.node[data-id="{B4}"] > .row > .text').click(); await pg.keyboard.press('Control+Shift+h'); await pg.wait_for_timeout(500)
        n = tree()[B4]; check('turning a task into a heading drops its days', (n['kind'], n['due_date'], n['due_days']) == ('heading', None, None), (n['kind'], n['due_date'], n['due_days']))
        await pg.keyboard.press('Control+z'); await pg.wait_for_timeout(600)
        n = tree()[B4]; check('...and Ctrl+Z brings them back', (n['kind'], n['due_date'], n['due_days']) == ('task', d(11), days(11, 12)), (n['kind'], n['due_date'], n['due_days']))

        # ---------------------------------------------------------------- History spells the days out
        await pg.evaluate("setView('history')"); await pg.wait_for_selector('table.history'); await pg.wait_for_timeout(300)
        check('History lists the days of a change with room to wrap', await pg.locator('table.history .h-detail', has_text=days(4, 5, 6).replace(',', ', ')).count() >= 1)

        # ---------------------------------------------------------------- Today follows a date change without a reload
        await pg.click('#tabs button[data-view="today"]'); await pg.wait_for_timeout(500)
        row = lambda id: pg.locator(f'#view .node[data-id="{id}"]').count()
        n0 = await badge(); check('a task due today is on the list', await row(C1) == 1)
        await chip(C1).click(); await pg.wait_for_timeout(300); await pick(d(1))
        check('re-dated to tomorrow, the row waits while the calendar is open', await row(C1) == 1 and await popover_open() and due(C1) == (d(1), None), due(C1))
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(500)
        check('...and leaves Today when it closes, with no reload', await row(C1) == 0, await row(C1))
        n1 = await badge(); check('...the Today count drops by one', n1 == n0 - 1, (n0, n1))
        await pg.locator(f'.node[data-id="{C2}"] > .row > .text').click(); await pg.keyboard.press('End'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(500)
        await pg.keyboard.type('Typed on Today'); await pg.wait_for_timeout(800)
        T = next(n['id'] for n in tree().values() if n['text'] == 'Typed on Today')
        check('a task typed into Today is due today', due(T) == (d(0), None) and await row(T) == 1, due(T))
        await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300); await pg.keyboard.press('ArrowRight'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(600)
        check('...and once re-dated it leaves too', due(T) == (d(1), None) and await row(T) == 0, (due(T), await row(T)))
        await pg.locator('.view-switch button').nth(1).click(); await pg.wait_for_timeout(400)
        check('Everything else lists an undated task', await row(C3) == 1)
        await open_cal(C3); await pick(d(0)); await pg.mouse.click(900, 40); await pg.wait_for_timeout(500)
        check('...which leaves that list once dated today (closing by clicking away)', due(C3) == (d(0), None) and await row(C3) == 0 and not await popover_open(), (due(C3), await row(C3)))

        check('no uncaught errors in the page', not page_errors, page_errors[:3])
        await b.close()
        print('\nconsole errors:', len(console_errors)); [print('  ', e[:200]) for e in console_errors[:10]]
    fails = [n for n, ok in checks if not ok]
    print(f'\n{len(checks) - len(fails)}/{len(checks)} checks passed')
    if fails: print('FAILED:', fails); sys.exit(1)

asyncio.run(main())
