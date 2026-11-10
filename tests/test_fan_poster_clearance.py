"""扇形海报真实旋转alpha与文字保留区，不以本体几何或已裁切画布估算。"""
from pathlib import Path
import json
import sys
import pytest
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine

@pytest.fixture(scope='module')
def engine():
    return PosterEngine(fonts_dir=str(ROOT/'fonts'), layouts_dir=str(ROOT/'layouts'))

class Context:
    def __init__(self, engine, size, no_background=False):
        self.engine, self.size = engine, size
        self.first = no_background
        self.text = Image.new('RGBA', size)
    def __getattr__(self, key):
        return getattr(self.engine, key)
    def download_img(self, url):
        if self.first:
            self.first=False
            return None
        return Image.new('RGBA', (100,150), (240,70,25,255))
    def draw_text_wrapper(self, draw, text, x, y, font, width, fill, **kw):
        self.engine.draw_text_wrapper(ImageDraw.Draw(self.text), text, x, y, font, width, fill, **kw)
        return self.engine.draw_text_wrapper(draw, text, x, y, font, width, fill, **kw)

def config_and_fonts(engine, layout, size):
    name='preset_1785762000000.json' if '(动态)' in layout else 'preset_1769089761543.json'
    config=json.loads((ROOT/'templates'/name).read_text())['config']
    config.update(title='国产剧',subtitle='TV SHOWS',title_size=200,subtitle_size=100,
                  bg_blur=0,bg_darkness=0)
    if size[0]!=1920:
        config=engine._dynamic_render_config(config,layout,size[0]/1920)
    fonts={'main':engine._load_text_font('料事如神SentyZHAO.ttf',config['title_size'],'国产剧'),
           'sub':engine._load_text_font('方正字迹-鸿飞汉魏简体.TTF',config['subtitle_size'],'TV SHOWS')}
    return config, fonts

@pytest.mark.parametrize('count',[3,5,9])
def test_static_final_card_alpha_stays_below_text(engine, monkeypatch, count):
    size=(1920,1080);ctx=Context(engine,size,no_background=True)
    config,fonts=config_and_fonts(engine,'扇形展开',size)
    # 压力位置强制触发真实避让，而非默认布局下偶然已有空白。
    config.update(poster_count=count,layout_y_offset=100)
    records=[];original=Image.Image.paste
    def capture(self,im,box=None,mask=None):
        if self.size==size and isinstance(im,Image.Image) and im.mode=='RGBA' and mask is im:
            bounds=im.getchannel('A').getbbox()
            if bounds:records.append((box,bounds))
        return original(self,im,box,mask)
    monkeypatch.setattr(Image.Image,'paste',capture)
    result=engine.layout_modules['扇形展开'](ctx,Image.new('RGBA',size),config,{'posters':['fixture']*count},fonts)
    safe=ctx.text.getbbox()[3]+max(16,size[1]//45)
    assert len(records)==count
    assert min(pos[1]+bbox[1] for pos,bbox in records)>=safe
    assert any(pos[1]+bbox[1]<size[1] for pos,bbox in records)
    result.close();ctx.text.close()

@pytest.mark.parametrize('count',[3,5,9])
@pytest.mark.parametrize('progress',[0,.25,.5,.9,1])
def test_533_dynamic_real_alpha_clearance_and_uniform_shift(engine,monkeypatch,count,progress):
    size=(533,300);ctx=Context(engine,size)
    config,fonts=config_and_fonts(engine,'扇形展开(动态)',size)
    config.update(poster_count=count,layout_y_offset=28)
    scope=engine.layout_modules['扇形展开(动态)'].__globals__
    scope['_draw_text'](ctx,Image.new('RGBA',size),config,fonts)
    safe=ctx.text.getbbox()[3]+max(16,size[1]//45)
    pairs=[('fixture',None)]*count
    canvases=[Image.new('RGBA',size),Image.new('RGBA',size)]
    records=[[],[]];original=Image.Image.alpha_composite
    def capture(self,im,dest=(0,0),source=(0,0)):
        for index,canvas in enumerate(canvases):
            if self is canvas:
                bounds=im.getchannel('A').getbbox()
                if bounds:records[index].append((dest,bounds))
        return original(self,im,dest,source)
    monkeypatch.setattr(Image.Image,'alpha_composite',capture)
    for index,top in enumerate([0,safe]):
        scope['_draw_fan'](ctx,canvases[index],dict(config,_text_safe_top=top),pairs,1,progress)
    assert records[1]
    assert min(pos[1]+box[1] for pos,box in records[1])>=safe
    # 同组每张海报位移相同，不能逐张向下推坏扇形。
    deltas={after[0][1]-before[0][1] for before,after in zip(records[0],records[1])}
    assert len(deltas)==1
    assert canvases[1].getchannel('A').getbbox()[1]>=safe
    assert canvases[1].getchannel('A').getbbox()[3]>safe
    for canvas in canvases:canvas.close()
    ctx.text.close()
