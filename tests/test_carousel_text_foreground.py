"""真实533画布：轮播海报不得覆盖已绘制正文（含半透明副标及遮罩）。"""
from pathlib import Path
import json
import sys

import pytest
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine

FONTS = sorted(p.name for p in (ROOT / 'fonts').iterdir() if p.suffix.lower() in ('.ttf', '.otf'))
SIZE = (533, 300)


@pytest.fixture(scope='module')
def engine():
    return PosterEngine(fonts_dir=str(ROOT / 'fonts'), layouts_dir=str(ROOT / 'layouts'))


class Context:
    def __init__(self, engine):
        self.engine = engine
        self.ink = Image.new('L', SIZE)
        self.after_text = None
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def download_img(self, url):
        return Image.new('RGBA', (120, 180), (234, 56, 147, 255))

    def draw_text_wrapper(self, draw, text, x, y, font, width, fill, **kwargs):
        # Both paths delegate the real wrapping/drawing implementation; ink is
        # coverage only, while after_text retains real overlay/alpha RGB values.
        self.engine.draw_text_wrapper(ImageDraw.Draw(self.ink), text, x, y, font, width, 255, **kwargs)
        result = self.engine.draw_text_wrapper(draw, text, x, y, font, width, fill, **kwargs)
        if self.after_text is not None:
            self.after_text.close()
        self.after_text = draw._image.copy()
        self.calls += 1
        return result

    def close(self):
        self.ink.close()
        if self.after_text is not None:
            self.after_text.close()


def changed_ink_pixels(image, snapshot, ink):
    diff = ImageChops.difference(image.convert('RGB'), snapshot.convert('RGB'))
    channels = diff.split()
    changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    return ImageChops.multiply(changed, ink).getbbox()


@pytest.mark.parametrize('font_name', FONTS)
@pytest.mark.parametrize('size_mode', ['default', '200/100'])
@pytest.mark.parametrize('phase', [0, .85, .9, .95, .999])
@pytest.mark.parametrize('position', ['default', 'overlap-pressure'])
def test_real_carousel_preserves_foreground_text(engine, font_name, size_mode, phase, position):
    scope = engine.layout_modules['经典平铺(动态)'].__globals__
    config = json.loads((ROOT / 'templates/preset_1769148057781.json').read_text())['config']
    config.update(title='国产剧', subtitle='TV SHOWS')
    if size_mode == '200/100':
        config.update(title_size=200, subtitle_size=100)
    config = engine._dynamic_render_config(config, '经典平铺(动态)', SIZE[0] / 1920)
    if position == 'overlap-pressure':
        config.update(text_top_percent=30, text_width_percent=90,
                      poster_baseline_percent=65, poster_height_percent=50)
    fonts = {'main': engine._load_text_font(font_name, config['title_size'], config['title']),
             'sub': engine._load_text_font(font_name, config['subtitle_size'], config['subtitle'])}
    for role, text in [('main', config['title']), ('sub', config['subtitle'])]:
        assert fonts[role].getmask(text).getbbox(), (font_name, role)
        assert not engine._font_missing_characters(fonts[role], text)
    assets = {'posters': [f'poster-{i}' for i in range(8)]}
    step = phase / 8
    ctx = Context(engine)
    background = Image.new('RGBA', SIZE, (28, 63, 81, 255))
    result = scope['render'](ctx, background, config, assets, fonts, step)
    assert result.size == SIZE
    assert ctx.calls == 2 and ctx.ink.getbbox()
    # Compare actual post-wrapper pixels, rather than requiring opaque white
    # pixels: subtitle alpha=210 and the horizontal shade remain unchanged.
    assert changed_ink_pixels(result, ctx.after_text, ctx.ink) is None

    if position == 'overlap-pressure':
        # Independently execute the former real order to ensure these are not
        # vacuous empty/missing-poster cases or an order-only mocked assertion.
        old_ctx = Context(engine)
        pairs = scope['_poster_pairs'](assets, config['poster_count'])
        current, upcoming, slide = scope['_timeline_state'](
            step, len(pairs), config['transition_ms'] / config['switch_interval'])
        old = scope['_background_for_state'](old_ctx, background, config, pairs, current, upcoming, slide)
        old = scope['_draw_overlay_and_text'](old_ctx, old, config, fonts)
        old = scope['_draw_carousel'](old_ctx, old, config, pairs, current, slide)
        assert changed_ink_pixels(old, old_ctx.after_text, old_ctx.ink) is not None, (
            font_name, size_mode, phase, 'pressure must fail on former painter order')
        old.close()
        old_ctx.close()
    result.close()
    background.close()
    ctx.close()
