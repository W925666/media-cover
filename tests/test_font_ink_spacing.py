"""墨迹超出 metrics 的字体组合仍保留至少10px真实间距。"""
from pathlib import Path
import sys
import math
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine


@pytest.fixture(scope='module')
def engine():
    return PosterEngine(fonts_dir=str(ROOT / 'fonts'), layouts_dir=str(ROOT / 'layouts'))


class InkContext:
    def __init__(self, engine):
        self.engine = engine
        self.layers = []
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def download_img(self, source):
        return None

    def draw_text_wrapper(self, draw, text, x, y, font, max_width, fill, **kwargs):
        # 真Pillow绘制独立蒙版，测量可见像素而非仅验证坐标公式。
        mask = Image.new('L', (1920, 1800))
        end = self.engine.draw_text_wrapper(ImageDraw.Draw(mask), text, x, y,
                                            font, max_width, 255, **kwargs)
        self.layers.append(mask)
        self.calls.append((text, x, y, font, end))
        return self.engine.draw_text_wrapper(draw, text, x, y, font, max_width, fill, **kwargs)


@pytest.mark.parametrize('layout', ['扇形展开', '扇形展开(动态)'])
@pytest.mark.parametrize('main_font,sub_font,title', [
    ('叶根友毛笔行书.ttf', '料事如神SentyZHAO.ttf', '国产剧\n国产剧'),
    ('剑豪体.ttf', '剑豪体.ttf', '国产剧'),
    ('料事如神SentyZHAO.ttf', '方正字迹-鸿飞汉魏简体.TTF', '国产剧'),
    ('OPPOSans-B.ttf', '料事如神SentyZHAO.ttf', '国产剧'),
])
def test_fan_default_sizes_actual_ink_gap(engine, layout, main_font, sub_font, title):
    ctx = InkContext(engine)
    fonts = {'main': engine._load_text_font(main_font, 200, title),
             'sub': engine._load_text_font(sub_font, 100, '国产剧')}
    config = dict(title=title, subtitle='国产剧', title_offset_y=-400,
                  subtitle_offset_y=-171, title_size=200, subtitle_size=100,
                  poster_count=1)
    bg = Image.new('RGBA', (1920, 1080))
    engine.layout_modules[layout](ctx, bg, config, {'posters': ['fixture']}, fonts)
    assert len(ctx.layers) == 2
    main_box, sub_box = [layer.getbbox() for layer in ctx.layers]
    assert main_box and sub_box
    assert sub_box[1] - main_box[3] >= 9  # fractional center raster rounding tolerance
    assert ctx.calls[0][3].size == 200
    assert ctx.calls[1][3].size == 100
    if main_font == '剑豪体.ttf':
        # 正常原版短标题保持已有metrics返回值驱动的坐标，不额外向下挪动。
        assert ctx.calls[1][2] == max(369, ctx.calls[0][4] + 10)


def test_shared_wrap_measurement_matches_real_pillow(engine):
    text = '国产剧\n国产剧'
    font = engine._load_text_font('叶根友毛笔行书.ttf', 200, text)
    box = engine.wrapped_text_ink_bbox(text, 960, 140, font, 1920, align='center')
    image = Image.new('L', (1920, 1500))
    end = engine.draw_text_wrapper(ImageDraw.Draw(image), text, 960, 140, font, 1920, 255, align='center')
    actual = image.getbbox()
    # FreeType bbox可包含字侧留白；纵向才是间距计算所需的真实上下界。
    assert abs(actual[1] - box[1]) <= 1
    assert abs(actual[3] - box[3]) <= 1
    assert actual[0] >= math.floor(box[0]) and actual[2] <= math.ceil(box[2])
    assert end == 140 + 2 * (sum(font.getmetrics()) + 10)
    assert engine.wrapped_text_ink_bbox('', 0, 0, font, 100) is None
    assert engine.wrapped_text_ink_bbox(' \n ', 0, 0, font, 100) is None
