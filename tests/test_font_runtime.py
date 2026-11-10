from pathlib import Path
from types import SimpleNamespace
import pytest
import font_runtime as runtime

@pytest.mark.parametrize('fail',[False,True])
def test_task_downloads_once_nested_and_cleans(monkeypatch,tmp_path,fail):
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    rows=[{'name':x}for x in ['OPPOSans-B.ttf','剑豪体.ttf','SourceHanSansCN-Bold.otf']]
    monkeypatch.setattr(runtime,'catalog',lambda:rows)
    paths=[]
    def fetch(row,directory):
        p=Path(directory)/row['name'];p.write_bytes(b'font');paths.append(p)
    monkeypatch.setattr(runtime,'fetch_font',fetch)
    import engine
    class Engine:
        def __init__(self,**kw):self.directory=kw['fonts_dir']
        def clear_image_cache(self):pass
    monkeypatch.setattr(engine,'PosterEngine',Engine)
    class Plugin:
        _engine=None
        def _plugin_config(self,ctx):return {}
        def _layout_defaults(self,layout):return {}
        @runtime.temporary_fonts
        def one(self,input,ctx):
            assert len(paths)==3
            if fail:raise ValueError('render failed')
            return 1
        @runtime.temporary_fonts
        def all(self,input,ctx):return self.one(input,ctx)+self.one(input,ctx)
    ctx=SimpleNamespace(mt=SimpleNamespace(storage=SimpleNamespace(get=lambda **kw:{})))
    p=Plugin()
    if fail:
        with pytest.raises(ValueError):p.all({},ctx)
    else:assert p.all({},ctx)==2
    assert len(paths)==3 and all(not x.exists()for x in paths)
    assert p._engine is None


def test_layout_fonts_and_saved_selection_are_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    names = ['OPPOSans-B.ttf', '剑豪体.ttf', 'SourceHanSansCN-Bold.otf', 'saved.ttf', 'config.ttf', 'layout.ttf', '字魂白鸽天行体.ttf']
    monkeypatch.setattr(runtime, 'catalog', lambda: [{'name': n} for n in names])
    downloaded = []
    monkeypatch.setattr(runtime, 'fetch_font', lambda row, directory: downloaded.append(row['name']))
    import engine
    monkeypatch.setattr(engine, 'PosterEngine', lambda **kw: SimpleNamespace(clear_image_cache=lambda: None))
    class Plugin:
        _engine = None
        def _plugin_config(self, ctx): return {'fontTitle': 'config.ttf'}
        def _layout_defaults(self, layout):
            assert layout == '荣耀视界'
            return {'font_title': 'layout.ttf', 'font_subtitle': '字魂白鸽天行体(商用需授权).ttf'}
        @runtime.temporary_fonts
        def run(self, input, ctx): return True
    ctx = SimpleNamespace(mt=SimpleNamespace(storage=SimpleNamespace(get=lambda **kw: {'found': True, 'value': {'fontTitle': 'saved.ttf', 'layout': '荣耀视界'}})))
    assert Plugin().run({}, ctx)
    assert 'saved.ttf' in downloaded and 'config.ttf' not in downloaded
    assert 'layout.ttf' in downloaded and '字魂白鸽天行体.ttf' in downloaded
