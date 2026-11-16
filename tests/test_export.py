import os
import tempfile
import unittest

import export_md


def node(id, parent_id, position, kind, text, **kw):
    base = dict(id=id, parent_id=parent_id, position=position, kind=kind, text=text,
                priority='none', color=None, due_date=None, due_slot=None, done_at=None)
    base.update(kw)
    return base


class Render(unittest.TestCase):
    def test_renders_headings_tasks_tags_and_nesting(self):
        nodes = [
            node(1, None, 0, 'heading', 'Lab'),
            node(2, 1, 0, 'heading', 'Racer'),
            node(3, 2, 0, 'task', 'Reach out', priority='urgent', due_date='2026-08-26', due_slot='evening'),
            node(4, 3, 0, 'task', 'Slack repost', color='#d60fd6'),
            node(5, 2, 1, 'task', 'Done thing', done_at='2026-08-25T10:00:00+00:00'),
            node(6, 1, 1, 'heading', 'Research'),
            node(7, 6, 0, 'task', 'GPs', due_date='2026-08-27'),
            node(8, None, 1, 'task', 'root task'),
            node(9, 8, 0, 'heading', 'deep heading'),
        ]
        expected = (
            '# Lab\n'
            '\n'
            '## Racer\n'
            '- [ ] Reach out !urgent @2026-08-26/evening\n'
            '  - [ ] Slack repost color=#d60fd6\n'
            '- [x] Done thing (done 2026-08-25)\n'
            '\n'
            '## Research\n'
            '- [ ] GPs @2026-08-27\n'
            '- [ ] root task\n'
            '\n'
            '## deep heading\n'
        )
        self.assertEqual(export_md.render(nodes), expected)

    def test_empty(self):
        self.assertEqual(export_md.render([]), '\n')

    def test_days_in_the_mirror(self):
        nodes = [
            node(1, None, 0, 'task', 'run', due_date='2026-11-17', due_days='2026-11-17,2026-11-18,2026-11-19', due_slot='morning'),
            node(2, None, 1, 'task', 'over the month end', due_date='2026-11-30', due_days='2026-11-30,2026-12-01'),
            node(3, None, 2, 'task', 'scattered', due_date='2026-11-17', due_days='2026-11-17,2026-11-19'),
            node(4, None, 3, 'task', 'two runs', due_date='2026-11-17', due_days='2026-11-17,2026-11-18,2026-11-20,2026-11-21,2026-11-25'),
            node(5, None, 4, 'task', 'one day', due_date='2026-11-17'),
        ]
        self.assertEqual(export_md.render(nodes), (
            '- [ ] run @2026-11-17..2026-11-19/morning\n'
            '- [ ] over the month end @2026-11-30..2026-12-01\n'
            '- [ ] scattered @2026-11-17,2026-11-19\n'
            '- [ ] two runs @2026-11-17..2026-11-18,2026-11-20..2026-11-21,2026-11-25\n'
            '- [ ] one day @2026-11-17\n'
        ))

    def test_write_is_atomic_and_replaces(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, 'todo.md')
        export_md.write(path, [node(1, None, 0, 'task', 'one')])
        export_md.write(path, [node(1, None, 0, 'task', 'two')])
        with open(path) as f:
            self.assertEqual(f.read(), '- [ ] two\n')
        self.assertEqual(os.listdir(d), ['todo.md'])
