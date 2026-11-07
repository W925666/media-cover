"""同素材、字体、随机种子下与固定上游逐像素对比（静态布局）。"""
import importlib.util, random, sys
from pathlib import Path
from PIL import Image, ImageChops
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.engine import PosterEngine
UP=Path('/tmp/media-cover-upstream-audit/defaults/layouts')
engine=PosterEngine(str(ROOT/'fonts'),str(ROOT/'layouts'))
# Pillow effect_noise 使用不可设种子的内部 RNG；两侧复用相同噪声样本。
noise_original = Image.effect_noise
noise_samples = {}
def same_noise(size, sigma):
    key = (size, sigma)
    if key not in noise_samples: noise_samples[key] = noise_original(size, sigma)
    return noise_samples[key].copy()
Image.effect_noise = same_noise
paths=[str(p) for p in Path('/tmp/cover-visual-check').glob('poster-*.jpg')]
assets={'posters':paths,'bg_url':paths[0],'backdrops':paths,'poster_backdrops':paths,'count':2505}
for name,render in engine.layout_modules.items():
    if '动态' in name: continue
    spec=importlib.util.spec_from_file_location('original',UP/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # 两边解析同一套上游附带装饰资源，避免比较路径造成资源缺失。
    module.__file__ = str(ROOT/'layouts'/(name+'.py'))
    config={'title':'欧美剧','subtitle':'WESTERN TV SERIES','enable_animation':False}
    schema=module.get_schema()
    for group in schema:
        for field in group.get('items',[]):
            if 'default' in field:config.setdefault(field['key'],field['default'])
    fonts={'main':engine._load_text_font(config.get('font_title'),int(config.get('title_size',160)),config['title']),
           'sub':engine._load_text_font(config.get('font_subtitle',config.get('font_sub')),int(config.get('subtitle_size',80)),config['subtitle'])}
    import numpy as np
    np.random.seed(123)
    random.seed(123);a=render(engine,Image.new('RGBA',(1920,1080),(20,30,40,255)),config,assets,fonts)
    engine.clear_image_cache()
    np.random.seed(123)
    random.seed(123);b=module.render(engine,Image.new('RGBA',(1920,1080),(20,30,40,255)),config,assets,fonts)
    assert a.size==b.size and ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox() is None,name
    a.close();b.close();engine.clear_image_cache();print('PIXEL PASS',name)
