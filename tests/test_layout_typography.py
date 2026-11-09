"""布局排版回归：真实 wrapper/字体度量，不用截图猜测文字边界。

标准布局保证副标在主标实际换行末端之后；不要求超大字号仍留在画布内。
动态布局直接调用其真实 render（空素材文字路径），避免 GIF 编码/缩放
混入字号契约。自由设计为独立元素坐标；荣耀视界为单行字距/描边/渐变；
斜线允许独立横坐标且阴影带偏移，这些布局不承诺统一自动排版或画布内适配。
"""
from dataclasses import dataclass
from pathlib import Path
import sys

import pytest
from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import PosterEngine  # noqa: E402

STATIC_LAYOUTS = (
    "经典九宫格", "经典堆叠", "经典平铺", "极简", "海报墙",
    "扇形展开", "旋转", "聚焦C佬",
)
DYNAMIC_LAYOUTS = (
    "经典九宫格(动态)", "经典堆叠(动态)", "经典平铺(动态)",
    "扇形展开(动态)", "旋转(动态)", "聚焦(动态)",
)
STANDARD_LAYOUTS = STATIC_LAYOUTS + DYNAMIC_LAYOUTS
# 每个主、副字号均覆盖40/80/100/160/240，并刻意保持两者不同。
SIZE_PAIRS = ((40, 240), (80, 160), (100, 40), (160, 100), (240, 80))
TITLES = ("电影", "中文电影合集 ANIMATED MOVIES\n长标题测试 WESTERN TELEVISION SERIES COLLECTION")
SUBTITLE = "WESTERN TV SERIES"


@dataclass
class TextCall:
    text: str
    x: float
    y: float
    font: object
    end_y: float
    lines: list


class RecordingDraw:
    """只记录 wrapper 实际绘制的行；其它图形仍由真实 PIL 绘制。"""

    def __init__(self):
        self.lines = []

    def text(self, position, text, font, fill):
        self.lines.append((position, text, font))


class RecordingContext:
    def __init__(self, engine, layout):
        self.engine = engine
        self.layout = layout
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def download_img(self, source):
        # 排版测试不依赖网络。旋转的文字路径需要一个有效主图；其它
        # 静态布局的真实缺图分支仍会绘字，省去海报阴影/滤镜开销。
        if self.layout in ("旋转", "斜线分割"):
            return Image.new("RGB", (24, 36), (70, 110, 150))
        return None

    def draw_text_wrapper(self, draw, text, x, y, font, max_width, fill,
                          line_spacing=10, align="left"):
        recorder = RecordingDraw()
        end_y = self.engine.draw_text_wrapper(
            recorder, text, x, y, font, max_width, fill,
            line_spacing=line_spacing, align=align,
        )
        self.calls.append(TextCall(str(text), x, y, font, end_y, recorder.lines))
        return end_y


@pytest.fixture(scope="module")
def engine():
    return PosterEngine(fonts_dir=str(ROOT / "fonts"), layouts_dir=str(ROOT / "layouts"))


@pytest.fixture(scope="module")
def font_cache(engine):
    return {
        size: engine._load_text_font("OPPOSans-B.ttf", size, "中文电影 ANIMATED MOVIES")
        for size in (40, 80, 100, 160, 240)
    }


def defaults(engine, layout):
    schema = engine.layout_modules[layout].__globals__["get_schema"]()
    return {
        item["key"]: item["default"]
        for group in schema for item in group.get("items", [])
        if "key" in item and "default" in item
    }


def render_text(engine, font_cache, layout, title, title_size, subtitle_size, **overrides):
    config = defaults(engine, layout)
    config.update(
        title=title, subtitle=SUBTITLE, title_size=title_size,
        subtitle_size=subtitle_size, font_title="OPPOSans-B.ttf",
        font_subtitle="OPPOSans-B.ttf", enable_animation=False,
        bg_blur=0, blur_radius=0, grain_intensity=0, mask_opacity=0,
        # 只减小九宫格缺图占位海报，不改文字区域/坐标。
        cell_width=32, cell_height=48,
    )
    config.update(overrides)
    fonts = {"main": font_cache[title_size], "sub": font_cache[subtitle_size]}
    ctx = RecordingContext(engine, layout)
    assets = {"posters": [] if layout in DYNAMIC_LAYOUTS else ["fixture"] * 9,
              "backdrops": ["fixture"] if layout == "斜线分割" else [],
              "poster_backdrops": [], "count": 9}
    canvas = Image.new("RGBA", (1920, 1080), (20, 30, 50, 255))
    result = engine.layout_modules[layout](ctx, canvas, config, assets, fonts)
    assert result.size == (1920, 1080)
    result.close()
    if result is not canvas:
        canvas.close()
    return ctx, config


def foreground(ctx, text):
    # 九宫格/旋转/斜线先画阴影后画正文；不能把阴影的偏移当正文锚点。
    matches = [call for call in ctx.calls if call.text == text]
    assert matches, (ctx.layout, text, ctx.calls)
    return matches[-1]


def assert_font_preserved(call, font):
    assert call.font is font
    assert call.lines
    assert all(line_font is font and line_font.size == font.size
               for _, _, line_font in call.lines)
    advance = sum(font.getmetrics()) + 10
    assert call.end_y == pytest.approx(call.y + len(call.lines) * advance)
    assert [position[1] for position, _, _ in call.lines] == pytest.approx(
        [call.y + i * advance for i in range(len(call.lines))]
    )


@pytest.mark.parametrize("layout", STANDARD_LAYOUTS)
@pytest.mark.parametrize("title_size,subtitle_size", SIZE_PAIRS)
@pytest.mark.parametrize("title", TITLES, ids=("short", "long-multiline"))
def test_standard_layout_subtitle_follows_real_title_end(
        engine, font_cache, layout, title_size, subtitle_size, title):
    ctx, _ = render_text(engine, font_cache, layout, title, title_size, subtitle_size)
    main = foreground(ctx, title)
    sub = foreground(ctx, SUBTITLE)
    assert_font_preserved(main, font_cache[title_size])
    assert_font_preserved(sub, font_cache[subtitle_size])
    assert sub.y >= main.end_y, (layout, title_size, subtitle_size, main, sub)
    # 显式换行和宽度产生的多行均使用真实font度量，不按title_size估算。
    if "\n" in title:
        assert len(main.lines) >= 2
    actual_bottom = max(position[1] + font.getbbox(line)[3]
                        for position, line, font in main.lines)
    assert sub.y >= actual_bottom


def subtitle_only_baseline(layout, config):
    """独立副标使用原布局基准；没有主标就不计入主标字号/行高。"""
    if layout in ("经典堆叠", "经典平铺", "极简", "经典堆叠(动态)", "经典平铺(动态)"):
        return int(1080 * float(config["text_top_percent"]) / 100)
    if layout == "海报墙" or layout.startswith("扇形展开"):
        return 540 + int(config["subtitle_offset_y"])
    if layout.startswith("旋转"):
        return int(1080 * float(config["subtitle_pos_y"]) / 100)
    if layout == "经典九宫格":
        return int(1080 * float(config["sub_y"]) / 100)
    if layout == "经典九宫格(动态)":
        return int(1080 * float(config["subtitle_y"]) / 100)
    if layout in ("聚焦C佬", "聚焦(动态)"):
        baseline = int(1080 * float(config["baseline_percent"]) / 100)
        hero = int(1080 * float(config["hero_height_percent"]) / 100)
        return (max(50 if layout == "聚焦C佬" else 35, (baseline - hero) // 2)
                + int(config["title_y_offset"]) + int(config["subtitle_offset_y"]))
    raise AssertionError(layout)


@pytest.mark.parametrize("layout", STANDARD_LAYOUTS)
@pytest.mark.parametrize("subtitle_size", (40, 80, 100, 160, 240))
def test_subtitle_without_title_keeps_independent_baseline(engine, font_cache, layout, subtitle_size):
    coordinates = []
    for title_size in (40, 240):
        ctx, config = render_text(engine, font_cache, layout, "", title_size, subtitle_size)
        assert not any(call.text == "" for call in ctx.calls)
        sub = foreground(ctx, SUBTITLE)
        assert_font_preserved(sub, font_cache[subtitle_size])
        assert sub.y == subtitle_only_baseline(layout, config)
        coordinates.append((sub.x, sub.y))
    assert coordinates[0] == coordinates[1]


def test_large_multiline_title_keeps_size_even_when_below_canvas(engine, font_cache):
    """固定画布空间不足时仍完整换行，明确记录超边界而非偷偷缩字。"""
    title = TITLES[1]
    ctx, _ = render_text(engine, font_cache, "经典九宫格", title, 240, 80)
    main, sub = foreground(ctx, title), foreground(ctx, SUBTITLE)
    assert_font_preserved(main, font_cache[240])
    assert main.end_y > 1080
    assert sub.y >= main.end_y
    assert "".join(line.replace(" ", "") for _, line, _ in main.lines) == title.replace(" ", "").replace("\n", "")


def test_classic_grid_schema_uses_engine_subtitle_keys(engine):
    schema = engine.layout_modules["经典九宫格"].__globals__["get_schema"]()
    fields = {item["key"]: item for group in schema for item in group.get("items", []) if "key" in item}
    assert fields["font_subtitle"]["type"] == "font"
    assert fields["subtitle_size"]["type"] == "range"
    assert "font_sub" not in fields
    assert "sub_size" not in fields


@pytest.mark.parametrize("title_size,subtitle_size", SIZE_PAIRS)
def test_diagonal_independent_horizontal_anchors_and_size_not_clipping(engine, font_cache, title_size, subtitle_size):
    """斜线只保证正文纵向不重叠；多重阴影/横向越界不纳入自动适配承诺。"""
    title = TITLES[1]
    ctx, _ = render_text(engine, font_cache, "斜线分割", title, title_size, subtitle_size,
                         title_x=20, sub_x=35)
    main, sub = foreground(ctx, title), foreground(ctx, SUBTITLE)
    assert main.x == 1920 * 20 // 100
    assert sub.x == 1920 * 35 // 100
    assert sub.y >= main.end_y
    assert_font_preserved(main, font_cache[title_size])
    assert_font_preserved(sub, font_cache[subtitle_size])


def test_free_designer_preserves_explicit_element_positions(engine, monkeypatch):
    """自由设计允许用户叠放元素，不强行串联主副标题；直接验证其元素坐标。"""
    render = engine.layout_modules["自由设计"]
    placements = []
    original = Image.Image.alpha_composite

    def record(canvas, layer, dest=(0, 0), source=(0, 0)):
        placements.append(dest)
        return original(canvas, layer, dest, source)

    monkeypatch.setattr(Image.Image, "alpha_composite", record)
    elements = [{"type": "text", "textSource": source, "x": 120, "y": 80,
                 "w": 500, "h": 160, "font": "OPPOSans-B.ttf", "fontSize": size}
                for source, size in (("title", 100), ("subtitle", 40))]
    canvas = Image.new("RGBA", (1920, 1080))
    ctx = RecordingContext(engine, "自由设计")
    result = render(ctx, canvas, {"title": "电影", "subtitle": SUBTITLE,
                                "designer_elements": elements}, {}, {})
    assert placements == [(120, 80), (120, 80)]
    assert ctx.calls == []  # 独立元素文本渲染，不走标准wrapper。
    result.close()
    canvas.close()


@pytest.mark.parametrize("subtitle_size", (40, 80, 100, 160, 240))
@pytest.mark.parametrize("subtitle_y", (140, 980), ids=("conflicting-anchor", "default-anchor"))
def test_glory_subtitle_uses_engine_font_fallback_without_reflow(engine, font_cache, monkeypatch, subtitle_size, subtitle_y):
    """荣耀保留字距/渐变/麦穗设计，只验证实际渲染选中sub字体及原始锚点。"""
    render = engine.layout_modules["荣耀视界"]
    scope = render.__globals__
    calls = []
    original = scope["draw_text_simple"]

    def record(draw, text, center_x, y, font, color, *args, **kwargs):
        calls.append((text, center_x, y, font))
        return original(draw, text, center_x, y, font, color, *args, **kwargs)

    monkeypatch.setitem(scope, "draw_text_simple", record)
    config = defaults(engine, "荣耀视界")
    config.update(title="电影", subtitle=SUBTITLE, font_title="missing-title-font.ttf",
                  font_subtitle="missing-subtitle-font.ttf", title_size=100,
                  subtitle_size=subtitle_size, subtitle_y=subtitle_y, poster_count=1, vignette_strength=0)
    fonts = {"main": font_cache[100], "sub": font_cache[subtitle_size]}
    ctx = RecordingContext(engine, "荣耀视界")
    canvas = Image.new("RGBA", (1920, 1080))
    result = render(ctx, canvas, config, {"posters": [], "count": 0}, fonts)
    subtitle_calls = [call for call in calls if call[0] == SUBTITLE]
    assert len(subtitle_calls) == 1
    _, x, y, font = subtitle_calls[0]
    _, _, gradient_height = scope["create_gradient_text"](
        config["title"], font_cache[100], config["title_gradient_top"],
        config["title_gradient_bottom"], spacing=int(config["title_spacing"]),
    )
    assert (x, y) == (960, max(subtitle_y, int(config["title_y"]) + gradient_height + 20))
    assert font.size == subtitle_size
    assert font.path == font_cache[subtitle_size].path
    assert ctx.calls == []  # 实色副标使用专属字距绘制，而非标准重排。
    result.close()
    if result is not canvas:
        canvas.close()
