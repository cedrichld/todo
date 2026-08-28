#!/usr/bin/env python3
"""Local todo server: JSON API + static page. Stdlib only.

    python3 server.py [--host 127.0.0.1] [--port 5757] [--db data/todo.db]
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from db import Store, StoreError, _UNSET

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, 'static')

sys.path.insert(0, ROOT)


def _int_or_none(v):
    return None if v is None else int(v)


ROUTES = [
    ('GET', r'/api/tree$', lambda s, m, q, b: (s.sweep_due(), {'nodes': s.tree()})[1]),  # the sweep tags newly due tasks red
    ('GET', r'/api/done-tree$', lambda s, m, q, b: {'nodes': s.done_tree()}),
    ('POST', r'/api/nodes$', lambda s, m, q, b: s.create(
        parent_id=_int_or_none(b.get('parent_id')),
        after_id=_int_or_none(b['after_id']) if 'after_id' in b else _UNSET,
        kind=b.get('kind', 'task'), text=b.get('text', ''))),
    ('PATCH', r'/api/nodes/(\d+)$', lambda s, m, q, b: s.update(int(m.group(1)), **b)),
    ('POST', r'/api/nodes/(\d+)/done$', lambda s, m, q, b: s.set_done(int(m.group(1)), bool(b.get('done', True)))),
    ('POST', r'/api/nodes/(\d+)/move$', lambda s, m, q, b: s.move(
        int(m.group(1)), _int_or_none(b.get('parent_id')), _int_or_none(b.get('after_id')))),
    ('POST', r'/api/nodes/(\d+)/split$', lambda s, m, q, b: {'nodes': list(s.split(
        int(m.group(1)), int(b.get('at', 0)), text=b.get('text'),
        parent_id=_int_or_none(b['parent_id']) if 'parent_id' in b else _UNSET,
        after_id=_int_or_none(b['after_id']) if 'after_id' in b else _UNSET))}),
    ('DELETE', r'/api/nodes/(\d+)$', lambda s, m, q, b: s.delete(int(m.group(1)), hard=q.get('hard') in ('1', 'true'))),
    ('POST', r'/api/nodes/(\d+)/restore$', lambda s, m, q, b: s.restore(
        int(m.group(1)),
        parent_id=_int_or_none(b['parent_id']) if 'parent_id' in b else _UNSET,
        after_id=_int_or_none(b['after_id']) if 'after_id' in b else _UNSET)),
    ('POST', r'/api/reorder$', lambda s, m, q, b: s.reorder(b.get('parent_id'), b.get('ids', []))),
    ('POST', r'/api/done-batch$', lambda s, m, q, b: {'ids': s.set_done_many([int(i) for i in b.get('ids', [])], bool(b.get('done', True)))}),
    ('POST', r'/api/archive-done$', lambda s, m, q, b: (lambda ids: {'archived': len(ids), 'ids': ids})(s.archive_done(b.get('before')))),
    ('GET', r'/api/done$', lambda s, m, q, b: {'days': s.done_log(q.get('from'), q.get('to'))}),
    ('GET', r'/api/insights$', lambda s, m, q, b: s.insights()),
    ('GET', r'/api/snapshot/(\d{4}-\d{2}-\d{2})$', lambda s, m, q, b: s.snapshot(m.group(1)) or {'day': None, 'nodes': []}),
    ('GET', r'/api/history$', lambda s, m, q, b: {'rows': s.history(q.get('q', ''), int(q.get('limit', 300)))}),
    ('GET', r'/api/search$', lambda s, m, q, b: {'nodes': s.search(q.get('q', ''))}),
]


def api(store, method, path, query, body):
    """Dispatch one API call. Returns a JSON-able dict, or None when no route matches."""
    body = body or {}
    for route_method, pattern, fn in ROUTES:
        if route_method != method:
            continue
        m = re.match(pattern, path)
        if m:
            return fn(store, m, query, body)
    return None


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if os.environ.get('TODO_LOG'):
                super().log_message(fmt, *args)

        def _send(self, code, data, ctype):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _body(self):
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b''
            if not raw.strip():
                return {}
            data = json.loads(raw.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('body must be a JSON object')
            return data

        def _static(self, path):
            if path == '/':
                path = '/index.html'
            full = os.path.normpath(os.path.join(STATIC, path.lstrip('/')))
            if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                return self._json(404, {'error': 'not found'})
            ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
                ctype += '; charset=utf-8'
            with open(full, 'rb') as f:
                self._send(200, f.read(), ctype)

        def _route(self, method):
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            try:
                if url.path.startswith('/api/'):
                    body = self._body() if method in ('POST', 'PATCH') else None
                    result = api(store, method, url.path, query, body)
                    if result is None:
                        return self._json(404, {'error': 'not found'})
                    return self._json(200, result)
                if method == 'GET':
                    return self._static(url.path)
                return self._json(405, {'error': 'method not allowed'})
            except StoreError as e:
                return self._json(400, {'error': str(e)})
            except (ValueError, KeyError, TypeError) as e:
                return self._json(400, {'error': f'bad request: {e}'})
            except Exception as e:  # noqa: BLE001 - last-resort handler for a local tool
                traceback.print_exc()
                return self._json(500, {'error': f'{type(e).__name__}: {e}'})

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def do_PATCH(self):
            self._route('PATCH')

        def do_DELETE(self):
            self._route('DELETE')

    return Handler


def make_server(store, host, port):
    return ThreadingHTTPServer((host, port), make_handler(store))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Local todo server')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5757)
    ap.add_argument('--db', default=os.path.join(ROOT, 'data', 'todo.db'))
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    md_path = os.path.splitext(os.path.abspath(args.db))[0] + '.md'
    store = Store(args.db, md_path=md_path)
    srv = make_server(store, args.host, args.port)
    print(f'todo: http://{args.host}:{srv.server_address[1]}/  (db: {args.db})', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        store.close()


if __name__ == '__main__':
    main()
