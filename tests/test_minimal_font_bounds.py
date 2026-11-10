"""高metrics中文主标与英文回退副标的默认画布边界。"""
from pathlib import Path
import sys
import pytest
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine import PosterEngine

@pytest.mark.parametrize('sizes',[(180,90),(200,100)])
def test_minimal_high_metrics_fallback_stays_inside_canvas(sizes):
    engine=PosterEngine(fonts_dir=str(ROOT/'fonts'),layouts_dir=str(ROOT/'layouts'))
    title,sub='纪录电影','COLLECTIONS'
    fonts={'main':engine._load_text_font('港式茶点-新蒂下午茶基本版.ttf',sizes[0],title),
           'sub':engine._load_text_font('港式茶点-新蒂下午茶基本版.ttf',sizes[1],sub)}
    config=dict(title=title,subtitle=sub,text_top_percent=60,text_left_percent=3,
                text_width_percent=80,gap_1=20,mask_opacity=0,brightness=1)
    boxes=[]
    class Context:
        def __getattr__(self,key):return getattr(engine,key)
        def draw_text_wrapper(self,draw,text,x,y,font,max_width,fill,**kw):
            boxes.append(engine.wrapped_text_ink_bbox(text,x,y,font,max_width,**kw))
            return engine.draw_text_wrapper(draw,text,x,y,font,max_width,fill,**kw)
    canvas=Image.new('RGBA',(1920,1080))
    result=engine.layout_modules['极简'](Context(),canvas,config,{},fonts)
    assert min(b[1] for b in boxes)>=0
    assert max(b[3] for b in boxes)<=1080
    assert [fonts[k].size for k in ('main','sub')]==list(sizes)
    result.close()
