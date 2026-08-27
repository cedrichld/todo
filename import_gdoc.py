#!/usr/bin/env python3
"""One-shot import of a Google Docs HTML export into the todo store.

    python3 import_gdoc.py doc.html                 # dry run: print the parsed tree
    python3 import_gdoc.py doc.html --load          # write into data/todo.db (refuses if non-empty)
    python3 import_gdoc.py doc.html --load --force  # append even if the db has nodes
Options: --db PATH, --date YYYY-MM-DD (the doc's "today", used to resolve day tags)
"""
import argparse
import datetime
import os
import re
from html.parser import HTMLParser

DAY_INDEX = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
DAY_TAG = re.compile(r'\s*\((?P<body>(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b[^()]*)\)', re.I)
COLOR_PRIORITY = {'#ff0000': 'urgent', '#ff9900': 'soon', '#b45f06': 'normal', '#bf9000': 'normal'}
NEUTRAL_COLORS = {'#000000', '#181818', '#1f1f1f', '#ffffff'}
BLOCK_TAGS = {'h1', 'h2', 'h3', 'h4', 'p', 'li'}


def parse_day_tag(text, base_date):
    """Turn a trailing/inline '(Thurs)' / '(Wed night)' / '(Thurs, Fri)' tag into a due date + slot."""
    m = DAY_TAG.search(text)
    if not m:
        return text, None, None
    parts = [p.strip() for p in m.group('body').replace('?', '').split(',') if p.strip()]
    words = parts[0].split()
    day = DAY_INDEX[words[0].lower()[:3]]
    due = base_date + datetime.timedelta(days=(day - base_date.weekday()) % 7)
    rest = {w.lower() for w in words[1:]}
    slot = None
    if rest & {'night', 'evening', 'eve', 'tonight'}:
        slot = 'evening'
    elif rest & {'morning', 'am'}:
        slot = 'morning'
    elif rest & {'afternoon', 'pm', 'noon'}:
        slot = 'afternoon'
    clean = (text[:m.start()] + ' ' + text[m.end():]).strip()
    clean = re.sub(r'\s{2,}', ' ', clean)
    if len(parts) > 1:
        clean += ' (' + ', '.join(parts[1:]) + ')'
    return clean, due.isoformat(), slot


class _Blocks(HTMLParser):
    """Flatten the export into blocks: headings, paragraphs and list items with their span styles."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.cur = None
        self.level = 0
        self.styles = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('ol', 'ul'):
            m = re.search(r'lst-kix_\S+?-(\d+)', a.get('class', ''))
            self.level = int(m.group(1)) if m else 0
        elif tag in BLOCK_TAGS:
            self.cur = {'tag': tag, 'level': self.level, 'spans': []}
        elif tag == 'span' and self.cur is not None:
            self.styles.append(a.get('style', ''))
        elif tag == 'br' and self.cur is not None:
            self.cur['spans'].append((' ', ''))

    def handle_endtag(self, tag):
        if tag == 'span' and self.styles:
            self.styles.pop()
        elif tag in BLOCK_TAGS and self.cur is not None:
            self.blocks.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None:
            self.cur['spans'].append((data, self.styles[-1] if self.styles else ''))


def parse_html(html):
    p = _Blocks()
    p.feed(html)
    p.close()
    return p.blocks


def _style_of(spans):
    """(color, bold, strike) — color/bold from the first non-blank span, strike from any."""
    color, bold, strike, seen = None, False, False, False
    for text, style in spans:
        if 'line-through' in style:
            strike = True
        if not seen and text.strip():
            seen = True
            m = re.search(r'(?<![-\w])color:(#[0-9a-fA-F]{6})', style)
            if m:
                color = m.group(1).lower()
            bold = 'font-weight:700' in style
    return color, bold, strike


def _clean(text):
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()


def convert(blocks, base_date):
    """Blocks -> (nodes, dropped). Each node: parent index, kind, text, priority, color, due, done."""
    nodes, dropped = [], []
    h1 = h2 = None
    stack = []  # index of the last task at each list level

    def add(parent, kind, text, **kw):
        node = {'parent': parent, 'kind': kind, 'text': text, 'priority': 'none', 'color': None,
                'due_date': None, 'due_slot': None, 'done': False}
        node.update(kw)
        nodes.append(node)
        return len(nodes) - 1

    for b in blocks:
        text = _clean(''.join(t for t, _ in b['spans']))
        if not text:
            continue
        tag = b['tag']
        if tag in ('h1', 'h2', 'h3', 'h4'):
            if re.fullmatch(r'[=\-_]+', text):
                continue
            text = text.rstrip(':').strip()
            if tag == 'h1':
                h1, h2 = add(None, 'heading', text), None
            else:
                h2 = add(h1, 'heading', text)
            stack = []
        elif tag == 'p':
            if text.endswith(':') and len(text) < 40:
                h2 = add(h1, 'heading', text.rstrip(':').strip())
                stack = []
            else:
                dropped.append(text)
        else:  # li
            color, bold, strike = _style_of(b['spans'])
            done = strike or '✅' in text
            text = _clean(text.replace('✅', ''))
            if not text:
                continue
            text, due_date, due_slot = parse_day_tag(text, base_date)
            priority = COLOR_PRIORITY.get(color, 'none')
            custom = None
            if color and priority == 'none' and color not in NEUTRAL_COLORS and not done:
                custom = color
            level = min(b['level'], len(stack))
            parent = stack[level - 1] if level > 0 else (h2 if h2 is not None else h1)
            idx = add(parent, 'task', text, priority=priority, color=custom,
                      due_date=due_date, due_slot=due_slot, done=done)
            stack = stack[:level] + [idx]
    return nodes, dropped


def format_tree(nodes):
    depth = {}
    lines = []
    for i, n in enumerate(nodes):
        d = 0 if n['parent'] is None else depth[n['parent']] + 1
        depth[i] = d
        marks = []
        if n['priority'] != 'none':
            marks.append('!' + n['priority'])
        if n['color']:
            marks.append('color=' + n['color'])
        if n['due_date']:
            marks.append('@' + n['due_date'] + ('/' + n['due_slot'] if n['due_slot'] else ''))
        if n['done']:
            marks.append('DONE')
        prefix = '# ' if n['kind'] == 'heading' else '- '
        lines.append('  ' * d + prefix + n['text'] + ('  [' + ' '.join(marks) + ']' if marks else ''))
    return '\n'.join(lines)


def load(store, nodes, import_ts):
    ids = {}
    for i, n in enumerate(nodes):
        parent_id = None if n['parent'] is None else ids[n['parent']]
        sibs = [x['id'] for x in store.tree() if x['parent_id'] == parent_id]
        created = store.create(parent_id=parent_id, after_id=sibs[-1] if sibs else None,
                               kind=n['kind'], text=n['text'], priority=n['priority'], color=n['color'],
                               due_date=n['due_date'], due_slot=n['due_slot'],
                               done_at=import_ts if n['done'] else None, action='import')
        ids[i] = created['id']
    return len(ids)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('html')
    ap.add_argument('--db', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'todo.db'))
    ap.add_argument('--date', default=datetime.date.today().isoformat())
    ap.add_argument('--load', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args(argv)
    with open(args.html, encoding='utf-8') as f:
        html = f.read()
    nodes, dropped = convert(parse_html(html), datetime.date.fromisoformat(args.date))
    print(format_tree(nodes))
    print(f'\n{len(nodes)} nodes; {len(dropped)} paragraphs dropped:')
    for d in dropped:
        print('  - ' + (d[:90] + '…' if len(d) > 90 else d))
    if not args.load:
        print('\n(dry run; add --load to write)')
        return
    from db import Store, now_iso
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    store = Store(args.db, md_path=os.path.splitext(os.path.abspath(args.db))[0] + '.md')
    if store.tree() and not args.force:
        raise SystemExit('db already has nodes; use --force to append anyway')
    n = load(store, nodes, now_iso())
    store.close()
    print(f'loaded {n} nodes into {args.db}')


if __name__ == '__main__':
    main()
