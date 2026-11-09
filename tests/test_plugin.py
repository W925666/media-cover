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


def test_title_map_switch_defaults_and_validation():
    mapping = {"港台剧": {"title": "新标题", "subtitle": "HK SERIES"},
               "电影": {"title": " ", "subtitle": ""}}
    cfg = {"titleMapEnabled": True, "titleMap": mapping}
    assert plugin._resolve_library_titles(cfg, "港台剧", "TV SHOWS") == ("新标题", "HK SERIES")
    assert plugin._resolve_library_titles(cfg, "电影", "MOVIES") == ("电影", "MOVIES")
    assert plugin._resolve_library_titles(cfg, "未配置", "MOVIES") == ("未配置", "MOVIES")
    assert plugin._resolve_library_titles(cfg, "港台剧合集", "TV SHOWS") == ("港台剧合集", "TV SHOWS")
    cfg["titleMapEnabled"] = False
    assert plugin._resolve_library_titles(cfg, "港台剧", "TV SHOWS") == ("港台剧", "TV SHOWS")
    for raw in ("broken", "[]", '{"港台剧":"HK SERIES"}', '{"港台剧":{"title":123}}'):
        try:
            plugin._title_map({"titleMapEnabled": True, "titleMap": raw})
        except ValueError:
            pass
        else:
            raise AssertionError(raw)


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


def test_library_without_collection_type_keeps_subtitle():
    p = plugin.Plugin()
    ctx = FakeCtx(files=[{"ref": "dummy.jpg"}])
    class LibraryEmby(FakeEmby):
        def request(self, **kwargs):
            return {"status": 200, "data": {"Items": [{
                "Name": "短剧", "Type": "CollectionFolder", "CollectionType": None
            }]}}
    ctx.mt.emby = LibraryEmby()
    captured = {}
    class Engine:
        def draw(self, config, assets):
            captured.update(config)
            return base64.b64encode(b"preview").decode()
    p.engine = lambda: Engine()
    p._plugin_config = lambda ctx: {}
    p._direct_for_server = lambda *args: types.SimpleNamespace(ok=False)
    p._layout_defaults = lambda layout: {}
    p.generate_cover({"serverId": "s1", "libraryId": "17", "upload": False}, ctx)
    assert captured["subtitle"] == "MEDIA LIBRARY", captured


def test_auto_subtitles_plain_words_and_custom_symbols_preserved():
    p = plugin.Plugin()
    ctx = FakeCtx(files=[{"ref": "dummy.jpg"}])
    current = {"name": "儿童", "type": "tvshows"}
    class LibraryEmby(FakeEmby):
        def request(self, **kwargs):
            return {"status": 200, "data": {"Items": [{
                "Name": current["name"], "Type": "CollectionFolder", "CollectionType": current["type"]
            }]}}
    ctx.mt.emby = LibraryEmby()
    captured = {}
    class Engine:
        def draw(self, config, assets):
            captured.update(config)
            return base64.b64encode(b"preview").decode()
    p.engine = lambda: Engine()
    p._plugin_config = lambda ctx: {}
    p._direct_for_server = lambda *args: types.SimpleNamespace(ok=False)
    p._layout_defaults = lambda layout: {}
    for name in ("儿童", "港台剧", "日韩电影", "我的私人影院", "任意库名"):
        current["name"] = name
        for kind, expected in {"movies": "MOVIES", "tvshows": "TV SHOWS",
                               "music": "MUSIC", "boxsets": "COLLECTIONS",
                               "books": "BOOKS", "homevideos": "VIDEOS",
                               "musicvideos": "MUSIC VIDEOS", "photos": "PHOTOS",
                               "playlists": "PLAYLISTS", "": "MEDIA LIBRARY"}.items():
            current["type"] = kind
            p.generate_cover({"serverId": "s1", "libraryId": "17", "upload": False}, ctx)
            assert captured["subtitle"] == expected
            assert captured["title"] == name
    custom = "MY MOVIES & TV | 4K ★"
    mapping = __import__("json").dumps({current["name"]: {"title": "", "subtitle": custom}, "港台剧": {"title": "香港台湾剧集", "subtitle": "HK SERIES"}})
    p._plugin_config = lambda ctx: {"titleMapEnabled": True, "titleMap": mapping}
    p.generate_cover({"serverId": "s1", "libraryId": "17", "subtitle": "旧工作台副标题", "title": "旧工作台标题", "upload": False}, ctx)
    assert captured["subtitle"] == custom
    assert captured["title"] == current["name"]
    current["name"] = "港台剧"
    p.generate_cover({"serverId": "s1", "libraryId": "17", "upload": False}, ctx)
    assert captured["subtitle"] == "HK SERIES"
    assert captured["title"] == "香港台湾剧集"
    current["name"] = "未配置的库"
    p.generate_cover({"serverId": "s1", "libraryId": "17", "upload": False}, ctx)
    assert captured["subtitle"] == "MEDIA LIBRARY"
    p._plugin_config = lambda ctx: {}
    p.generate_cover({"serverId": "s1", "libraryId": "17", "subtitle": "旧工作台副标题", "title": "旧工作台标题", "upload": False}, ctx)
    assert captured["title"] == current["name"]
    assert captured["subtitle"] == "MEDIA LIBRARY"


def test_explicit_title_subtitle_sizes_reach_engine():
    p = plugin.Plugin()
    ctx = FakeCtx(files=[{"ref": "dummy.jpg"}])
    captured = {}
    class Engine:
        def draw(self, config, assets):
            captured.clear()
            captured.update(config)
            return base64.b64encode(b"preview").decode()
    p.engine = lambda: Engine()
    p._plugin_config = lambda ctx: {}
    p._direct_for_server = lambda *args: types.SimpleNamespace(ok=False)
    p._layout_defaults = lambda layout: {"title_size": 160, "subtitle_size": 50}
    for title_size in ("40", "80", "100", "160", "240", "500", "", "bad", "0", "999"):
        for subtitle_size in ("40", "80", "100", "160", "240", "500", "", "bad", "0", "999"):
            p.generate_cover({"serverId": "s1", "libraryId": "17", "upload": False,
                              "titleSize": title_size, "subtitleSize": subtitle_size}, ctx)
            expected_title = 160 if title_size == "" else plugin._parse_size(title_size, 160)
            expected_subtitle = 50 if subtitle_size == "" else plugin._parse_size(subtitle_size, 80)
            assert captured["title_size"] == expected_title
            assert captured["subtitle_size"] == expected_subtitle


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
    test_explicit_title_subtitle_sizes_reach_engine(); print("test_explicit_sizes 100 combinations OK")
    test_library_without_collection_type_keeps_subtitle(); print("test_library_subtitle OK")
    test_helpers(); print("test_helpers OK")
    test_queries_survive_empty_storage(); print("test_queries OK")
    test_load_libraries_and_items(); print("test_load OK")
    test_generate_cover_no_assets_raises_clear_error(); print("test_generate_no_assets OK")
    test_generate_cover_rejects_bad_sizes_gracefully(); print("test_generate_bad_sizes OK")
    test_generate_cover_library_mode_without_target(); print("test_library_mode OK")
    test_probe_image_requires_server_and_item(); print("test_probe OK")
