"""荣耀视界必须沿用引擎已解析的字形回退，而非重开缺字源字体。"""
from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine

@pytest.mark.parametrize('requested,text,key,size', [
    ('Bodoni MT Black.TTF', '国产剧', 'main', 200),
    ('港式茶点-新蒂下午茶基本版.ttf', 'TV SHOWS', 'sub', 100),
])
def test_glory_keeps_resolved_font(requested, text, key, size):
    pytest.importorskip('numpy')
    engine = PosterEngine(fonts_dir=str(ROOT/'fonts'), layouts_dir=str(ROOT/'layouts'))
    resolved = engine._load_text_font(requested, size, text)
    loader = engine.layout_modules['荣耀视界'].__globals__['get_font_obj']
    actual = loader({key: resolved}, requested, size, fallback_key=key)
    assert Path(actual.path).name == Path(resolved.path).name
    assert not engine._font_missing_characters(actual, text)
    assert actual.size == size
