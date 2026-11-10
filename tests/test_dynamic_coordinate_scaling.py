"""真实 draw/GIF 回归；素材全部是本地生成的纯色图，不访问网络。"""
import base64
from io import BytesIO
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import PosterEngine

ROOT = Path(__file__).resolve().parents[1]
SCALE = 533 / 1920


@pytest.fixture
def engine():
    return PosterEngine(fonts_dir=str(ROOT / 'fonts'), layouts_dir=str(ROOT / 'layouts'))


@pytest.fixture
def assets(tmp_path):
    posters = []
    for index, color in enumerate(('#bb3020', '#2060bb', '#20aa60')):
        path = tmp_path / f'plain-{index}.png'
        Image.new('RGB', (120, 180), color).save(path)
        posters.append(str(path))
    background = tmp_path / 'plain-background.png'
    Image.new('RGB', (320, 180), '#101820').save(background)
    return {'bg_url': str(background), 'posters': posters,
            'poster_backdrops': [str(background)] * 3, 'count': 3}


def config(layout, **values):
    return dict(engine=layout, title='TEST', subtitle='PLAIN',
                font_title='OPPOSans-B.ttf', font_subtitle='OPPOSans-B.ttf',
                title_size=160, subtitle_size=80, enable_animation=True,
                anim_width=533, anim_duration=500, switch_interval=500,
                badge_style='none', grain_intensity=0, **values)


def decode(encoded):
    image = Image.open(BytesIO(base64.b64decode(encoded)))
    image.load()
    return image


@pytest.mark.parametrize('width', (320, 533, 960))
def test_signed_pixels_zero_and_dimensionless_parameters(engine, width):
    scale = width / 1920
    original = config('扇形展开(动态)', title_offset_y=-400,
                      subtitle_offset_y=-171, fan_radius=850, layout_y_offset=491,
                      shadow_blur=0, shadow_offset_x=-15, layer_offset_y=-40,
                      center_scale=0.5, fan_spread=90, poster_ratio=0.67,
                      title_opacity=255, transition_ms=500)
    before = dict(original)
    scaled = engine._dynamic_render_config(original, original['engine'], scale)
    for key in ('title_offset_y', 'subtitle_offset_y', 'fan_radius',
                'layout_y_offset', 'shadow_blur', 'shadow_offset_x', 'layer_offset_y'):
        assert scaled[key] == round(original[key] * scale)
    for key in ('center_scale', 'fan_spread', 'poster_ratio', 'title_opacity', 'transition_ms'):
        assert scaled[key] == original[key]
    assert original == before
    grid = engine._dynamic_render_config({'title_x': 5, 'title_y': 40}, '经典九宫格(动态)', SCALE)
    assert (grid['title_x'], grid['title_y']) == (5, 40)
    assert grid['start_y'] == round(-200 * SCALE)
    assert grid['column_lift'] == round(-80 * SCALE)


@pytest.mark.parametrize('layout', tuple(PosterEngine._DYNAMIC_LAYOUT_PIXELS))
def test_real_draw_all_dynamic_layouts_scale_explicit_and_default_pixels(engine, assets, layout):
    renderer = engine.layout_modules[layout]
    calls = []
    # Spy delegates to the real renderer: no dummy render or draw replacement.
    def observed(ctx, bg, frame_config, frame_assets, fonts, **kwargs):
        calls.append((dict(frame_config), bg.size))
        return renderer(ctx, bg, frame_config, frame_assets, fonts, **kwargs)
    engine.layout_modules = dict(engine.layout_modules, **{layout: observed})
    original = config(layout)
    output = decode(engine.draw(original, assets))
    assert output.format == 'GIF'
    assert output.size == (533, 300)
    assert calls
    for supplied, size in calls:
        assert size == output.size
        for key, default in engine._DYNAMIC_LAYOUT_PIXELS[layout].items():
            assert supplied[key] == round(default * SCALE), (layout, key)
        assert supplied['title_size'] == round(160 * SCALE)
    # Supplying identical 1920-reference defaults must not change output or scale twice.
    explicit = dict(original, **engine._DYNAMIC_LAYOUT_PIXELS[layout])
    assert engine.draw(original, assets) == engine.draw(explicit, assets)


def test_fan_text_and_posters_are_visible_with_original_negative_offsets(engine, assets):
    original = config('扇形展开(动态)', title_offset_y=-400,
                      subtitle_offset_y=-171, fan_radius=850, layout_y_offset=491)
    text_calls = []
    real_wrapper = engine.draw_text_wrapper
    def observed(draw, text, x, y, font, max_width, fill, **kwargs):
        end = real_wrapper(draw, text, x, y, font, max_width, fill, **kwargs)
        text_calls.append((text, y, end, font.size))
        return end
    engine.draw_text_wrapper = observed
    image = decode(engine.draw(original, assets)).convert('RGB')
    assert text_calls
    for text, y, end, size in text_calls:
        assert 0 <= y < end < 300, (text, y, end, size)
    # Bright neutral glyphs survive GIF quantization, not merely an on-canvas origin.
    bright = [(x, y) for y in range(160) for x in range(533)
              if min(image.getpixel((x, y))) > 120]
    assert bright and min(y for x, y in bright) < 90
    # Red/green/blue poster pixels remain in the bottom half of the canvas.
    assert any(max(image.getpixel((x, y))) - min(image.getpixel((x, y))) > 70
               for y in range(150, 300, 3) for x in range(0, 533, 3))
    assert original['title_offset_y'] == -400


def test_static_draw_retains_original_config_fonts_and_pixels(engine, assets):
    layout = '扇形展开(动态)'
    renderer = engine.layout_modules[layout]
    calls = []
    def observed(ctx, bg, supplied, supplied_assets, fonts, **kwargs):
        calls.append((dict(supplied), bg.size, fonts['main'].size))
        return renderer(ctx, bg, supplied, supplied_assets, fonts, **kwargs)
    engine.layout_modules = dict(engine.layout_modules, **{layout: observed})
    original = config(layout, title_offset_y=-400, subtitle_offset_y=-171,
                      fan_radius=850, layout_y_offset=491)
    original['enable_animation'] = False
    image = decode(engine.draw(original, assets))
    assert image.format == 'JPEG' and image.size == (1920, 1080)
    assert calls == [(original, (1920, 1080), 160)]


def test_wrapper_metrics_contract_and_small_font_floor_remain_local(engine):
    # Existing metrics include ascent + descent + default 10px spacing. Do not
    # replace them globally with glyph bounds merely to tighten animation text.
    from PIL import ImageDraw
    font = engine._load_text_font('OPPOSans-B.ttf', 1, 'TEST')
    assert font.size == 8
    image = Image.new('RGBA', (100, 100))
    end = engine.draw_text_wrapper(ImageDraw.Draw(image), 'A\nB', 0, 0, font, 100, 'white')
    assert end == 2 * (sum(font.getmetrics()) + 10)
