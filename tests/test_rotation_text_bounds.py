"""旋转文字组边界：真实布局文字路径、真实字体与纯色画布，无卡片/噪点开销。"""
from pathlib import Path
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine

LAYOUTS = ("旋转", "旋转(动态)")


@pytest.fixture(scope="module")
def engine():
    return PosterEngine(fonts_dir=str(ROOT / "fonts"), layouts_dir=str(ROOT / "layouts"))


class LegacyContext:
    """没有 bbox/分行测量接口的旧 fake ctx，仍按原 wrapper 顺序绘制。"""
    def __init__(self, engine):
        self.engine = engine
        self.calls = []

    def resolve_text_fill(self, *args, **kwargs):
        return self.engine.resolve_text_fill(*args, **kwargs)

    def draw_text_wrapper(self, draw, text, x, y, font, max_width, fill):
        end = self.engine.draw_text_wrapper(draw, text, x, y, font, max_width, fill)
        # 独立加高蒙版保留画布底部之外的真实像素，避免 clipped bbox 假阳性。
        mask = Image.new("L", (1920, 6000))
        self.engine.draw_text_wrapper(ImageDraw.Draw(mask), text, x, y + 3000,
                                      font, max_width, 255)
        bbox = mask.getbbox()
        if bbox:
            bbox = (bbox[0], bbox[1] - 3000, bbox[2], bbox[3] - 3000)
        self.calls.append(dict(text=text, x=x, y=y, font=font, end=end, bbox=bbox,
                               lines=self.engine._wrapped_text_lines(text, font, max_width)))
        mask.close()
        return end


class MeasuredContext(LegacyContext):
    def __init__(self, engine):
        super().__init__(engine)
        self.measurements = []

    def _wrapped_text_lines(self, *args, **kwargs):
        return self.engine._wrapped_text_lines(*args, **kwargs)

    def wrapped_text_ink_bbox(self, *args, **kwargs):
        assert not self.calls, "文字组应在第一次绘制前完成测量"
        box = self.engine.wrapped_text_ink_bbox(*args, **kwargs)
        self.measurements.append(box)
        return box


def render_text(engine, layout, *, measured=True, font_name="NotoSansCJKsc-Bold.otf",
                title="纪录电影", subtitle="TV SHOWS", title_size=200,
                subtitle_size=100, shadow=0, **overrides):
    renderer = engine.layout_modules[layout]
    scope = renderer.__globals__
    config = {item["key"]: item["default"] for group in scope["get_schema"]()
              for item in group.get("items", []) if "default" in item}
    config.update(title=title, subtitle=subtitle, text_shadow_opacity=shadow)
    config.update(overrides)
    # 直接加载指定字体，不让缺字回退掩盖 Noto 高 metrics 的已知回归。
    fonts = {"main": ImageFont.truetype(str(ROOT / "fonts" / font_name), title_size),
             "sub": ImageFont.truetype(str(ROOT / "fonts" / font_name), subtitle_size)}
    ctx = MeasuredContext(engine) if measured else LegacyContext(engine)
    canvas = Image.new("RGBA", (1920, 1080), (20, 30, 50, 255))
    result = scope["_draw_text"](ctx, canvas, config, fonts)
    assert result is canvas
    return ctx, canvas


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("subtitle", ("TV SHOWS", "COLLECTIONS"))
@pytest.mark.parametrize("shadow", (0, 100))
def test_noto_two_line_group_moves_up_by_exact_ink_overflow(engine, layout, subtitle, shadow):
    old, old_image = render_text(engine, layout, measured=False, subtitle=subtitle, shadow=shadow)
    ctx, image = render_text(engine, layout, subtitle=subtitle, shadow=shadow)
    assert len(ctx.measurements) == 2
    old_bottom = max(call["bbox"][3] for call in old.calls)
    expected_overflow = max(box[3] + (offset if shadow else 0)
                            for box, offset in zip(ctx.measurements, (3, 2))) - 1080
    assert old_bottom > 1080 and expected_overflow > 0
    assert max(call["bbox"][3] for call in ctx.calls) <= 1080
    assert min(call["bbox"][1] for call in ctx.calls) >= 0
    for previous, current in zip(old.calls, ctx.calls):
        assert current["y"] == previous["y"] - expected_overflow
        assert current["x"] == previous["x"]
        assert current["font"].size == previous["font"].size
        assert current["lines"] == previous["lines"]
    main = [call for call in ctx.calls if call["text"] == "纪录电影"][-1]
    sub = [call for call in ctx.calls if call["text"] == subtitle][-1]
    assert len(main["lines"]) == 2 and sub["y"] >= main["end"] + 10
    image.close()
    old_image.close()


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("title,subtitle", (("电影", "TV SHOWS"), ("", "COLLECTIONS"), ("电影", ""), ("", "")))
def test_in_bounds_presets_keep_identical_positions_and_pixels(engine, layout, title, subtitle):
    options = dict(font_name="OPPOSans-B.ttf", title=title, subtitle=subtitle,
                   title_size=140, subtitle_size=50, shadow=100)
    old, old_image = render_text(engine, layout, measured=False, **options)
    ctx, image = render_text(engine, layout, **options)
    assert [(c["x"], c["y"], c["end"], c["lines"]) for c in ctx.calls] == [
        (c["x"], c["y"], c["end"], c["lines"]) for c in old.calls]
    assert image.tobytes() == old_image.tobytes()
    image.close()
    old_image.close()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_subtitle_only_retains_independent_anchor_even_when_too_tall(engine, layout):
    options = dict(title="", subtitle="WESTERN TV SERIES", subtitle_size=240)
    old, old_image = render_text(engine, layout, measured=False, **options)
    ctx, image = render_text(engine, layout, **options)
    assert ctx.measurements == []
    assert ctx.calls[0]["y"] == old.calls[0]["y"] == 594
    assert image.tobytes() == old_image.tobytes()
    image.close()
    old_image.close()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_taller_than_canvas_preserves_size_reflow_and_relative_spacing(engine, layout):
    title = "纪录电影\n" * 3 + "纪录电影"
    old, old_image = render_text(engine, layout, measured=False, title=title)
    ctx, image = render_text(engine, layout, title=title)
    # 一组本身超高时上端仍越界；不通过缩字、少画行或改主副间距假装容纳。
    assert min(call["bbox"][1] for call in ctx.calls) < 0
    assert max(call["bbox"][3] for call in ctx.calls) <= 1080
    assert ctx.calls[0]["font"].size == 200
    assert ctx.calls[1]["font"].size == 100
    assert [call["lines"] for call in ctx.calls] == [call["lines"] for call in old.calls]
    assert ctx.calls[1]["y"] - ctx.calls[0]["y"] == old.calls[1]["y"] - old.calls[0]["y"]
    image.close()
    old_image.close()
