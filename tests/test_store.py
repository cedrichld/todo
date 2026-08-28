import datetime
import json
import os
import sqlite3
import zlib
import shutil
import tempfile
import unittest

from db import RETENTION_DAYS, SCHEMA, Store, StoreError


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
                         ['create'])  # a tick followed by an untick cancels out)
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

    def test_new_sub_task_reopens_a_done_parent(self):
        self.s.set_done(self.p['id'], True)
        z = self.s.create(parent_id=self.p['id'], text='z')  # Enter on a done parent
        self.assertFalse(self.done(self.p)); self.assertFalse(self.done(z))
        self.assertTrue(self.done(self.x) and self.done(self.y))  # siblings keep their state
        self.s.set_done(self.p['id'], True)
        g = self.s.create(parent_id=self.x['id'], text='g')  # deeper: reopens x and p, not the heading
        self.assertFalse(self.done(self.x) or self.done(self.p)); self.assertTrue(self.done(self.y))
        self.assertIsNone(self.s.get(self.h['id'])['done_at'])
        self.s.set_done(self.p['id'], True)
        _, tail = self.s.split(self.y['id'], 1)  # Enter inside a done child: the new sibling reopens the parent
        self.assertFalse(self.done(self.p)); self.assertFalse(self.done(tail))
        self.assertTrue(self.done(self.y))

    def test_removing_the_last_open_sub_task_completes_the_parent(self):
        self.s.set_done(self.x['id'], True)
        self.s.delete(self.y['id'])  # archived: x is the only live sub-task and it is done
        self.assertTrue(self.done(self.p))
        e = self.s.create(parent_id=self.p['id'], text='')  # a new open row reopens p; x stays done
        self.assertFalse(self.done(self.p)); self.assertTrue(self.done(self.x))
        self.s.delete(e['id'])  # hard delete of an empty row does the same
        self.assertTrue(self.done(self.p))

    def test_split_inherits_the_parents_priority(self):
        self.s.update(self.p['id'], priority='urgent')
        _, tail = self.s.split(self.x['id'], 1)  # Enter inside a child: sibling under p
        self.assertEqual(tail['priority'], 'urgent')
        _, kid = self.s.split(self.p['id'], 6, parent_id=self.p['id'], after_id=None)  # Enter on p: first child
        self.assertEqual(kid['priority'], 'urgent')
        _, top = self.s.split(self.p['id'], 6)  # sibling under the heading: nothing to inherit
        self.assertEqual(top['priority'], 'none')

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
        con.executescript(SCHEMA.replace('  waiting_on  TEXT,\n  waiting_since TEXT,\n  note        TEXT,\n  auto_urgent TEXT,\n', ''))
        con.execute("INSERT INTO nodes(parent_id, position, kind, text, created_at, updated_at) VALUES (NULL, 0, 'task', 'legacy', 't', 't')")
        con.commit(); con.close()
        s2 = Store(old)
        try:
            self.assertEqual(s2.update(1, waiting_on='Bob')['waiting_on'], 'Bob')
            self.assertEqual(s2.update(1, note='details')['note'], 'details')
            self.assertEqual(s2.update(1, due_date=datetime.date.today().isoformat())['priority'], 'urgent')
        finally:
            s2.close()


class Notes(StoreTestCase):
    """Free-text description on any item: kept verbatim, blank means none, edits coalesce like title edits."""

    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.t = self.s.create(parent_id=self.h['id'], text='Email Bob')

    def test_note_round_trips_and_logs_one_coalesced_row(self):
        n = self.s.update(self.t['id'], note='bob@example.com\nask about the deadline')
        self.assertEqual(n['note'], 'bob@example.com\nask about the deadline')
        self.s.update(self.t['id'], note='bob@example.com\nask about the deadline and the budget')
        rows = [r for r in self.chrono('edit') if r['field'] == 'note']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['old'], rows[0]['new']), ('', 'bob@example.com\nask about the deadline and the budget'))
        self.assertEqual(rows[0]['snapshot'], 'Email Bob')

    def test_blank_note_is_none_and_bad_values_rejected(self):
        self.s.update(self.t['id'], note='x')
        self.assertIsNone(self.s.update(self.t['id'], note='  \n ')['note'])
        self.assertIsNone(self.s.update(self.t['id'], note=None)['note'])
        with self.assertRaises(StoreError):
            self.s.update(self.t['id'], note=5)
        self.assertEqual(self.s.update(self.h['id'], note='section context')['note'], 'section context')

    def test_mirror_indents_note_lines_under_the_item(self):
        k = self.s.create(parent_id=self.t['id'], text='sub')
        self.s.update(self.t['id'], note='line one\nline two')
        self.s.update(self.h['id'], note='about H')
        with open(self.md) as f:
            self.assertEqual(f.read(), '# H\nabout H\n- [ ] Email Bob\n  line one\n  line two\n  - [ ] sub\n')


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
    def test_path_lists_every_ancestor(self):
        h1 = self.s.create(kind='heading', text='Lab')
        h2 = self.s.create(parent_id=h1['id'], kind='heading', text='Racer')
        t = self.s.create(parent_id=h2['id'], text='parent task')
        k = self.s.create(parent_id=t['id'], text='kid')
        self.assertEqual(self.s.path(k['id']), ['Lab', 'Racer', 'parent task'])
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
        ids = [n['id'] for n in self.s.done_tree()]
        self.assertEqual(sorted(ids), sorted([h['id'], a['id'], b['id']]))  # archived done tasks come with their (archived) section
        self.assertEqual({n['id']: bool(n['archived_at']) for n in self.s.done_tree()}, {h['id']: False, a['id']: True, b['id']: True})
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


class DueSoon(StoreTestCase):
    """Due today / tomorrow / overdue → red, once per due date; the user's own colour afterwards sticks."""

    def setUp(self):
        super().setUp()
        self.h = self.s.create(kind='heading', text='H')
        self.t = self.s.create(parent_id=self.h['id'], text='Autograder', priority='later')
        self.today = datetime.date.today()

    def d(self, days):
        return (self.today + datetime.timedelta(days=days)).isoformat()

    def test_due_tomorrow_turns_red_and_is_logged(self):
        n = self.s.update(self.t['id'], due_date=self.d(1), due_slot='morning')
        self.assertEqual((n['priority'], n['auto_urgent']), ('urgent', self.d(1)))
        self.assertIn('urgent (due soon)', [r['new'] for r in self.chrono('edit')])
        far = self.s.update(self.t['id'], due_date=self.d(5))
        self.assertEqual(far['priority'], 'urgent')  # nothing resets a colour silently
        self.assertEqual(self.s.update(self.t['id'], priority='later')['priority'], 'later')
        self.assertEqual(self.s.update(self.t['id'], due_date=self.d(0))['priority'], 'urgent')  # a new due date tags again

    def test_override_sticks_through_updates_and_sweeps(self):
        self.s.update(self.t['id'], due_date=self.d(0))
        self.s.update(self.t['id'], priority='normal')
        self.s.update(self.t['id'], text='Autograder v2')
        self.assertEqual(self.s.sweep_due(), [])
        self.assertEqual(self.s.get(self.t['id'])['priority'], 'normal')
        self.assertEqual(self.s.update(self.t['id'], color='#123456')['priority'], 'normal')

    def test_sweep_tags_dates_that_rolled_in_and_skips_done(self):
        u = self.s.create(parent_id=self.h['id'], text='untouched')
        self.s.conn.execute('UPDATE nodes SET due_date=? WHERE id=?', (self.d(1), self.t['id']))
        self.s.conn.execute('UPDATE nodes SET due_date=? WHERE id=?', (self.d(-3), u['id']))
        self.s.conn.commit()
        self.s.set_done(u['id'], True)
        self.assertEqual(self.s.sweep_due(), [self.t['id']])
        self.assertEqual(self.s.get(self.t['id'])['priority'], 'urgent')
        self.assertEqual(self.s.get(u['id'])['priority'], 'none')
        self.assertEqual(self.s.sweep_due(), [])

    def test_choosing_a_colour_first_counts_as_the_decision(self):
        self.s.conn.execute('UPDATE nodes SET due_date=? WHERE id=?', (self.d(0), self.t['id'])); self.s.conn.commit()
        n = self.s.update(self.t['id'], priority='soon', color=None)
        self.assertEqual((n['priority'], n['auto_urgent']), ('soon', self.d(0)))
        self.assertEqual(self.s.sweep_due(), [])
        self.assertIsNone(self.s.update(self.t['id'], due_date=None)['auto_urgent'])


class Snapshots(StoreTestCase):
    """One compressed copy of the list per day it was saved; a year of snapshots is kept, history forever."""

    def test_every_save_updates_todays_snapshot_and_old_ones_are_pruned(self):
        today = datetime.date.today().isoformat()
        h = self.s.create(kind='heading', text='H')
        t = self.s.create(parent_id=h['id'], text='first')
        snap = self.s.snapshot(today)
        self.assertEqual(snap['day'], today)
        self.assertEqual([n['text'] for n in snap['nodes']], ['H', 'first'])
        self.s.update(t['id'], text='renamed')
        self.assertEqual([n['text'] for n in self.s.snapshot(today)['nodes']], ['H', 'renamed'])
        old_day = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS + 3)).isoformat()
        kept_day = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS - 3)).isoformat()
        blob = zlib.compress(json.dumps([]).encode())
        self.s.conn.execute('INSERT INTO snapshots(day, taken_at, nodes) VALUES (?,?,?)', (old_day, 't', blob))
        self.s.conn.execute('INSERT INTO snapshots(day, taken_at, nodes) VALUES (?,?,?)', (kept_day, 't', blob))
        self.s.conn.execute("INSERT INTO history(node_id, ts, action, snapshot) VALUES (?,?,?,?)", (t['id'], old_day + 'T09:00:00+00:00', 'done', 'x'))
        self.s.conn.commit()
        path = self.s.conn.execute('PRAGMA database_list').fetchone()[2]
        self.s.close()
        self.s = Store(path, md_path=self.md)
        self.assertEqual(self.s.snapshot_days(), [kept_day, today])
        self.assertEqual(self.s.conn.execute('SELECT COUNT(*) FROM history WHERE ts LIKE ?', (old_day + '%',)).fetchone()[0], 1)  # history is kept: years of completions stay countable
        # a day without a snapshot shows the latest earlier one; before any snapshot there is nothing
        between = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.s.snapshot(between)['day'], kept_day)
        self.assertIsNone(self.s.snapshot('2000-01-01'))


class Insights(StoreTestCase):
    def test_counts_streaks_and_sections(self):
        h = self.s.create(kind='heading', text='Lab')
        a = self.s.create(parent_id=h['id'], text='a')
        b = self.s.create(parent_id=h['id'], text='b')
        c = self.s.create(text='loose')
        self.s.set_done(a['id'], True)
        self.s.set_done(b['id'], True)
        yesterday = (datetime.datetime.now().astimezone() - datetime.timedelta(days=1)).isoformat(timespec='milliseconds')
        self.s.conn.execute("UPDATE history SET ts=? WHERE node_id=? AND action='done'", (yesterday, b['id']))
        self.s.conn.commit()
        ins = self.s.insights()
        self.assertEqual(len(ins['days']), RETENTION_DAYS)
        self.assertEqual(ins['done'][-1], 1)
        self.assertEqual(ins['done'][-2], 1)
        self.assertEqual(ins['created'][-1], 4)
        self.assertEqual(ins['streak'], 2)
        self.s.set_done(a['id'], False); self.s.set_done(a['id'], True); self.s.set_done(a['id'], False); self.s.set_done(a['id'], True)
        self.assertEqual(self.s.insights()['done'][-1], 1)  # undo / redo cycles still count the task once
        self.s.set_done(a['id'], False)
        self.assertEqual(self.s.insights()['done'][-1], 0)
        self.assertEqual(self.s.insights()['totals']['done_all_time'], 1)
        self.s.set_done(a['id'], True)
        self.assertEqual(ins['best_streak'], 2)
        self.assertEqual(ins['by_section'], [{'section': 'Lab', 'done': 2, 'open': 0}])
        self.assertEqual(ins['totals']['open'], 1)
        self.assertEqual(ins['open'][-1], 1)
        self.assertEqual(sum(ins['by_hour']), 2)
        self.assertEqual(ins['snapshot_days'], [datetime.date.today().isoformat()])


class Reconstruct(StoreTestCase):
    """Days before the first snapshot are rebuilt from the history by undoing later changes."""

    def test_rebuilds_yesterday_from_history(self):
        h = self.s.create(kind='heading', text='H')
        a = self.s.create(parent_id=h['id'], text='old name')
        b = self.s.create(parent_id=h['id'], text='b')
        gone = self.s.create(parent_id=h['id'], text='archived later')
        self.s.set_done(b['id'], True)
        # pretend all of that happened yesterday, and today's snapshot did not exist back then
        y = (datetime.datetime.now().astimezone() - datetime.timedelta(days=1)).replace(hour=10)
        self.s.conn.execute('UPDATE history SET ts=?', (y.isoformat(timespec='milliseconds'),))
        self.s.conn.execute('DELETE FROM snapshots'); self.s.conn.commit()
        # today's changes
        self.s.update(a['id'], text='new name', priority='urgent')
        self.s.set_done(b['id'], False)
        self.s.delete(gone['id'])
        c = self.s.create(parent_id=h['id'], text='created today')
        yday = y.date().isoformat()
        snap = self.s.snapshot(yday)
        self.assertTrue(snap['reconstructed'])
        by = {n['id']: n for n in snap['nodes']}
        self.assertEqual(by[a['id']]['text'], 'old name'); self.assertEqual(by[a['id']]['priority'], 'none')
        # b was ticked yesterday and unticked today: the pair cancelled, so the rebuild cannot know it was briefly done
        self.assertIsNone(by[b['id']]['done_at'])
        self.assertIn(gone['id'], by); self.assertIsNone(by[gone['id']]['archived_at'])
        self.assertNotIn(c['id'], by)
        self.assertIsNone(self.s.snapshot((y.date() - datetime.timedelta(days=1)).isoformat()))
        self.assertEqual(self.s.first_day(), yday)
        self.assertEqual(self.s.snapshot(datetime.date.today().isoformat())['day'], datetime.date.today().isoformat())  # today has a real snapshot again


class Reorder(StoreTestCase):
    def test_reorder_sets_positions_and_rejects_other_sets(self):
        h = self.s.create(kind='heading', text='H')
        a = self.s.create(parent_id=h['id'], text='a'); b = self.s.create(parent_id=h['id'], text='b'); c = self.s.create(parent_id=h['id'], text='c')
        r = self.s.reorder(h['id'], [c['id'], a['id'], b['id']])
        self.assertEqual(r['ids'], [c['id'], a['id'], b['id']])
        self.assertEqual([n['text'] for n in self.s.tree() if n['parent_id'] == h['id']], ['c', 'a', 'b'])
        with self.assertRaises(StoreError):
            self.s.reorder(h['id'], [a['id'], b['id']])
        self.assertEqual(self.chrono('move')[-1]['old'], 'reorder')


class DoneHistory(StoreTestCase):
    """Toggling leaves no trace: a tick then an untick cancel, an untick then a tick keep one done row."""

    def rows(self, nid):
        return [r['action'] for r in self.chrono() if r['node_id'] == nid and r['action'] in ('done', 'undone')]

    def test_toggling_cancels_and_final_state_keeps_one_row(self):
        h = self.s.create(kind='heading', text='H')
        t = self.s.create(parent_id=h['id'], text='t')
        self.s.set_done(t['id'], True); self.s.set_done(t['id'], False)
        self.assertEqual(self.rows(t['id']), [])
        self.s.set_done(t['id'], True); self.s.set_done(t['id'], False); self.s.set_done(t['id'], True)
        self.assertEqual(self.rows(t['id']), ['done'])
        self.assertEqual(self.s.insights()['totals']['done_all_time'], 1)
        # a task that arrived done (no done row) and is reopened keeps its undone row until it is done again
        d = self.s.create(parent_id=h['id'], text='d', done_at='2026-08-20T09:00:00+00:00')
        self.s.set_done(d['id'], False)
        self.assertEqual(self.rows(d['id']), ['undone'])
        self.s.set_done(d['id'], True)
        self.assertEqual(self.rows(d['id']), ['done'])

    def test_compaction_cleans_an_old_log(self):
        h = self.s.create(kind='heading', text='H')
        t = self.s.create(parent_id=h['id'], text='t')
        for i, a in enumerate(['done', 'undone', 'done', 'undone', 'done', 'done', 'undone', 'done']):
            self.s.conn.execute("INSERT INTO history(node_id, ts, action, snapshot) VALUES (?,?,?,?)", (t['id'], f'2026-08-2{i}T09:00:00+00:00', a, 't'))
        self.s.conn.commit()
        self.assertEqual(self.s._compact_history(), 7)
        self.assertEqual(self.rows(t['id']), ['done'])
        self.assertEqual(self.s._compact_history(), 0)
