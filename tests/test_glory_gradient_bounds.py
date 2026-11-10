from pathlib import Path
import sys
import pytest
from PIL import Image, ImageDraw, ImageChops
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine import PosterEngine
NAMES=sorted(p.name for p in (ROOT/'fonts').iterdir() if p.suffix.lower() in ('.ttf','.otf'))
@pytest.fixture(scope='module')
def engine():return PosterEngine(fonts_dir=str(ROOT/'fonts'),layouts_dir=str(ROOT/'layouts'))
@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('size',[200,206])
def test_glory_gradient_keeps_full_actual_mask(engine,name,size):
    f=engine._load_text_font(name,size,'国产剧');scope=engine.layout_modules['荣耀视界'].__globals__
    actual,_,_=scope['create_gradient_text']('国产剧',f,'#ffffff','#aaaaaa',spacing=10)
    expected=Image.new('L',(1600,700));draw=ImageDraw.Draw(expected);x=200
    for ch in '国产剧':
        draw.text((x,200),ch,font=f,fill=255);x+=f.getlength(ch)+10
    placed=Image.new('L',expected.size);ox,oy=actual.info['text_origin_offset'];placed.paste(actual.getchannel('A'),(200+ox,200+oy))
    assert not ImageChops.difference(expected,placed).getbbox(),name
    actual.close();expected.close();placed.close()
