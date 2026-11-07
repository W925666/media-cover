#!/usr/bin/env python3
"""Generate the 封面工厂 plugin icon: 黑白灰 / Apple 圆角 / 毛玻璃 / 网格风."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ui" / "icon.png"
FONT = ROOT / "fonts" / "NotoSansCJKsc-Bold.otf"
S = 1024

# --- 1. 深炭灰垂直渐变底 + 径向高光 ------------------------------------ #
grad = Image.new("RGB", (1, 256))
gp = grad.load()
top = (30, 30, 34)
bottom = (10, 10, 12)
for y in range(256):
    t = y / 255.0
    gp[0, y] = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
base = grad.resize((S, S), Image.BILINEAR).convert("RGBA")

# 左上角柔光，增加苹果式纵深
glow = Image.new("L", (S, S), 0)
gd = ImageDraw.Draw(glow)
gd.ellipse([-360, -360, 700, 700], fill=70)
glow = glow.filter(ImageFilter.GaussianBlur(160))
white_glow = Image.new("RGBA", (S, S), (255, 255, 255, 0))
white_glow.putalpha(glow)
base = Image.alpha_composite(base, white_glow)

# --- 2. 网格背景 -------------------------------------------------------- #
grid = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(grid)
step = 128
line = (255, 255, 255, 13)
for x in range(0, S, step):
    gd.line([(x, 0), (x, S)], fill=line, width=1)
for y in range(0, S, step):
    gd.line([(0, y), (S, y)], fill=line, width=1)
base = Image.alpha_composite(base, grid)

# --- 3. 毛玻璃圆角卡片（squircle 风格，大圆角） -------------------------- #
card = (212, 212, 812, 812)
radius = 150

# 卡片区先取底图模糊 -> 再按圆角 mask 贴回，形成真·毛玻璃
blur = base.crop(card).filter(ImageFilter.GaussianBlur(30))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(card, radius=radius, fill=255)
base.paste(blur, (card[0], card[1]), mask.crop(card))

glass = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(glass)
# 玻璃填充 + 描边
gd.rounded_rectangle(card, radius=radius, fill=(255, 255, 255, 14),
                     outline=(255, 255, 255, 42), width=3)
# 上缘高光（Apple 玻璃高光带）
gd.rounded_rectangle((card[0], card[1], card[2], card[1] + (card[3] - card[1]) // 2),
                     radius=radius, fill=(255, 255, 255, 12))
base = Image.alpha_composite(base, glass)

# --- 4. 中央图形：三张叠放海报（单色灰阶） ------------------------------- #
# 柔和投影
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(shadow)
gd.rounded_rectangle((372, 412, 692, 812), radius=26, fill=(0, 0, 0, 90))
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
base = Image.alpha_composite(base, shadow)

def poster(box, fill, outline=None):
    gd = ImageDraw.Draw(base)
    gd.rounded_rectangle(box, radius=26, fill=fill, outline=outline, width=3)

# 后卡（左后）、中卡（中间偏左）
poster((286, 286, 586, 686), fill=(58, 58, 64, 255))
poster((348, 318, 648, 718), fill=(98, 98, 106, 255))
# 前卡（近白）
poster((410, 350, 710, 750), fill=(245, 245, 247, 255))

# 前卡上的「封面」字样 + 顶部小缩略框（迷你海报）
gd = ImageDraw.Draw(base)
gd.rounded_rectangle((452, 400, 668, 560), radius=20, fill=(34, 34, 38, 255))
# 缩略框内一个小「山」构图（抽象海报）
gd.rounded_rectangle((452, 440, 668, 460), fill=(120, 120, 128, 255))
gd.rounded_rectangle((460, 500, 620, 520), fill=(150, 150, 158, 255))
gd.rounded_rectangle((500, 470, 590, 486), fill=(150, 150, 158, 255))

font = ImageFont.truetype(str(FONT), 92)
text = "封面"
tb = gd.textbbox((0, 0), text, font=font)
tw, th = tb[2] - tb[0], tb[3] - tb[1]
gx = (452 + 668) // 2 - tw // 2
gy = 590
gd.text((gx - tb[0], gy - tb[1]), text, font=font, fill=(24, 24, 26, 255))

# --- 5. 输出 ------------------------------------------------------------- #
final = base.convert("RGB")
final.save(OUT, "PNG", optimize=True)
print("saved", OUT, final.size)
