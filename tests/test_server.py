import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

import server
from db import Store


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Store(os.path.join(self.dir, 't.db'))

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir)

    def call(self, method, path, body=None, **query):
        return server.api(self.s, method, path, query, body)

    def test_crud_through_dispatcher(self):
        h = self.call('POST', '/api/nodes', {'kind': 'heading', 'text': 'H'})
        t = self.call('POST', '/api/nodes', {'parent_id': h['id'], 'text': 'hello world'})
        self.assertEqual(self.call('PATCH', f"/api/nodes/{t['id']}", {'priority': 'soon'})['priority'], 'soon')
        pair = self.call('POST', f"/api/nodes/{t['id']}/split", {'at': 5, 'text': 'hello there'})['nodes']
        self.assertEqual([n['text'] for n in pair], ['hello', ' there'])
        self.assertTrue(self.call('POST', f"/api/nodes/{t['id']}/done", {'done': True})['done_at'])
        moved = self.call('POST', f"/api/nodes/{pair[1]['id']}/move", {'parent_id': h['id'], 'after_id': None})
        self.assertEqual(moved['position'], 0)
        self.assertEqual(self.call('DELETE', f"/api/nodes/{pair[1]['id']}")['hard'], False)
        self.assertEqual(len(self.call('GET', '/api/tree')['nodes']), 2)
        arch = self.call('POST', '/api/archive-done', {})
        self.assertEqual((arch['archived'], arch['ids']), (1, [t['id']]))
        self.assertEqual(len(self.call('GET', '/api/done')['days']), 1)
        self.assertTrue(self.call('GET', '/api/history', q='hello')['rows'])
        self.assertEqual(self.call('GET', '/api/search', q='zzz')['nodes'], [])
        self.assertIsNone(self.call('POST', f"/api/nodes/{t['id']}/restore", {})['archived_at'])
        self.assertEqual(self.call('DELETE', f"/api/nodes/{t['id']}", hard='1')['hard'], True)
        self.assertFalse(self.call('GET', '/api/history', q='hello')['rows'])  # hard delete drops history
        self.assertIsNone(self.call('GET', '/api/nope'))


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.store = Store(os.path.join(cls.dir, 't.db'), md_path=os.path.join(cls.dir, 'todo.md'))
        cls.srv = server.make_server(cls.store, '127.0.0.1', 0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.store.close()
        shutil.rmtree(cls.dir)

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={'Content-Type': 'application/json'} if data else {})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, r.getheader('Content-Type'), raw

    def test_json_roundtrip_and_errors(self):
        status, ctype, raw = self.req('POST', '/api/nodes', {'text': 'héllo'})
        self.assertEqual(status, 200)
        self.assertIn('application/json', ctype)
        node = json.loads(raw)
        self.assertEqual(node['text'], 'héllo')
        status, _, raw = self.req('PATCH', f"/api/nodes/{node['id']}", {'priority': 'bogus'})
        self.assertEqual(status, 400)
        self.assertIn('priority', json.loads(raw)['error'])
        status, _, _ = self.req('PATCH', '/api/nodes/999999', {'text': 'x'})
        self.assertEqual(status, 400)
        status, _, _ = self.req('POST', '/api/nodes', None)
        self.assertEqual(status, 200)  # empty body = defaults
        status, _, _ = self.req('GET', '/api/nope')
        self.assertEqual(status, 404)
        status, _, raw = self.req('GET', '/api/tree')
        self.assertEqual(len(json.loads(raw)['nodes']), 2)

    def test_static_files(self):
        status, ctype, raw = self.req('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', ctype)
        self.assertIn(b'<html', raw.lower())
        status, _, _ = self.req('GET', '/../db.py')
        self.assertEqual(status, 404)
        status, _, _ = self.req('GET', '/missing.js')
        self.assertEqual(status, 404)
