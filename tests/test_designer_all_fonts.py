"""Exercise designer's independent loader, including ASCII, not engine preload only."""
from pathlib import Path
import sys
import pytest
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine import PosterEngine
NAMES=sorted(p.name for p in (ROOT/'fonts').iterdir() if p.suffix.lower() in ('.ttf','.otf'))
@pytest.fixture(scope='module')
def engine():
    return PosterEngine(fonts_dir=str(ROOT/'fonts'),layouts_dir=str(ROOT/'layouts'))
@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('text',['国产剧','TV SHOWS','0123456789'])
def test_designer_actual_font_has_ink_and_supported_characters(engine,name,text):
    scope=engine.layout_modules['自由设计'].__globals__
    actual=scope['_load_font'](engine,name,100,text)
    assert not engine._font_missing_characters(actual,text)
    assert actual.getmask(text).getbbox()
    im=Image.new('RGBA',(1200,300));ImageDraw.Draw(im).text((20,30),text,font=actual,fill='white')
    assert im.getbbox()
    im.close()

@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('color_mode',['solid','gradient'])
@pytest.mark.parametrize('role,text,width,height,size',[('title','国产剧',820,210,160),('subtitle','TV SHOWS',820,180,64)])
def test_designer_default_title_preserves_all_ink(engine,monkeypatch,name,color_mode,role,text,width,height,size):
    scope=engine.layout_modules['自由设计'].__globals__
    records=[]
    original=ImageDraw.ImageDraw.text
    def capture(draw,xy,content,*args,**kwargs):
        if content==text:
            bbox=draw.textbbox(xy,content,font=kwargs['font'],stroke_width=kwargs.get('stroke_width',0))
            records.append(bbox)
        return original(draw,xy,content,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    element={'type':'text','textSource':role,'w':width,'h':height,'font':name,'fontSize':size,'colorMode':color_mode,'align':'left'}
    layer=scope['_element_image'](engine,element,{}, {role:text})
    assert records and layer.getbbox()
    for bbox in records:
        assert 0<=bbox[0]<=bbox[2]<=width and 0<=bbox[1]<=bbox[3]<=height,(name,bbox)
    layer.close()
