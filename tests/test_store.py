import os
import shutil
import tempfile
import unittest

from db import Store, StoreError


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.md = os.path.join(self.dir, 'todo.md')
        self.s = Store(os.path.join(self.dir, 't.db'), md_path=self.md)

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir)

    def texts(self, parent_id):
        return [n['text'] for n in self.s.tree() if n['parent_id'] == parent_id]

    def chrono(self, action=None):
        rows = list(reversed(self.s.history(limit=1000)))
        return [r for r in rows if action is None or r['action'] == action]


class CreateAndUpdate(StoreTestCase):
    def test_create_orders_siblings_and_logs_history(self):
        h = self.s.create(kind='heading', text='Lab')
        a = self.s.create(parent_id=h['id'], text='a')
        self.s.create(parent_id=h['id'], text='b')
        self.s.create(parent_id=h['id'], after_id=a['id'], text='c')
        self.s.create(parent_id=h['id'], after_id=None, text='first')
        self.assertEqual(self.texts(h['id']), ['first', 'a', 'c', 'b'])
        positions = [n['position'] for n in self.s.tree() if n['parent_id'] == h['id']]
        self.assertEqual(positions, [0, 1, 2, 3])
        self.assertEqual([r['action'] for r in self.chrono()], ['create'] * 5)
        self.assertEqual(self.chrono()[0]['snapshot'], 'Lab')

    def test_create_validates(self):
        with self.assertRaises(StoreError):
            self.s.create(priority='high')
        with self.assertRaises(StoreError):
            self.s.create(parent_id=999)
        with self.assertRaises(StoreError):
            self.s.create(due_slot='night')
        with self.assertRaises(StoreError):
            self.s.create(color='red')
        h = self.s.create(kind='heading', text='h')
        with self.assertRaises(StoreError):
            self.s.create(after_id=h['id'], parent_id=h['id'])  # after_id must be a sibling

    def test_update_logs_each_field_and_coalesces_text(self):
        n = self.s.create(text='hello')
        self.s.update(n['id'], text='hello w')
        self.s.update(n['id'], text='hello world')
        self.s.update(n['id'], priority='urgent')
        self.s.update(n['id'], due_date='2026-08-27', due_slot='morning')
        self.s.update(n['id'], color='#d60fd6')
        edits = [(r['field'], r['old'], r['new']) for r in self.chrono('edit')]
        self.assertEqual(edits, [
            ('text', 'hello', 'hello world'),
            ('priority', 'none', 'urgent'),
            ('due', '', '2026-08-27 morning'),
            ('color', '', '#d60fd6'),
        ])
        got = self.s.get(n['id'])
        self.assertEqual((got['text'], got['priority'], got['due_date'], got['due_slot'], got['color']),
                         ('hello world', 'urgent', '2026-08-27', 'morning', '#d60fd6'))

    def test_update_noop_and_collapsed_do_not_log(self):
        n = self.s.create(text='x')
        self.s.update(n['id'], text='x')
        self.s.update(n['id'], collapsed=True)
        self.assertEqual(self.s.get(n['id'])['collapsed'], 1)
        self.assertEqual([r['action'] for r in self.chrono()], ['create'])

    def test_update_rejects_unknown_or_invalid(self):
        n = self.s.create(text='x')
        with self.assertRaises(StoreError):
            self.s.update(n['id'], position=3)
        with self.assertRaises(StoreError):
            self.s.update(n['id'], due_date='tomorrow')
        with self.assertRaises(StoreError):
            self.s.update(n['id'], kind='note')
        with self.assertRaises(StoreError):
            self.s.update(999, text='y')

    def test_turning_task_into_heading_clears_task_fields(self):
        n = self.s.create(text='x', priority='urgent', due_date='2026-08-27')
        self.s.update(n['id'], kind='heading')
        got = self.s.get(n['id'])
        self.assertEqual((got['kind'], got['priority'], got['due_date'], got['done_at']),
                         ('heading', 'none', None, None))
        self.assertEqual([r['field'] for r in self.chrono('edit')], ['kind'])

    def test_history_filters_by_text(self):
        self.s.create(text='alpha')
        self.s.create(text='beta')
        self.assertEqual([r['snapshot'] for r in self.s.history('alp')], ['alpha'])
        self.assertEqual(len(self.s.history()), 2)


if __name__ == '__main__':
    unittest.main()
