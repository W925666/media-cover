"""Run with python3 scripts/test_servers.py; no network or real credentials."""
import ast
import types
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'plugin.py').read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Plugin')
names = {'save_server', '_direct_for_server', '_first_server_id'}
methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
for method in methods:
    method.decorator_list = []
module = ast.Module(body=[ast.ClassDef(name='Plugin', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
class Direct:
    def __init__(self, url, key):
        self.base, self.key = url.rstrip('/'), key
ns = {'Context': object, 'EmbyDirect': Direct, 'urllib': urllib,
      '_storage_value': lambda r: r.get('value') if r.get('found') else None}
exec(compile(ast.fix_missing_locations(module), 'server-tests', 'exec'), ns)

class Tests(unittest.TestCase):
    def setUp(self):
        self.data = {}
        storage = types.SimpleNamespace(
            get=lambda key: {'found': key in self.data, 'value': self.data.get(key)},
            set=lambda key, value: self.data.__setitem__(key, value))
        servers = types.SimpleNamespace(list=lambda: [
            {'id': 'one', 'enabled': True, 'hasApiKey': True},
            {'id': 'two', 'enabled': True, 'hasApiKey': True}])
        self.ctx = types.SimpleNamespace(mt=types.SimpleNamespace(storage=storage, emby=types.SimpleNamespace(servers=servers)))
        self.p = ns['Plugin']()
        self.p._plugin_config = lambda ctx: {'embyUrl': 'http://legacy', 'embyApiKey': 'legacy-key'}

    def test_connections_are_isolated(self):
        for sid in ('one', 'two'):
            self.p.save_server({'serverId': sid, 'serverUrl': f'http://{sid}', 'serverApiKey': f'{sid}-key'}, self.ctx)
        for sid in ('one', 'two'):
            d = self.p._direct_for_server({}, self.ctx, sid)
            self.assertEqual((d.base, d.key), (f'http://{sid}', f'{sid}-key'))

    def test_second_never_uses_first_credentials(self):
        with self.assertRaises(ValueError):
            self.p._direct_for_server({}, self.ctx, 'two')
        self.assertEqual(self.p._direct_for_server({}, self.ctx, 'one').base, 'http://legacy')

    def test_ungranted_server_rejected(self):
        with self.assertRaises(ValueError):
            self.p.save_server({'serverId': 'unknown', 'serverUrl': 'http://x', 'serverApiKey': 'x'}, self.ctx)
        self.assertFalse(self.data)

    def test_invalid_url_rejected(self):
        with self.assertRaises(ValueError):
            self.p.save_server({'serverId': 'one', 'serverUrl': 'file:///tmp/x', 'serverApiKey': 'x'}, self.ctx)

if __name__ == '__main__':
    unittest.main()
