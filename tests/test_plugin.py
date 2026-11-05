"""plugin.py 逻辑测试：用假 mt_plugin 桩导入，覆盖健壮性修复。

运行: python3 tests/test_plugin.py
"""
import base64
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _install_mt_plugin_stub():
    mt = types.ModuleType("mt_plugin")
    ui = types.ModuleType("mt_plugin.ui")

    class Plugin:
        pass

    class Context:
        pass

    def action(name):
        def deco(fn):
            fn._action = name
            return fn
        return deco

    def query(name):
        def deco(fn):
            fn._query = name
            return fn
        return deco

    mt.Plugin, mt.Context, mt.action, mt.query, mt.ui = Plugin, Context, action, query, ui
    sys.modules["mt_plugin"] = mt
    sys.modules["mt_plugin.ui"] = ui


_install_mt_plugin_stub()
import plugin  # noqa: E402


class FakeStorage:
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def get(self, key):
        if key in self.store:
            return {"found": True, "value": self.store[key]}
        return {"found": False, "value": None}

    def set(self, key, value):
        self.store[key] = value


class FakeEmby:
    def __init__(self):
        self.servers_list = [{"id": "s1", "name": "srv", "enabled": True, "hasApiKey": True}]

    class servers:
        @staticmethod
        def list():
            return FakeEmby._instance.servers_list

    def request(self, server_id=None, path=None, query=None, method=None, json=None):
        if path == "/Library/SelectableMediaFolders":
            return {"status": 200, "data": [{"Name": "Movies", "Id": "lib1"}]}
        if path == "/Items" and query and query.get("ParentId"):
            return {"status": 200, "data": {"Items": [{"Name": "Test Movie", "ProductionYear": 2026, "Id": "it1"}]}}
        if path == "/Items" and query and query.get("Ids"):
            return {"status": 200, "data": {"Items": [{"Name": "Test Movie", "ProductionYear": 2026}]}}
        if path and "/Images/Primary" in path and method == "POST":
            return {"status": 204, "data": None}
        return {"status": 404, "data": None}


FakeEmby._instance = FakeEmby()


class FakeMt:
    def __init__(self):
        self.emby = FakeEmby._instance
        self.storage = FakeStorage()
        self.logs = []

    def log(self, level="info", message=""):
        self.logs.append((level, message))


class FakeCtx:
    def __init__(self, files=None):
        self.mt = FakeMt()
        self.files = files

    def download(self, ref):
        return ref


def test_helpers():
    assert plugin._parse_size("160", 160) == 160
    assert plugin._parse_size("abc", 160) == 160
    assert plugin._parse_size(None, 80) == 80
    assert plugin._parse_size(9999, 160) == 500
    assert plugin._parse_size(1, 160) == 8
    assert plugin._to_options(None, "Name", "Id") == []
    assert plugin._to_options([{"Name": "A", "Id": "1"}, "bad"], "Name", "Id") == [{"label": "A", "value": "1"}]
    assert plugin._storage_value(None) is None
    assert plugin._storage_value({"found": False, "value": 1}) is None
    assert plugin._storage_value({"found": True, "value": 5}) == 5


def test_queries_survive_empty_storage():
    p = plugin.Plugin()
    ctx = FakeCtx()
    # libraries 查询只读 storage 缓存（query 场景不允许 emby.request）
    assert p.libraries({}, ctx) == []
    assert p.items({}, ctx) == []
    assert p.preview({}, ctx) == {}


def test_generate_cover_library_mode_without_target():
    """一键模式：不给 targetId、只给 libraryId 时不报'未选择条目'。"""
    p = plugin.Plugin()
    ctx = FakeCtx(files=[])
    try:
        p.generate_cover({"libraryId": "lib1", "upload": False}, ctx)
    except ValueError as e:
        assert "素材" in str(e)  # 走到素材校验，说明 library 模式已接受
    else:
        raise AssertionError("expected ValueError for empty assets")


def test_load_libraries_and_items():
    p = plugin.Plugin()
    ctx = FakeCtx()
    assert p.load_libraries({}, ctx) == {"count": 1}
    assert p.load_items({"libraryId": "lib1"}, ctx) == {"count": 1}


def test_generate_cover_no_assets_raises_clear_error():
    p = plugin.Plugin()
    ctx = FakeCtx(files=None)  # 无 FilePicker 选择时 ctx.files 为 None 不应崩
    try:
        p.generate_cover({"targetId": "it1", "upload": False}, ctx)
    except ValueError as e:
        assert "素材" in str(e)
    else:
        raise AssertionError("expected ValueError for empty assets")


def test_generate_cover_rejects_bad_sizes_gracefully():
    p = plugin.Plugin()
    ctx = FakeCtx(files=[])
    try:
        p.generate_cover({"targetId": "it1", "titleSize": "not-a-number"}, ctx)
    except ValueError as e:
        assert "素材" in str(e)  # 字号不合法时回退默认，走到素材校验
    else:
        raise AssertionError("expected ValueError")


def test_probe_image_requires_server_and_item():
    p = plugin.Plugin()
    ctx = FakeCtx()
    ctx.mt.emby.servers_list = []
    try:
        p.probe_image({}, ctx)
    except ValueError as e:
        assert "服务器" in str(e)
    else:
        raise AssertionError("expected ValueError")


if __name__ == "__main__":
    test_helpers(); print("test_helpers OK")
    test_queries_survive_empty_storage(); print("test_queries OK")
    test_load_libraries_and_items(); print("test_load OK")
    test_generate_cover_no_assets_raises_clear_error(); print("test_generate_no_assets OK")
    test_generate_cover_rejects_bad_sizes_gracefully(); print("test_generate_bad_sizes OK")
    test_generate_cover_library_mode_without_target(); print("test_library_mode OK")
    test_probe_image_requires_server_and_item(); print("test_probe OK")
