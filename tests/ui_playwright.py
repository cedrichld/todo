"""Browser regression suite (not run by unittest discover).

Start a server on a *copy* of the db, then run this with a Python that has Playwright:

    cp data/todo.db /tmp/ui.db && python3 server.py --db /tmp/ui.db --port 5799 &
    ~/.venvs/ml/bin/python tests/ui_playwright.py

It types into the page like a user would and checks the API after each keystroke.
"""
import asyncio, json, sys, urllib.request
from playwright.async_api import async_playwright
import os, tempfile
SP = os.environ.get('UI_SHOTS', tempfile.gettempdir())  # where screenshots go
URL = 'http://127.0.0.1:5799/'
def tree():
    nodes = json.load(urllib.request.urlopen(URL + 'api/tree'))['nodes']
    return {n['id']: n for n in nodes}
def by_text(t, text):
    return next(n for n in t.values() if n['text'] == text)
checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1100, 'height': 900})
        errors = []
        pg.on('console', lambda m: errors.append(f'{m.type}: {m.text}') if m.type in ('error', 'warning') else None)
        pg.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
        await pg.goto(URL); await pg.wait_for_selector('.node')
        logs = []; pg.on('console', lambda m: logs.append(f'{m.type}: {m.text}'))
        await pg.evaluate("""() => { for (const k of ['done','move','patch','split','del']) { const o = api[k]; api[k] = (...a) => { console.log('API', k, JSON.stringify(a)); return o(...a).then(r => r, e => { console.log('ERR', k, e.message); throw e; }); }; }
          document.addEventListener('change', e => console.log('CHANGE', e.target.className, e.target.checked, 'node', e.target.closest('.node')?.dataset.id), true);
          document.addEventListener('click', e => console.log('CLICK', e.target.className, 'node', e.target.closest('.node')?.dataset.id), true);
          document.addEventListener('drop', e => console.log('DROP', e.target.className), true); }""")
        await pg.screenshot(path=f'{SP}/ui-all.png')
        check('renders all nodes', await pg.locator('.node').count() == 113, await pg.locator('.node').count())
        # --- Enter splits / creates
        t = pg.locator('.node.task > .row > .text', has_text='Reach out Alex Rivera').first
        await t.click(); await pg.keyboard.press('End'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        await pg.keyboard.type('New item via Enter'); await pg.wait_for_timeout(600)
        tr = tree(); new = by_text(tr, 'New item via Enter'); first_row = by_text(tr, 'Reach out Alex Rivera')
        check('Enter creates sibling after', new['parent_id'] == first_row['parent_id'] and new['position'] == first_row['position'] + 1)
        check('focus on new item', await pg.evaluate('document.activeElement.textContent') == 'New item via Enter')
        # --- Tab / Shift+Tab
        await pg.keyboard.press('Tab'); await pg.wait_for_timeout(400)
        check('Tab nests under previous', tree()[new['id']]['parent_id'] == first_row['id'])
        check('focus kept after Tab', await pg.evaluate('document.activeElement.textContent') == 'New item via Enter')
        await pg.keyboard.press('Shift+Tab'); await pg.wait_for_timeout(400)
        check('Shift+Tab un-nests', tree()[new['id']]['parent_id'] == first_row['parent_id'])
        # --- Alt+Up
        await pg.keyboard.press('Alt+ArrowUp'); await pg.wait_for_timeout(400)
        check('Alt+Up moves above', tree()[new['id']]['position'] == first_row['position'])
        # --- priority + done + heading toggles
        await pg.keyboard.press('Control+Shift+Digit1'); await pg.wait_for_timeout(400)
        check('Ctrl+Shift+1 = urgent', tree()[new['id']]['priority'] == 'urgent')
        check('urgent renders red', 'urgent' == await pg.evaluate(f'document.querySelector(\'.node[data-id="{new["id"]}"]\').dataset.prio'))
        await pg.keyboard.press('Control+Shift+Digit4'); await pg.wait_for_timeout(400)
        check('Ctrl+Shift+4 = later', tree()[new['id']]['priority'] == 'later')
        await pg.keyboard.press('Control+Enter'); await pg.wait_for_timeout(400)
        check('Ctrl+Enter marks done', tree()[new['id']]['done_at'])
        check('done row styled', await pg.evaluate(f'document.querySelector(\'.node[data-id="{new["id"]}"]\').classList.contains("done")'))
        await pg.keyboard.press('Control+Enter'); await pg.wait_for_timeout(400)
        check('Ctrl+Enter again un-does', not tree()[new['id']]['done_at'])
        # --- due popover
        await pg.keyboard.press('Control+d'); await pg.wait_for_timeout(300)
        check('Ctrl+D opens calendar', await pg.locator('#popover .cal-grid').is_visible())
        await pg.locator('#popover .day.today').click(); await pg.wait_for_timeout(300)
        await pg.locator('#popover .slots button', has_text='Morning').click(); await pg.wait_for_timeout(400)
        n = tree()[new['id']]
        check('calendar sets today + morning', n['due_slot'] == 'morning' and n['due_date'], (n['due_date'], n['due_slot']))
        chip = await pg.locator(f'.node[data-id="{new["id"]}"] > .row > .chip').text_content()
        check('chip shows day AM', chip.endswith(' AM'), chip)
        await pg.screenshot(path=f'{SP}/ui-due.png')
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(200)
        check('Escape closes popover', await pg.locator('#popover').is_hidden())
        # --- heading toggle
        await pg.locator(f'.node[data-id="{new["id"]}"] > .row > .text').click()
        await pg.keyboard.press('Control+Shift+h'); await pg.wait_for_timeout(400)
        check('Ctrl+Shift+H makes heading', tree()[new['id']]['kind'] == 'heading' and tree()[new['id']]['due_date'] is None)
        await pg.keyboard.press('Control+Shift+h'); await pg.wait_for_timeout(400)
        check('… and back to task', tree()[new['id']]['kind'] == 'task')
        # --- Enter mid-text splits
        await pg.locator(f'.node[data-id="{new["id"]}"] > .row > .text').click()
        await pg.keyboard.press('Home'); await pg.keyboard.press('ArrowRight'); await pg.keyboard.press('ArrowRight'); await pg.keyboard.press('ArrowRight')
        await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        tr = tree(); check('Enter splits at caret', tr[new['id']]['text'] == 'New' and any(x['text'] == ' item via Enter' for x in tr.values()))
        tail = by_text(tr, ' item via Enter')
        # --- Backspace on empty deletes
        await pg.keyboard.press('End'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        cnt = len(tree()); await pg.keyboard.press('Backspace'); await pg.wait_for_timeout(400)
        check('Backspace on empty deletes', len(tree()) == cnt - 1)
        check('focus returns to previous', await pg.evaluate('document.activeElement.textContent') == ' item via Enter')
        # --- arrow navigation
        await pg.keyboard.press('ArrowUp'); await pg.wait_for_timeout(100)
        check('ArrowUp moves to previous item', await pg.evaluate('document.activeElement.textContent') == 'New')
        # --- typing autosaves
        await pg.keyboard.press('End'); await pg.keyboard.type(' typed'); await pg.wait_for_timeout(700)
        check('typing autosaves', tree()[new['id']]['text'] == 'New typed')
        # --- mouse: checkbox, dot picker, menu
        logs.append('--- checkbox step, tail id ' + str(tail['id']))
        await pg.locator(f'.node[data-id="{tail["id"]}"] > .row > .check').click(); await pg.wait_for_timeout(400)
        check('checkbox marks done', tree()[tail['id']]['done_at'])
        logs.append('--- end checkbox step; tail now ' + json.dumps({k: tree()[tail['id']][k] for k in ('text','done_at','parent_id','position')}))
        await pg.locator(f'.node[data-id="{new["id"]}"] > .row > .dot').click(); await pg.wait_for_timeout(200)
        await pg.locator('#popover .swatch[data-prio=soon]').click(); await pg.wait_for_timeout(400)
        check('dot picker sets soon', tree()[new['id']]['priority'] == 'soon')
        await pg.locator(f'.node[data-id="{new["id"]}"] > .row').hover()
        await pg.locator(f'.node[data-id="{new["id"]}"] > .row > .menu').click(); await pg.wait_for_timeout(200)
        check('menu opens', await pg.locator('#popover .menu-list').is_visible())
        await pg.screenshot(path=f'{SP}/ui-menu.png')
        await pg.locator('#popover .menu-list button', has_text='Move down').click(); await pg.wait_for_timeout(400)
        check('menu move down', tree()[new['id']]['position'] == tree()[tail['id']]['position'] + 1, (tree()[new['id']]['position'], tree()[tail['id']]['position']))
        # --- Enter on a heading creates its first child
        await pg.locator('.node.heading > .row > .text', has_text='Career').first.click(); await pg.keyboard.press('End'); await pg.keyboard.press('Enter'); await pg.wait_for_timeout(400)
        await pg.keyboard.type('first career task'); await pg.wait_for_timeout(600)
        tr = tree(); check('Enter on heading creates first child', by_text(tr, 'first career task')['parent_id'] == by_text(tr, 'Career')['id'])
        # --- fold
        fold_node = by_text(tree(), 'Reach out to ppl')
        await pg.locator(f'.node[data-id="{fold_node["id"]}"] > .row > .fold').click(); await pg.wait_for_timeout(400)
        check('fold hides children', await pg.locator(f'.node[data-id="{fold_node["id"]}"] .kids .node').count() == 0 and tree()[fold_node['id']]['collapsed'] == 1)
        await pg.locator(f'.node[data-id="{fold_node["id"]}"] > .row > .fold').click(); await pg.wait_for_timeout(400)
        check('unfold shows children', await pg.locator(f'.node[data-id="{fold_node["id"]}"] .kids .node').count() == 4)
        # --- search
        await pg.keyboard.press('Control+k'); await pg.keyboard.type('jo'); await pg.wait_for_timeout(400)
        check('search shows match + its ancestors only', await pg.locator('#view .node.task').count() == 2 and await pg.locator('#view .node.task > .row > .text', has_text='jo').count() == 1, await pg.locator('#view .node.task').count())
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(300)
        check('Escape clears search', await pg.locator('#view .node').count() > 100)
        # --- views
        await pg.click('#tabs button[data-view=today]'); await pg.wait_for_timeout(400)
        groups = await pg.locator('h2.group').count(); items = await pg.locator('#view .node').count()
        check('Today view groups by section', groups >= 3 and items >= 17, (groups, items))
        await pg.screenshot(path=f'{SP}/ui-today.png')
        await pg.click('#tabs button[data-view=done]'); await pg.wait_for_timeout(600)
        check('Done view lists today', await pg.locator('.log-row', has_text=' item via Enter').count() == 1)
        await pg.screenshot(path=f'{SP}/ui-done.png')
        await pg.click('#tabs button[data-view=history]'); await pg.wait_for_timeout(600)
        rows = await pg.locator('table.history tr').count()
        check('History view has rows', rows > 113, rows)
        first = await pg.locator('table.history tr').first.text_content()
        await pg.screenshot(path=f'{SP}/ui-history.png')
        await pg.click('#tabs button[data-view=all]'); await pg.wait_for_timeout(400)
        # --- hide done + archive done
        await pg.click('#hide-done'); await pg.wait_for_timeout(300)
        check('Hide done hides', await pg.locator('#view .node.done').count() == 0)
        await pg.click('#hide-done'); await pg.wait_for_timeout(300)
        await pg.click('#archive-done'); await pg.wait_for_timeout(600)
        check('Archive done removes done', all(not n['done_at'] for n in tree().values()))
        # --- drag: drag 'IROS Book' handle onto 'PhD Apps think through' row (after)
        src = by_text(tree(), 'IROS Book'); dst = by_text(tree(), 'PhD Apps think through')
        await pg.locator(f'.node[data-id="{src["id"]}"] > .row').hover()
        h = pg.locator(f'.node[data-id="{src["id"]}"] > .row > .handle'); d = pg.locator(f'.node[data-id="{dst["id"]}"] > .row')
        rh = (await d.bounding_box())['height']; check('task rows are single-line', rh < 34, rh)
        logs.append('--- drag step')
        await pg.drag_and_drop(f'.node[data-id="{src["id"]}"] > .row > .handle', f'.node[data-id="{dst["id"]}"] > .row', target_position={'x': 200, 'y': rh - 4}); await pg.wait_for_timeout(500)
        check('drag reorders', tree()[src['id']]['position'] > tree()[dst['id']]['position'], (tree()[src['id']]['position'], tree()[dst['id']]['position']))
        check('status dot saved', await pg.evaluate('document.querySelector("#status").className') == 'saved')
        await pg.screenshot(path=f'{SP}/ui-final.png')
        # --- mobile viewport sanity
        await pg.set_viewport_size({'width': 390, 'height': 800}); await pg.wait_for_timeout(300)
        check('no horizontal scroll on phone width', await pg.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'))
        await pg.screenshot(path=f'{SP}/ui-phone.png')
        await b.close()
        print('\n'.join(l for l in logs if not l.startswith('log: API patch')))
        print('\nconsole errors:', len(errors)); [print('  ', e[:200]) for e in errors[:15]]
        print('first history row:', first[:120] if first else None)
        fails = [n for n, ok in checks if not ok]
        print(f'\n{len(checks) - len(fails)}/{len(checks)} checks passed'); 
        if fails: print('FAILED:', fails)
asyncio.run(main())
