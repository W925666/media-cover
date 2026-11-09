"""PosterEngine 冒烟测试：不依赖 MediaTidy 宿主，本地可跑。

运行: python3 -m pytest tests/ -q   或   python3 tests/test_engine.py
依赖: Pillow（与 pyproject.toml 锁定的 pillow==11.1.0 兼容）
"""
import base64
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageFont  # noqa: E402

from engine import PosterEngine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "fonts")
LAYOUTS = os.path.join(ROOT, "layouts")

ALL_LAYOUTS = [
    "经典九宫格", "经典堆叠", "经典平铺", "极简", "海报墙",
    "扇形展开", "斜线分割", "自由设计", "旋转",
]


def _assets(tmp):
    posters = []
    for i in range(9):
        p = os.path.join(tmp, f"p{i}.jpg")
        Image.new("RGB", (400, 600), (i * 25 % 255, 100, 150)).save(p)
        posters.append(p)
    bg = os.path.join(tmp, "bg.jpg")
    Image.new("RGB", (1920, 1080), (30, 40, 60)).save(bg)
    return bg, posters


def _config(layout):
    return {
        "engine": layout,
        "title": "测试标题 Test",
        "subtitle": "2026",
        "font_title": "OPPOSans-B.ttf",
        "font_subtitle": "OPPOSans-B.ttf",
        "title_size": 160,
        "subtitle_size": 80,
        "enable_animation": False,
    }


def test_all_layouts_render():
    with tempfile.TemporaryDirectory() as tmp:
        bg, posters = _assets(tmp)
        engine = PosterEngine(fonts_dir=FONTS, layouts_dir=LAYOUTS)
        assert set(ALL_LAYOUTS) <= set(engine.layout_modules), engine.layout_modules.keys()
        for layout in ALL_LAYOUTS:
            assets = {"bg_url": bg, "posters": posters, "backdrops": [],
                      "poster_backdrops": [], "count": len(posters)}
            data = base64.b64decode(engine.draw(_config(layout), assets))
            img = Image.open(BytesIO(data))
            assert img.size == (1920, 1080), (layout, img.size)
            assert len(data) > 10000, (layout, len(data))


def test_unknown_layout_falls_back_not_solid():
    """未知布局必须回退到默认布局，而不是静默返回纯色背景。"""
    with tempfile.TemporaryDirectory() as tmp:
        bg, posters = _assets(tmp)
        engine = PosterEngine(fonts_dir=FONTS, layouts_dir=LAYOUTS)
        assets = {"bg_url": bg, "posters": posters, "backdrops": [],
                  "poster_backdrops": [], "count": len(posters)}
        solid = base64.b64decode(engine.draw(_config("不存在的布局"), assets))
        # 纯色背景 JPEG 约 33KB；回退渲染（含标题/素材）应显著更大
        assert len(solid) > 50000, len(solid)


def test_image_cache_is_pixel_bounded_and_sources_are_downscaled():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "large.png")
        Image.new("RGB", (3000, 2000), (20, 40, 60)).save(path)
        engine = PosterEngine(fonts_dir=FONTS, layouts_dir=LAYOUTS)
        image = engine.download_img(path)
        assert image is not None
        assert max(image.size) == 1920
        assert engine._img_cache_pixels <= engine._img_cache_max_pixels
        assert path in engine._img_cache


def test_default_dirs_work_from_any_cwd():
    """不传 fonts/layouts 时应自动定位项目根目录。"""
    cwd = os.getcwd()
    try:
        os.chdir(tempfile.gettempdir())
        engine = PosterEngine()
        assert len(engine.layout_modules) >= 9
        assert os.path.exists(engine.default_font_path)
    finally:
        os.chdir(cwd)


class _TextRecorder:
    """记录实际绘制行及字体，不依赖截图像素判断换行。"""

    def __init__(self):
        self.calls = []

    def text(self, position, text, font, fill):
        self.calls.append((position, text, font, fill))


def _wrap_text(text, width, align='left', fill='white', draw=None):
    engine = PosterEngine(fonts_dir=FONTS, layouts_dir=LAYOUTS)
    font = ImageFont.truetype(os.path.join(FONTS, 'OPPOSans-B.ttf'), 64)
    draw = draw if draw is not None else _TextRecorder()
    end_y = engine.draw_text_wrapper(draw, text, 300, 20, font, width, fill,
                                     line_spacing=7, align=align)
    return draw, font, end_y


def test_wrapper_wraps_english_words_without_shrinking_normal_titles():
    font = ImageFont.truetype(os.path.join(FONTS, 'OPPOSans-B.ttf'), 64)
    # 原实现会绘制 ANIMATED MO / VIES。
    width = font.getlength('ANIMATED MO')
    draw, original, end_y = _wrap_text('ANIMATED MOVIES', width)
    assert [call[1] for call in draw.calls] == ['ANIMATED', 'MOVIES']
    assert all(call[2].size == original.size for call in draw.calls)
    height = sum(original.getmetrics()) + 7
    assert end_y == 20 + 2 * height
    assert draw.calls[1][0][1] == 20 + height


def test_wrapper_preserves_explicit_size_for_overlong_word_and_alignment():
    text = 'Movies Supercalifragilisticexpialidocious'
    width = 280
    for align in ('left', 'center', 'right'):
        draw, original, end_y = _wrap_text(text, width, align)
        assert ' '.join(call[1] for call in draw.calls) == text
        sizes = {call[2].size for call in draw.calls}
        assert len(sizes) == 1
        assert next(iter(sizes)) == original.size
        for position, line, font, fill in draw.calls:
            line_width = font.getlength(line)
            if 'Supercalifragilisticexpialidocious' not in line:
                assert line_width <= width
            expected_x = 300 - (line_width / 2 if align == 'center'
                                else line_width if align == 'right' else 0)
            assert position[0] == expected_x
            assert fill == 'white'
        selected = draw.calls[0][2]
        # 即使单词超宽，也保留明确填写的字号，由预览提示用户调整。
        assert selected.size == original.size
        assert end_y == 20 + len(draw.calls) * (sum(selected.getmetrics()) + 7)


def test_wrapper_keeps_chinese_and_mixed_language_breaks():
    font = ImageFont.truetype(os.path.join(FONTS, 'OPPOSans-B.ttf'), 64)
    width = font.getlength('中文') + 1
    draw, original, _ = _wrap_text('中文电影合集', width)
    assert [call[1] for call in draw.calls] == ['中文', '电影', '合集']
    assert all(call[2].size == original.size for call in draw.calls)
    draw, _, _ = _wrap_text('中文 ANIMATED 电影 MOVIES', font.getlength('ANIMATED MO'))
    assert ''.join(call[1].replace(' ', '') for call in draw.calls) == '中文ANIMATED电影MOVIES'
    assert all('ANIMATED' in call[1] or not any(c.isascii() and c.isalpha() for c in call[1])
               or 'MOVIES' in call[1] for call in draw.calls)
    assert all(call[2].getlength(call[1]) <= font.getlength('ANIMATED MO') for call in draw.calls)


def test_wrapper_respects_explicit_newlines_and_discards_wrap_edge_spaces():
    draw, font, end_y = _wrap_text("  Children's Spider-Man  \r\n\nMOVIES  ", 500)
    assert [call[1] for call in draw.calls] == ["Children's", 'Spider-Man', '', 'MOVIES']
    assert all(call[1] == call[1].strip() for call in draw.calls)
    assert end_y == 20 + 4 * (sum(font.getmetrics()) + 7)
    draw, _, end_y = _wrap_text('', 500)
    assert draw.calls == []
    assert end_y == 20


def test_wrapper_gradient_paint_uses_wrapped_lines_and_selected_font():
    engine = PosterEngine(fonts_dir=FONTS, layouts_dir=LAYOUTS)
    draw = _TextRecorder()
    draw._image = object()
    painted = []
    engine._draw_text_paint = lambda *args: painted.append(args)
    font = ImageFont.truetype(os.path.join(FONTS, 'OPPOSans-B.ttf'), 64)
    fill = {'mode': 'gradient'}
    engine.draw_text_wrapper(draw, 'ANIMATED MOVIES', 300, 20, font,
                             font.getlength('ANIMATED MO'), fill, align='center')
    assert [args[2] for args in painted] == ['ANIMATED', 'MOVIES']
    assert all(args[0] is draw._image and args[4] is fill for args in painted)
    assert draw.calls == []


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith('test_') and callable(fn)]
    for name, test in tests:
        test()
        print(f"{name} OK")
    print(f"{len(tests)} tests passed")
