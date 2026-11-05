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

from PIL import Image  # noqa: E402

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


if __name__ == "__main__":
    test_all_layouts_render()
    print("test_all_layouts_render OK")
    test_unknown_layout_falls_back_not_solid()
    print("test_unknown_layout_falls_back_not_solid OK")
    test_default_dirs_work_from_any_cwd()
    print("test_default_dirs_work_from_any_cwd OK")
