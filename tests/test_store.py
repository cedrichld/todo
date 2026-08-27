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


class Structure(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.a = self.s.create(parent_id=self.h['id'], text='a')
        self.b = self.s.create(parent_id=self.h['id'], text='b')
        self.c = self.s.create(parent_id=self.h['id'], text='c')

    def test_done_toggle_logs_and_rejects_headings(self):
        n = self.s.set_done(self.a['id'], True)
        self.assertTrue(n['done_at'])
        self.s.set_done(self.a['id'], True)  # idempotent
        self.s.set_done(self.a['id'], False)
        self.assertIsNone(self.s.get(self.a['id'])['done_at'])
        self.assertEqual([r['action'] for r in self.chrono() if r['node_id'] == self.a['id']],
                         ['create', 'done', 'undone'])
        with self.assertRaises(StoreError):
            self.s.set_done(self.h['id'], True)

    def test_split_uses_client_text_and_places_after(self):
        head, tail = self.s.split(self.a['id'], 3, text='hello world')
        self.assertEqual((head['text'], tail['text']), ('hel', 'lo world'))
        self.assertEqual(self.texts(self.h['id']), ['hel', 'lo world', 'b', 'c'])
        self.assertEqual(tail['kind'], 'task')
        edits = [r for r in self.chrono('edit') if r['node_id'] == self.a['id']]
        self.assertEqual((edits[-1]['old'], edits[-1]['new']), ('a', 'hel'))

    def test_split_can_target_first_child(self):
        head, tail = self.s.split(self.h['id'], 1, parent_id=self.h['id'], after_id=None)
        self.assertEqual(self.texts(self.h['id']), ['', 'a', 'b', 'c'])
        self.assertEqual(head['kind'], 'heading')

    def test_move_within_and_across_parents(self):
        self.s.move(self.c['id'], self.h['id'], None)
        self.assertEqual(self.texts(self.h['id']), ['c', 'a', 'b'])
        self.s.move(self.b['id'], self.a['id'], None)  # b becomes child of a
        self.assertEqual(self.texts(self.h['id']), ['c', 'a'])
        self.assertEqual(self.texts(self.a['id']), ['b'])
        self.assertEqual(self.chrono('move')[-1]['snapshot'], 'b')
        with self.assertRaises(StoreError):
            self.s.move(self.a['id'], self.b['id'], None)  # cycle
        with self.assertRaises(StoreError):
            self.s.move(self.a['id'], self.a['id'], None)
        with self.assertRaises(StoreError):
            self.s.move(self.a['id'], self.h['id'], self.b['id'])  # b is not a child of h

    def test_delete_empty_hard_deletes_and_promotes_children(self):
        e = self.s.create(parent_id=self.h['id'], after_id=self.a['id'], text='')
        k1 = self.s.create(parent_id=e['id'], text='k1')
        k2 = self.s.create(parent_id=e['id'], text='k2')
        res = self.s.delete(e['id'])
        self.assertEqual(res, {'id': e['id'], 'hard': True})
        self.assertEqual(self.texts(self.h['id']), ['a', 'k1', 'k2', 'b', 'c'])
        self.assertEqual(self.s.get(k1['id'])['parent_id'], self.h['id'])
        with self.assertRaises(StoreError):
            self.s.get(e['id'])
        self.assertFalse([r for r in self.s.history(limit=1000) if r['node_id'] == e['id']])
        self.assertEqual(self.s.get(k2['id'])['position'], 2)

    def test_delete_nonempty_archives_subtree(self):
        k = self.s.create(parent_id=self.b['id'], text='kid')
        res = self.s.delete(self.b['id'])
        self.assertEqual(res, {'id': self.b['id'], 'hard': False})
        self.assertEqual(self.texts(self.h['id']), ['a', 'c'])
        self.assertTrue(self.s.get(k['id'])['archived_at'])
        self.assertEqual(self.s.get(self.c['id'])['position'], 1)
        self.assertEqual([r['snapshot'] for r in self.chrono('archive')], ['b', 'kid'])

    def test_archive_done_skips_tasks_with_open_children(self):
        self.s.set_done(self.a['id'], True)
        self.s.set_done(self.b['id'], True)
        self.s.create(parent_id=self.b['id'], text='still open')
        self.assertEqual(self.s.archive_done(), 1)
        self.assertEqual(self.texts(self.h['id']), ['b', 'c'])
        self.s.set_done(self.c['id'], True)
        self.s.update(self.c['id'], text='c')  # touch; done_at is today
        self.assertEqual(self.s.archive_done(before='2000-01-01'), 0)
        self.assertEqual(self.s.archive_done(), 1)

    def test_undone_restores_archived_node_and_parents(self):
        self.s.set_done(self.a['id'], True)
        self.s.archive_done()
        self.s.delete(self.h['id'])  # archive the heading too
        self.assertEqual(self.s.tree(), [])
        self.s.set_done(self.a['id'], False)
        self.assertEqual([n['text'] for n in self.s.tree()], ['H', 'a'])


class Mirror(StoreTestCase):
    def test_markdown_mirror_regenerated_on_every_write(self):
        h = self.s.create(kind='heading', text='H')
        with open(self.md) as f:
            self.assertEqual(f.read(), '# H\n')
        t = self.s.create(parent_id=h['id'], text='t', priority='soon')
        self.s.set_done(t['id'], True)
        with open(self.md) as f:
            body = f.read()
        self.assertIn('- [x] t !soon (done ', body)
        self.s.delete(t['id'])
        with open(self.md) as f:
            self.assertEqual(f.read(), '# H\n')


class Queries(StoreTestCase):
    def test_path_lists_heading_ancestors_only(self):
        h1 = self.s.create(kind='heading', text='Lab')
        h2 = self.s.create(parent_id=h1['id'], kind='heading', text='Racer')
        t = self.s.create(parent_id=h2['id'], text='parent task')
        k = self.s.create(parent_id=t['id'], text='kid')
        self.assertEqual(self.s.path(k['id']), ['Lab', 'Racer'])
        self.assertEqual(self.s.path(h1['id']), [])

    def test_done_log_groups_by_day_and_includes_archived(self):
        h = self.s.create(kind='heading', text='H')
        a = self.s.create(parent_id=h['id'], text='a')
        b = self.s.create(parent_id=h['id'], text='b')
        self.s.set_done(a['id'], True)
        self.s.set_done(b['id'], True)
        self.s.conn.execute("UPDATE nodes SET done_at='2026-08-20T09:00:00+00:00' WHERE id=?", (a['id'],))
        self.s.conn.commit()
        self.s.archive_done()
        days = self.s.done_log()
        self.assertEqual([len(d['items']) for d in days], [1, 1])
        self.assertEqual(days[1]['day'], '2026-08-20')
        self.assertEqual(days[1]['items'][0]['path'], ['H'])
        self.assertEqual(self.s.done_log(date_from='2026-08-21'), days[:1])
        self.assertEqual(self.s.done_log(date_to='2026-08-20'), days[1:])

    def test_search_is_case_insensitive_and_skips_archived(self):
        a = self.s.create(text='Reach out Jo')
        self.s.create(text='other')
        self.assertEqual([n['id'] for n in self.s.search('jo')], [a['id']])
        self.s.delete(a['id'])
        self.assertEqual(self.s.search('jo'), [])
