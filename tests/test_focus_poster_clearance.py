"""真实聚焦海报本体与正文墨迹避让；背景上的设计叠字不计错误。"""
import json
from pathlib import Path
import sys
import pytest
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine
NAMES = sorted(p.name for p in (ROOT/'fonts').iterdir() if p.suffix.lower() in ('.ttf', '.otf'))

@pytest.fixture(scope='module')
def engine():
    return PosterEngine(fonts_dir=str(ROOT/'fonts'), layouts_dir=str(ROOT/'layouts'))

def defaults(engine, layout):
    config = {}
    for path in sorted((ROOT/'templates').glob('*.json')):
        doc = json.loads(path.read_text())
        if doc.get('engine') == layout:
            config.update(doc['config'])
            break
    for group in engine.layout_modules[layout].__globals__['get_schema']():
        for field in group.get('items', []):
            config.setdefault(field['key'], field.get('default'))
    return config

class Context:
    def __init__(self, engine, size):
        self.engine = engine
        self.text = Image.new('L', size)
    def __getattr__(self, name):
        return getattr(self.engine, name)
    def download_img(self, url):
        return Image.new('RGBA', (100,150), '#ec3977')
    def draw_text_wrapper(self, draw, text, x, y, font, width, fill, **kw):
        self.engine.draw_text_wrapper(ImageDraw.Draw(self.text), text, x, y, font, width, 255, **kw)
        return self.engine.draw_text_wrapper(draw, text, x, y, font, width, fill, **kw)

@pytest.mark.parametrize('font_name', NAMES)
@pytest.mark.parametrize('mode', ['default', '200/100'])
@pytest.mark.parametrize('layout,step', [('聚焦C佬',0),('聚焦(动态)',0),('聚焦(动态)',.095),('聚焦(动态)',.105)])
def test_focus_real_text_does_not_enter_hero(engine, monkeypatch, font_name, mode, layout, step):
    size = (1920,1080) if layout == '聚焦C佬' else (533,300)
    config = defaults(engine,layout)
    config.update(title='国产剧', subtitle='TV SHOWS', poster_count=9, bg_blur=0)
    if mode == '200/100':
        config.update(title_size=200, subtitle_size=100)
    if size[0] != 1920:
        config = engine._dynamic_render_config(config,layout,size[0]/1920)
    fonts = {'main':engine._load_text_font(font_name,config['title_size'],'国产剧'),
             'sub':engine._load_text_font(font_name,config['subtitle_size'],'TV SHOWS')}
    ctx=Context(engine,size)
    hero_h = int(size[1] * float(config['hero_height_percent'])/100)
    hero_size = (int(hero_h*float(config['poster_ratio'])),hero_h)
    heroes=[]
    old_paste,old_composite=Image.Image.paste,Image.Image.alpha_composite
    def paste(self,im,box=None,mask=None):
        if self.size==size and isinstance(im,Image.Image) and im.size==hero_size and mask is im:
            # Only poster body: reflections are below baseline and have faded alpha.
            if im.getpixel((im.width//2,0))[3] == 255:
                heroes.append((box[0],box[1],box[0]+im.width,box[1]+im.height))
        return old_paste(self,im,box,mask)
    def composite(self,im,dest=(0,0),source=(0,0)):
        if self.size==size and im.size==hero_size and im.getpixel((im.width//2,0))[3]==255:
            heroes.append((dest[0],dest[1],dest[0]+im.width,dest[1]+im.height))
        return old_composite(self,im,dest,source)
    monkeypatch.setattr(Image.Image,'paste',paste)
    monkeypatch.setattr(Image.Image,'alpha_composite',composite)
    args=(ctx,Image.new('RGBA',size,'#142635'),config,{'posters':['fixture']*9},fonts)
    result=engine.layout_modules[layout](*args,step=step) if '(动态)' in layout else engine.layout_modules[layout](*args)
    assert heroes, (layout,hero_size)
    bounds=ctx.text.getbbox()
    assert bounds, font_name
    for hero in heroes:
        assert not ctx.text.crop(hero).getbbox(), (layout,font_name,mode,step,'text',bounds,'hero',hero)
        assert bounds[3] <= hero[1], (layout,font_name,mode,step,'text',bounds,'hero',hero)
        original_top = int(size[1]*float(config['baseline_percent'])/100)-hero_h
        expected_top = bounds[3]+max(4,size[1]//100) if bounds[3]>original_top else original_top
        assert hero[1] == expected_top, ('non-colliding layout must not move',hero,expected_top)
        assert hero[1] < size[1], ('hero pushed entirely off canvas',hero)
    result.close();ctx.text.close()
