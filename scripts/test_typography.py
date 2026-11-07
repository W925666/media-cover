"""离线多布局文字回归：真实字体、长短中英文、静态与动态首帧。"""
import sys
import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from engine.engine import PosterEngine


def main():
    output = Path('/tmp/cover-typography-regression')
    output.mkdir(exist_ok=True)
    paths = []
    for i in range(9):
        path = output / f'poster-{i}.jpg'
        Image.new('RGB', (400, 600), (40+i*18, 60+i*10, 100)).save(path)
        paths.append(str(path))
    engine = PosterEngine(str(ROOT/'fonts'), str(ROOT/'layouts'))
    for name, render in engine.layout_modules.items():
        schema = render.__globals__.get('get_schema')
        config = {'engine': name}
        if schema:
            for group in schema():
                for field in group.get('items', []):
                    if 'key' in field and 'default' in field:
                        config[field['key']] = field['default']
        for long in (False, True):
            config.update(title='糖心Vlog\n&玩偶姐姐' if long else '欧美剧',
                          subtitle='WESTERN TELEVISION SERIES COLLECTION' if long else 'WESTERN TV SERIES',
                          frame_index=0, frame_progress=0.0, enable_animation=False)
            fonts = {'main': engine._load_text_font('OPPOSans-B.ttf', int(config.get('title_size',160)),config['title']),
                     'sub': engine._load_text_font('剑豪体.ttf',int(config.get('subtitle_size',config.get('sub_size',80))),config['subtitle']),
                     'count':engine._load_text_font('OPPOSans-B.ttf',40,'2505')}
            result = render(engine, Image.new('RGBA',(1920,1080),(25,30,40,255)),config,
                            {'posters':paths,'bg_url':paths[0],'backdrops':paths,'poster_backdrops':paths,'count':2505},fonts)
            assert result.size==(1920,1080), (name,result.size)
            result.convert('RGB').save(output/(name+('-long' if long else '-short')+'.jpg'))
            result.close()
            engine.clear_image_cache()
        print('PASS',name)
    print('Outputs:', output)

if __name__=='__main__':
    main()
