import datetime
import unittest

import import_gdoc as ig

BASE = datetime.date(2026, 8, 26)  # a Wednesday


def span(text, color=None, bold=False, strike=False):
    style = 'font-size:11pt'
    if color:
        style += f';color:{color}'
    style += ';font-weight:700' if bold else ';font-weight:400'
    if strike:
        style += ';text-decoration:line-through'
    return f'<span style="{style}">{text}</span>'


FIXTURE = f"""<html><body class="doc">
<p class="title">{span('TODO LIST Aug 26/26', bold=True)}</p>
<h1>{span('Todo 26/26', bold=True)}</h1>
<ol class="c1 lst-kix_a-0 start"><li>{span('')}</li></ol>
<p>{span('Later:')}</p>
<ol class="lst-kix_b-0 start"><li>{span('Reach out Alex Rivera')}</li></ol>
<h1>{span('Lab', bold=True)}</h1>
<h2>{span('Racer - Conf 2026')}</h2>
<ol class="lst-kix_c-0 start"><li>{span('Reach out to ppl (Wed night)', '#ff0000', bold=True)}</li></ol>
<ol class="lst-kix_c-1 start"><li>{span('Slack repost', '#ff0000', bold=True)}</li><li>{span('Jordan', '#ff0000', bold=True)}</li></ol>
<ol class="lst-kix_c-0"><li>{span('Sponsors', '#b45f06', bold=True)}</li></ol>
<ol class="lst-kix_c-1 start"><li>{span('FIELD AI! (Wed night?)', '#ff0000', bold=True)}</li><li>{span('Prep meeting Tuesday', '#b45f06')}</li></ol>
<ol class="lst-kix_c-0"><li>{span('GPs (Thurs, Fri)', '#ff0000', bold=True)}</li><li>{span('Redo I/O', '#bf9000', bold=True)}</li></ol>
<h2>{span('=========================')}</h2>
<h2>{span('FAIL')}</h2>
<ol class="lst-kix_d-0 start"><li>{span('Read paper', strike=True)}</li></ol>
<ol class="lst-kix_d-1 start"><li>{span('Pratik')}</li></ol>
<ol class="lst-kix_d-2 start"><li>{span('YTB Shorter')}</li></ol>
<h1>{span('Non-critical', bold=True)}</h1>
<h2>{span('ML')}</h2>
<ol class="lst-kix_e-0 start"><li>{span('Lab 5 (transformers)', '#cccccc', bold=True)}{span('&nbsp;— notes', '#cccccc')}</li></ol>
<h2>{span('Classes:')}</h2>
<ol class="lst-kix_f-1 start"><li>{span('Moveit ', '#6aa84f')}{span('✅', bold=True)}</li></ol>
<ol class="lst-kix_f-2 start"><li>{span('Add vision?', '#d60fd6', bold=True)}</li></ol>
<p>{span('I need to now start the autograding, this is a long paragraph of prose.')}</p>
</body></html>"""


class DayTag(unittest.TestCase):
    def t(self, text):
        return ig.parse_day_tag(text, BASE)

    def test_plain_day(self):
        self.assertEqual(self.t('Competition Layout Send (Thurs)'), ('Competition Layout Send', '2026-08-27', None))

    def test_night_and_question_mark(self):
        self.assertEqual(self.t('FIELD AI! (Wed night)'), ('FIELD AI!', '2026-08-26', 'evening'))
        self.assertEqual(self.t('Add a live sim (Wed night?)'), ('Add a live sim', '2026-08-26', 'evening'))

    def test_morning_afternoon(self):
        self.assertEqual(self.t('x (Fri morning)'), ('x', '2026-08-28', 'morning'))
        self.assertEqual(self.t('x (Monday afternoon)'), ('x', '2026-08-31', 'afternoon'))

    def test_multiple_days_keeps_rest_as_text(self):
        self.assertEqual(self.t('BO (Thurs, Fri)'), ('BO (Fri)', '2026-08-27', None))

    def test_non_day_parens_untouched(self):
        self.assertEqual(self.t('Statics (quick)'), ('Statics (quick)', None, None))
        self.assertEqual(self.t('Lab 7 (diffusion) — notes'), ('Lab 7 (diffusion) — notes', None, None))
        self.assertEqual(self.t('Prep meeting with TIER IV Tuesday'), ('Prep meeting with TIER IV Tuesday', None, None))

    def test_tag_in_middle(self):
        self.assertEqual(self.t('Orientation (Thurs) prep'), ('Orientation prep', '2026-08-27', None))


class Convert(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes, cls.dropped = ig.convert(ig.parse_html(FIXTURE), BASE)

    def by_text(self, text):
        for n in self.nodes:
            if n['text'] == text:
                return n
        raise AssertionError(f'{text!r} not imported; have {[n["text"] for n in self.nodes]}')

    def parent_text(self, n):
        return None if n['parent'] is None else self.nodes[n['parent']]['text']

    def test_headings_and_nesting(self):
        self.assertEqual(self.by_text('Todo 26/26')['kind'], 'heading')
        self.assertIsNone(self.by_text('Todo 26/26')['parent'])
        later = self.by_text('Later')
        self.assertEqual((later['kind'], self.parent_text(later)), ('heading', 'Todo 26/26'))
        self.assertEqual(self.parent_text(self.by_text('Reach out Alex Rivera')), 'Later')
        self.assertEqual(self.parent_text(self.by_text('Racer - Conf 2026')), 'Lab')
        self.assertEqual(self.parent_text(self.by_text('Reach out to ppl')), 'Racer - Conf 2026')
        self.assertEqual(self.parent_text(self.by_text('Slack repost')), 'Reach out to ppl')
        self.assertEqual(self.parent_text(self.by_text('Jordan')), 'Reach out to ppl')
        self.assertEqual(self.parent_text(self.by_text('FIELD AI!')), 'Sponsors')
        self.assertEqual(self.parent_text(self.by_text('GPs (Fri)')), 'Racer - Conf 2026')
        self.assertEqual(self.parent_text(self.by_text('YTB Shorter')), 'Pratik')
        self.assertEqual(self.parent_text(self.by_text('Pratik')), 'Read paper')

    def test_level_jump_without_parent_clamps_to_heading(self):
        self.assertEqual(self.parent_text(self.by_text('Moveit')), 'Classes')
        self.assertEqual(self.parent_text(self.by_text('Add vision?')), 'Moveit')

    def test_colors_map_to_priority_or_custom(self):
        self.assertEqual(self.by_text('Reach out to ppl')['priority'], 'urgent')
        self.assertEqual((self.by_text('Sponsors')['priority'], self.by_text('Sponsors')['color']), ('normal', None))
        self.assertEqual(self.by_text('Prep meeting Tuesday')['priority'], 'normal')
        self.assertEqual(self.by_text('Redo I/O')['priority'], 'normal')
        grey = self.by_text('Lab 5 (transformers) — notes')
        self.assertEqual((grey['priority'], grey['color']), ('none', '#cccccc'))
        magenta = self.by_text('Add vision?')
        self.assertEqual((magenta['priority'], magenta['color']), ('none', '#d60fd6'))
        self.assertEqual(self.by_text('Lab')['priority'], 'none')

    def test_due_dates(self):
        self.assertEqual((self.by_text('Reach out to ppl')['due_date'], self.by_text('Reach out to ppl')['due_slot']),
                         ('2026-08-26', 'evening'))
        self.assertEqual(self.by_text('FIELD AI!')['due_slot'], 'evening')
        self.assertEqual(self.by_text('GPs (Fri)')['due_date'], '2026-08-27')

    def test_done_markers(self):
        self.assertTrue(self.by_text('Read paper')['done'])
        moveit = self.by_text('Moveit')
        self.assertTrue(moveit['done'])
        self.assertIsNone(moveit['color'])  # green + check just means done
        self.assertFalse(self.by_text('Pratik')['done'])

    def test_dropped_content(self):
        texts = [n['text'] for n in self.nodes]
        self.assertNotIn('=========================', texts)
        self.assertNotIn('', texts)
        self.assertNotIn('TODO LIST Aug 26/26', texts)
        self.assertEqual(len(self.dropped), 2)
        self.assertTrue(self.dropped[1].startswith('I need to now start'))

    def test_format_tree(self):
        out = ig.format_tree(self.nodes)
        self.assertIn('# Lab', out)
        self.assertIn('    - Reach out to ppl  [!urgent @2026-08-26/evening]', out)
        self.assertIn('      - Slack repost  [!urgent]', out)
        self.assertIn('DONE', out)
