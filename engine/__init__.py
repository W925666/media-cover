"""封面引擎：从 FrameBase 移植的 Pillow 海报渲染核心。

只依赖 PIL 与标准库，布局模块（layouts/*.py 的 render 函数）由
PosterEngine 动态加载。此包不含任何 MediaTidy SDK 依赖，便于独立测试。
"""
from .engine import PosterEngine

__all__ = ["PosterEngine"]
