"""角标不得分配整幅4x画布；覆盖所有样式。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from engine.engine import PosterEngine
engine = PosterEngine('fonts', 'layouts')
original = Image.new
allocations = []
def tracked(mode, size, *args, **kwargs):
    allocations.append((mode, size))
    assert size[0] * size[1] < 1920 * 1080 * 4, (mode, size)
    return original(mode, size, *args, **kwargs)
for style in ('box','ribbon','circle','outline','hexagon','ticket','stamp','glitch','wave','burst','tape','split'):
    canvas = original('RGBA', (1920,1080), (20,30,40,255))
    Image.new = tracked
    try:
        result = engine._draw_badge(canvas, {'badge_style':style,'badge_size':100,'badge_font':'剑豪体.ttf'},2505,{})
        assert result is canvas
    finally:
        Image.new = original
        canvas.close()
    print('PASS', style)
print('Largest allocation pixels:', max(w*h for _,(w,h) in allocations))
