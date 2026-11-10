from types import SimpleNamespace
import pytest
from test_plugin import plugin, FakeCtx
from PIL import Image, ImageChops, ImageFont
from pathlib import Path
from engine import PosterEngine

@pytest.mark.parametrize('kind,expected', [('movies','Movie'),('tvshows','Series'),('boxsets','BoxSet'),('collections','BoxSet'),('music','MusicAlbum'),('books','Book,AudioBook'),('','Movie,Series')])
def test_independent_count_types(kind,expected):
    calls=[]
    def request(**kw):
        calls.append(kw)
        return {'status':200,'data':{'TotalRecordCount':6460,'Items':[]}}
    ctx=SimpleNamespace(mt=SimpleNamespace(emby=SimpleNamespace(request=request),log=lambda **kw:None))
    assert plugin.Plugin()._fetch_library_count(ctx,'s','l',kind)==6460
    assert calls[0]['query']['IncludeItemTypes']==expected
    assert calls[0]['query']['Limit']==0

@pytest.mark.parametrize('raw,expected',[(0,0),('0',0),(None,None),(-1,None),('bad',None),(True,None),(1.5,None)])
def test_missing_total_never_uses_candidate_count(raw,expected):
    calls=[]
    def request(**kw):
        calls.append(kw['query'])
        if kw['query']['Limit']==0:
            return {'status':200,'data':{'TotalRecordCount':raw}}
        return {'status':200,'data':{'TotalRecordCount':999,'Items':[{'Id':'1','Name':'x','ImageTags':{'Primary':'a'}}]}}
    ctx=SimpleNamespace(mt=SimpleNamespace(emby=SimpleNamespace(request=request),log=lambda **kw:None))
    posters,total,_=plugin.Plugin()._fetch_emby_posters(ctx,'s','l',plugin.EmbyDirect('http://fixture','k'),collection_type='movies')
    assert posters and total==expected
    assert all(x['IncludeItemTypes']=='Movie'for x in calls)

def test_count_http_failure_is_unknown():
    ctx=SimpleNamespace(mt=SimpleNamespace(emby=SimpleNamespace(request=lambda **kw:{'status':500,'data':{}}),log=lambda **kw:None))
    assert plugin.Plugin()._fetch_library_count(ctx,'s','l','movies') is None

def test_generation_persists_selection_without_target_scope():
    ctx=FakeCtx()
    ctx.mt.storage.set(key='ui-settings',value={'layout':'扇形展开','fontTitle':'old'})
    plugin._generation_input({'layout':'经典九宫格','fontTitle':'new','showSubtitle':False,'libraryId':'secret-target','upload':True},ctx)
    saved=ctx.mt.storage.get('ui-settings')['value']
    assert saved['layout']=='经典九宫格' and saved['fontTitle']=='new'
    assert saved['showSubtitle'] is False
    assert 'libraryId'not in saved and 'upload'not in saved
    assert plugin._generation_input({},ctx)['layout']=='经典九宫格'

def test_batch_preserves_custom_fonts_and_internal_calls_do_not_write_settings():
    ctx=FakeCtx()
    p=plugin.Plugin()
    selected={'serverId':'s1','layout':'经典九宫格','fontTitle':'custom.ttf','fontSubtitle':'english.ttf','badgeFont':'digits.ttf','showSubtitle':False}
    p._cover_libraries=lambda *args:[{'Id':'a','Name':'普通库'},{'Id':'b','Name':'罕见字龘库'}]
    p.engine=lambda:SimpleNamespace(clear_image_cache=lambda:None)
    received=[]
    def generate(input,ctx):
        received.append(dict(input))
        # 模拟库内缺字后的内部设置，必须不污染用户保存值。
        plugin._generation_input({**input,'fontTitle':'fallback.ttf'},ctx)
        return {'uploaded':True,'size':1}
    p.generate_cover=generate
    result=p.generate_all(selected,ctx)
    assert result['uploaded']==2
    for item in received:
        assert item['fontTitle']=='custom.ttf' and item['fontSubtitle']=='english.ttf'
        assert item['badgeFont']=='digits.ttf' and item['layout']=='经典九宫格'
        assert item['_batch_internal'] is True
        assert 'forceUniformTitleFont'not in item
    assert ctx.mt.storage.get('ui-settings')['value']['fontTitle']=='custom.ttf'

def test_zero_count_draws_but_unknown_does_not():
    root=Path(__file__).resolve().parents[1]
    engine=PosterEngine(fonts_dir=str(root/'fonts'),layouts_dir=str(root/'layouts'))
    font=ImageFont.truetype(str(root/'fonts/OPPOSans-B.ttf'),50)
    image=Image.new('RGBA',(533,300),'#234567')
    config={'badge_style':'box','badge_size':50,'badge_font':'OPPOSans-B.ttf'}
    assert engine._draw_badge(image,config,None,{'main':font}) is image
    zero=engine._draw_badge(image.copy(),config,0,{'main':font})
    assert ImageChops.difference(image.convert('RGB'),zero.convert('RGB')).getbbox()
