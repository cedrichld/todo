import os
import sqlite3
import shutil
import tempfile
import unittest

from db import SCHEMA, Store, StoreError


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
        self.assertEqual(self.s.archive_done(), [self.a['id']])
        self.assertEqual(self.texts(self.h['id']), ['b', 'c'])
        self.s.set_done(self.c['id'], True)
        self.s.update(self.c['id'], text='c')  # touch; done_at is today
        self.assertEqual(self.s.archive_done(before='2000-01-01'), [])
        self.assertEqual(self.s.archive_done(), [self.c['id']])

    def test_undone_restores_archived_node_and_parents(self):
        self.s.set_done(self.a['id'], True)
        self.s.archive_done()
        self.s.delete(self.h['id'])  # archive the heading too
        self.assertEqual(self.s.tree(), [])
        self.s.set_done(self.a['id'], False)
        self.assertEqual([n['text'] for n in self.s.tree()], ['H', 'a'])


class DoneFlow(StoreTestCase):
    """A task with sub-tasks is done exactly when all of them are."""

    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.p = self.s.create(parent_id=self.h['id'], text='parent')
        self.x = self.s.create(parent_id=self.p['id'], text='x')
        self.y = self.s.create(parent_id=self.p['id'], text='y')

    def done(self, n):
        return bool(self.s.get(n['id'])['done_at'])

    def test_last_sub_task_completes_the_parent(self):
        r = self.s.set_done(self.x['id'], True)
        self.assertEqual(r['changed'], [self.x['id']])
        self.assertFalse(self.done(self.p))
        r = self.s.set_done(self.y['id'], True)
        self.assertEqual(r['changed'], [self.y['id'], self.p['id']])
        self.assertTrue(self.done(self.p))
        self.assertEqual(self.chrono('done')[-1]['node_id'], self.p['id'])

    def test_reopening_a_sub_task_reopens_the_parent(self):
        self.s.set_done(self.x['id'], True); self.s.set_done(self.y['id'], True)
        r = self.s.set_done(self.y['id'], False)
        self.assertEqual(r['changed'], [self.y['id'], self.p['id']])
        self.assertFalse(self.done(self.p)); self.assertTrue(self.done(self.x))

    def test_parent_toggle_applies_to_sub_tasks(self):
        self.s.set_done(self.x['id'], True)
        r = self.s.set_done(self.p['id'], True)
        self.assertEqual(r['changed'], [self.p['id'], self.y['id']])  # x was already done
        self.assertTrue(self.done(self.x) and self.done(self.y))
        r = self.s.set_done(self.p['id'], False)
        self.assertEqual(sorted(r['changed']), sorted([self.p['id'], self.x['id'], self.y['id']]))
        self.assertFalse(self.done(self.x) or self.done(self.y))

    def test_flows_through_grandchildren_and_stops_at_headings(self):
        g = self.s.create(parent_id=self.x['id'], text='g')
        self.s.set_done(self.y['id'], True)
        r = self.s.set_done(g['id'], True)
        self.assertEqual(r['changed'], [g['id'], self.x['id'], self.p['id']])
        self.assertIsNone(self.s.get(self.h['id'])['done_at'])
        self.s.set_done(self.p['id'], False)
        self.assertFalse(self.done(g))

    def test_archived_sub_tasks_do_not_count(self):
        self.s.set_done(self.x['id'], True)
        self.s.delete(self.x['id'])  # archived; y is the only live sub-task
        self.s.set_done(self.y['id'], True)
        self.assertTrue(self.done(self.p))

    def test_set_done_many_is_exact(self):
        self.s.set_done(self.x['id'], True)
        self.assertEqual(self.s.set_done_many([self.p['id'], self.y['id']], True), [self.p['id'], self.y['id']])
        self.assertEqual(self.s.set_done_many([self.p['id'], self.y['id']], False), [self.p['id'], self.y['id']])
        self.assertTrue(self.done(self.x)); self.assertFalse(self.done(self.p))
        with self.assertRaises(StoreError):
            self.s.set_done(self.h['id'], True)


class Waiting(StoreTestCase):
    """Blocked todos: waiting_on (who/what) + waiting_since, still ordinary open tasks."""

    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.t = self.s.create(parent_id=self.h['id'], text='Reach out Jo')

    def test_marking_waiting_stamps_since_and_logs(self):
        n = self.s.update(self.t['id'], waiting_on='Sam: which channel')
        self.assertEqual(n['waiting_on'], 'Sam: which channel')
        self.assertTrue(n['waiting_since'])
        self.assertIsNone(n['done_at'])
        row = self.chrono('edit')[-1]
        self.assertEqual((row['field'], row['old'], row['new']), ('waiting', '', 'Sam: which channel'))

    def test_bump_resets_since_and_clear_drops_both(self):
        self.s.update(self.t['id'], waiting_on='Sam', waiting_since='2026-08-01T09:00:00-04:00')
        n = self.s.update(self.t['id'], waiting_since='2026-08-20T09:00:00-04:00')
        self.assertEqual(n['waiting_since'], '2026-08-20T09:00:00-04:00')
        self.assertEqual(self.chrono('edit')[-1]['new'], 'Sam (bumped)')
        n = self.s.update(self.t['id'], waiting_on='')
        self.assertIsNone(n['waiting_on']); self.assertIsNone(n['waiting_since'])
        self.assertEqual(self.chrono('edit')[-1]['new'], '')

    def test_rejects_bad_values_and_headings_drop_it(self):
        with self.assertRaises(StoreError):
            self.s.update(self.t['id'], waiting_since='not a time')
        with self.assertRaises(StoreError):
            self.s.update(self.t['id'], waiting_on=5)
        self.s.update(self.t['id'], waiting_on='Sam')
        n = self.s.update(self.t['id'], kind='heading')
        self.assertIsNone(n['waiting_on']); self.assertIsNone(n['waiting_since'])

    def test_mirror_and_migration(self):
        self.s.update(self.t['id'], waiting_on='Sam', waiting_since='2026-08-20T09:00:00-04:00')
        with open(self.md) as f:
            self.assertIn('- [ ] Reach out Jo (waiting on Sam since 2026-08-20)', f.read())
        old = os.path.join(self.dir, 'old.db')
        con = sqlite3.connect(old)
        con.executescript(SCHEMA.replace('  waiting_on  TEXT,\n  waiting_since TEXT,\n', ''))
        con.execute("INSERT INTO nodes(parent_id, position, kind, text, created_at, updated_at) VALUES (NULL, 0, 'task', 'legacy', 't', 't')")
        con.commit(); con.close()
        s2 = Store(old)
        try:
            self.assertEqual(s2.update(1, waiting_on='Bob')['waiting_on'], 'Bob')
        finally:
            s2.close()


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


class Undo(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.a = self.s.create(parent_id=self.h['id'], text='a')
        self.b = self.s.create(parent_id=self.h['id'], text='b')
        self.c = self.s.create(parent_id=self.h['id'], text='c')

    def test_hard_delete_of_nonempty_node(self):
        res = self.s.delete(self.b['id'], hard=True)
        self.assertEqual(res, {'id': self.b['id'], 'hard': True})
        with self.assertRaises(StoreError):
            self.s.get(self.b['id'])
        self.assertEqual(self.texts(self.h['id']), ['a', 'c'])
        self.assertFalse([r for r in self.s.history(limit=1000) if r['node_id'] == self.b['id']])

    def test_restore_puts_subtree_back_in_place(self):
        k = self.s.create(parent_id=self.b['id'], text='kid')
        old_kid = self.s.create(parent_id=self.b['id'], text='')
        self.s.delete(old_kid['id'])  # hard, gone for good
        self.s.delete(self.b['id'])  # archives b + kid
        self.assertEqual(self.texts(self.h['id']), ['a', 'c'])
        n = self.s.restore(self.b['id'], parent_id=self.h['id'], after_id=self.a['id'])
        self.assertIsNone(n['archived_at'])
        self.assertEqual(self.texts(self.h['id']), ['a', 'b', 'c'])
        self.assertEqual(self.texts(self.b['id']), ['kid'])
        self.assertEqual([r['snapshot'] for r in self.chrono('restore')], ['b', 'kid'])
        self.assertEqual(self.s.restore(self.b['id'])['id'], self.b['id'])  # no-op when active

    def test_restore_defaults_to_end_of_old_parent_and_revives_archived_parent(self):
        self.s.delete(self.a['id'])
        self.s.delete(self.h['id'])
        self.assertEqual(self.s.tree(), [])
        self.s.restore(self.a['id'])
        self.assertEqual([n['text'] for n in self.s.tree()], ['H', 'a'])
        self.assertTrue(self.s.get(self.b['id'])['archived_at'])  # siblings archived with H stay archived

    def test_restore_does_not_drag_along_children_archived_earlier(self):
        k = self.s.create(parent_id=self.b['id'], text='old kid')
        self.s.delete(k['id'])  # archived first, separately
        self.s.conn.execute("UPDATE nodes SET archived_at='2026-01-01T00:00:00.000-04:00' WHERE id=?", (k['id'],))
        self.s.conn.commit()
        self.s.delete(self.b['id'])
        self.s.restore(self.b['id'])
        self.assertEqual(self.texts(self.b['id']), [])
