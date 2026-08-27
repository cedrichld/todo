"""Markdown mirror of the todo tree: human-readable backup, never read back by the app."""
import os
import tempfile


def _note_lines(n, indent):
    note = n.get('note')
    return ['  ' * indent + ln for ln in note.split('\n')] if note else []


def render(nodes):
    by_parent = {}
    for n in nodes:
        by_parent.setdefault(n['parent_id'], []).append(n)
    for kids in by_parent.values():
        kids.sort(key=lambda n: (n['position'], n['id']))
    out = []

    def walk(parent_id, depth, tdepth):
        # depth = number of ancestors (sets heading size, like the page); tdepth = task nesting
        for n in by_parent.get(parent_id, []):
            if n['kind'] == 'heading':
                if out:
                    out.append('')
                out.append('#' * min(depth + 1, 3) + ' ' + n['text'])
                out.extend(_note_lines(n, 0))
                walk(n['id'], depth + 1, 0)
            else:
                box = '[x]' if n['done_at'] else '[ ]'
                tags = []
                if n['priority'] != 'none':
                    tags.append('!' + n['priority'])
                if n['color']:
                    tags.append('color=' + n['color'])
                if n['due_date']:
                    tags.append('@' + n['due_date'] + ('/' + n['due_slot'] if n['due_slot'] else ''))
                if n.get('waiting_on') and not n['done_at']:
                    since = n.get('waiting_since') or ''
                    tags.append('(waiting on ' + n['waiting_on'] + (' since ' + since[:10] if since else '') + ')')
                if n['done_at']:
                    tags.append('(done ' + n['done_at'][:10] + ')')
                line = '  ' * tdepth + '- ' + box + ' ' + n['text']
                if tags:
                    line += ' ' + ' '.join(tags)
                out.append(line)
                out.extend(_note_lines(n, tdepth + 1))
                walk(n['id'], depth + 1, tdepth + 1)

    walk(None, 0, 0)
    return '\n'.join(out) + '\n'


def write(path, nodes):
    body = render(nodes)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.todo-', suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
