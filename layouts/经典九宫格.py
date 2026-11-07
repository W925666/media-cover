from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageColor
import colorsys
import random
import math

# =============================================================================
# 1. 辅助函数 (无 Numpy)
# =============================================================================

def _fit_title_height(font, text, max_width, available_height):
    while True:
        lines = 0
        for paragraph in str(text).splitlines() or [str(text)]:
            current = ''
            lines += 1
            for char in paragraph:
                if current and font.getlength(current + char) > max_width:
                    lines += 1
                    current = char
                else:
                    current += char
        ascent, descent = font.getmetrics()
        if lines * (ascent + descent + 10) <= available_height or font.size <= 1:
            return font
        font = font.font_variant(size=font.size - 1)


def add_shadow(img, offset=(20, 20), shadow_color=(0, 0, 0, 200), blur_radius=20):
    """给图片添加阴影"""
    w, h = img.size
    shadow_w = w + abs(offset[0]) + blur_radius * 2
    shadow_h = h + abs(offset[1]) + blur_radius * 2
    
    shadow = Image.new("RGBA", (shadow_w, shadow_h), (0,0,0,0))
    s_layer = Image.new("RGBA", (w, h), shadow_color)
    
    # 放置阴影层
    paste_x = blur_radius + max(0, offset[0])
    paste_y = blur_radius + max(0, offset[1])
    shadow.paste(s_layer, (paste_x, paste_y))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    
    # 放置原图
    img_x = blur_radius + max(0, -offset[0])
    img_y = blur_radius + max(0, -offset[1])
    
    result = Image.new("RGBA", (shadow_w, shadow_h), (0,0,0,0))
    result.paste(shadow, (0,0))
    result.paste(img, (img_x, img_y), img)
    return result

def create_gradient_bg(size, color_left, color_right):
    """纯 PIL 绘制横向渐变"""
    w, h = size
    # 创建 256x1 的渐变条
    gradient = Image.new('RGB', (256, 1))
    draw = ImageDraw.Draw(gradient)
    
    r1, g1, b1 = color_left[:3]
    r2, g2, b2 = color_right[:3]
    
    for i in range(256):
        ratio = i / 255.0
        r = int(r1 * (1 - ratio) + r2 * ratio)
        g = int(g1 * (1 - ratio) + g2 * ratio)
        b = int(b1 * (1 - ratio) + b2 * ratio)
        draw.point((i, 0), fill=(r, g, b))
        
    return gradient.resize((w, h), Image.BICUBIC)

def darken_color(color, factor=0.7):
    return (int(color[0]*factor), int(color[1]*factor), int(color[2]*factor))

def get_random_bright_color():
    h = random.random()
    s = random.uniform(0.5, 1.0)
    v = random.uniform(0.7, 1.0)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r*255), int(g*255), int(b*255))

# =============================================================================
# 2. 参数 Schema (已更新默认坐标以适配3列)
# =============================================================================
def get_schema():
    return [
        {
            "group": "文字内容",
            "items": [
                {"key": "title", "label": "中文标题", "type": "text", "default": ""},
                {"key": "subtitle", "label": "英文标题", "type": "text", "default": ""},
            ]
        },
        {
            "group": "海报墙布局",
            "items": [
                {"key": "cell_width", "label": "单张海报宽", "type": "range", "min": 200, "max": 600, "default": 410},
                {"key": "cell_height", "label": "单张海报高", "type": "range", "min": 300, "max": 900, "default": 610},
                {"key": "corner_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 100, "default": 46},
                {"key": "rotation", "label": "整体旋转角度", "type": "range", "min": -45, "max": 45, "default": -16},
                # 修改：为了容纳3列，将默认起始X从835改为350
                {"key": "start_x", "label": "起始位置 X", "type": "range", "min": 0, "max": 2000, "default": 350},
                {"key": "start_y", "label": "起始位置 Y", "type": "range", "min": -1000, "max": 1000, "default": -200},
                {"key": "col_spacing", "label": "列间距微调", "type": "range", "min": -100, "max": 300, "default": 50},
            ]
        },
        {
            "group": "文字样式",
            "items": [
                {"key": "font_title", "label": "标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "标题字号", "type": "range", "min": 50, "max": 300, "default": 160},
                {"key": "title_x", "label": "标题 X%", "type": "range", "min": 0, "max": 100, "default": 5},
                {"key": "title_y", "label": "标题 Y%", "type": "range", "min": 0, "max": 100, "default": 40},
                {"key": "font_sub", "label": "英文副标字体", "type": "font", "default": "default.ttf"},
                {"key": "sub_size", "label": "副标字号", "type": "range", "min": 20, "max": 150, "default": 50},
                {"key": "sub_x", "label": "副标 X%", "type": "range", "min": 0, "max": 100, "default": 8},
                {"key": "sub_y", "label": "副标 Y%", "type": "range", "min": 0, "max": 100, "default": 58},
            ]
        },
        {
            "group": "背景设置",
            "items": [
                {"key": "use_blur_bg", "label": "使用模糊原图背景", "type": "boolean", "default": False},
                {"key": "bg_blur_radius", "label": "模糊半径", "type": "range", "min": 0, "max": 200, "default": 60},
            ]
        }
    ]

# =============================================================================
# 3. 渲染逻辑 (已优化3列逻辑)
# =============================================================================
def render(ctx, bg, config, assets, fonts):
    w, h = bg.size
    poster_urls = assets.get('posters', [])
    if not poster_urls: return bg
    
    # --- 1. 背景生成 ---
    first_img = ctx.download_img(poster_urls[0])
    if first_img:
        first_img = first_img.convert("RGB")
        thumb = first_img.resize((1, 1), Image.BICUBIC)
        theme_color = thumb.getpixel((0, 0))
    else:
        theme_color = (100, 100, 100)
    
    color_left = darken_color(theme_color, 0.6)
    color_right = darken_color(theme_color, 1.2)
    
    use_blur = config.get('use_blur_bg')
    if use_blur == True or use_blur == 'True' or str(use_blur) == '1':
        if first_img:
            blur_r = int(config.get('bg_blur_radius', 60))
            bg_base = first_img.copy()
            bg_base = ImageOps.fit(bg_base, (w, h), method=Image.LANCZOS)
            bg_base = bg_base.filter(ImageFilter.GaussianBlur(blur_r))
            overlay = Image.new("RGBA", (w, h), (0,0,0,80))
            bg.paste(bg_base, (0,0))
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay)
        else:
            bg = create_gradient_bg((w, h), color_left, color_right).convert("RGBA")
    else:
        bg = create_gradient_bg((w, h), color_left, color_right).convert("RGBA")
        
    # --- 2. 海报墙生成 ---
    cell_w = int(config.get('cell_width', 410))
    cell_h = int(config.get('cell_height', 610))
    radius = int(config.get('corner_radius', 46))
    rotation = float(config.get('rotation', -16))
    
    # 获取坐标配置
    start_x = int(config.get('start_x', 350))
    start_y = int(config.get('start_y', -200))
    # 使用 col_spacing 作为列与列之间的额外间距（除了海报宽度外的）
    col_gap = int(config.get('col_spacing', 50))
    margin_y = 22 # 图片垂直堆叠间距
    
    # 定义行列数：3列 x 3行
    cols = 3
    rows = 3
    max_count = rows * cols
    
    # [新增] 自动补全图片：如果图片不够9张，循环使用
    if len(poster_urls) < max_count:
        import itertools
        # 创建一个无限循环迭代器
        cycled_urls = itertools.cycle(poster_urls)
        # 取出 max_count 个
        poster_urls = [next(cycled_urls) for _ in range(max_count)]
    
    # 预加载并处理所有图片
    processed_images = []
    
    # 制作圆角蒙版
    mask = Image.new("L", (cell_w, cell_h), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([(0,0), (cell_w, cell_h)], radius=radius, fill=255)
    
    for i in range(max_count):
        url = poster_urls[i]
        img = ctx.download_img(url)
        if not img: 
            # 如果下载失败，创建一个纯色块代替，防止报错
            img = Image.new("RGB", (cell_w, cell_h), (50, 50, 50))
        
        # 裁剪并缩放
        img = ImageOps.fit(img, (cell_w, cell_h), method=Image.LANCZOS)
        
        # 应用圆角
        img_rgba = img.convert("RGBA")
        base = Image.new("RGBA", (cell_w, cell_h), (0,0,0,0))
        base.paste(img_rgba, (0,0), mask=mask)
        
        # 添加阴影
        img_shadow = add_shadow(base, offset=(15, 15), blur_radius=15, shadow_color=(0,0,0,180))
        processed_images.append(img_shadow)
        
    # --- 分列渲染核心逻辑 ---
    for col_idx in range(cols):
        # 提取当前列的图片索引: [0,1,2], [3,4,5], [6,7,8]
        col_imgs = []
        for row_idx in range(rows):
            idx = col_idx * rows + row_idx
            if idx < len(processed_images):
                col_imgs.append(processed_images[idx])
        
        if not col_imgs: continue
        
        # 1. 拼合整列到一个临时长画布
        one_w, one_h = col_imgs[0].size
        # 计算总高度：图片高 * 数量 + 间距 * (数量-1)
        total_h = one_h * len(col_imgs) + margin_y * (len(col_imgs)) 
        
        col_canvas = Image.new("RGBA", (one_w, total_h), (0,0,0,0))
        
        curr_y = 0
        for img in col_imgs:
            col_canvas.paste(img, (0, curr_y))
            # 这里的 -30 是为了消除部分阴影带来的视觉过大间距
            curr_y += img.height - 30 
            
        # 2. 旋转整列
        # expand=True 保证旋转后不被裁剪，但这会改变图层尺寸，需要重新计算中心
        rotated_col = col_canvas.rotate(rotation, resample=Image.BICUBIC, expand=True)
        
        # 3. 计算放置位置
        # 逻辑：起始X + 列号 * (图片宽 - 叠加偏移 + 额外间距)
        # (cell_w - 50) 模仿了原代码的重叠紧凑感
        x_step = cell_w - 50 + col_gap
        col_base_x = start_x + col_idx * x_step
        
        # 垂直错落感：每向右一列，Y轴稍微变化，造成一种倾斜墙面的视差
        # 这里设置为每列向上提一些 (例如 -80)，配合旋转角度
        y_step = -80 
        col_base_y = start_y + col_idx * y_step
        
        # 绘制到背景
        bg.paste(rotated_col, (int(col_base_x), int(col_base_y)), rotated_col)

    # --- 3. 绘制文字 ---
    draw = ImageDraw.Draw(bg)
    title = config.get('title', '')
    subtitle = config.get('subtitle', '')
    
    f_title = fonts.get('font_title') or fonts.get('main')
    f_sub = fonts.get('font_sub') or fonts.get('sub')
    
    t_size = int(config.get('title_size', 160))
    s_size = int(config.get('sub_size', 50))
    
    tx = int(w * float(config.get('title_x', 5))/100)
    ty = int(h * float(config.get('title_y', 40))/100)
    
    sx = int(w * float(config.get('sub_x', 8))/100)
    sy = int(h * float(config.get('sub_y', 58))/100)
    
    subtitle_width = max(1, min(int(w * 0.34) - sx, w - sx - 3))
    gap = max(8, round(h * 0.018))
    if subtitle:
        f_sub = ctx.fit_single_line_font(f_sub, subtitle, subtitle_width)
    reserve = (f_sub.getbbox(subtitle)[3] - f_sub.getbbox(subtitle)[1] + 2 * gap + 5) if subtitle else gap
    minimum_title_height = 13 * max(1, len(str(title).splitlines()))
    ty = max(0, min(ty, h - reserve - minimum_title_height))
    f_title = _fit_title_height(f_title, title, w // 2, max(1, h - ty - reserve))

    # 颜色装饰块属于中文主标题，放在主标题左侧并保留安全边距。
    random_color = get_random_bright_color() + (255,)
    bar_gap = 20
    bar_width = 20
    bar_height = max(60, int(t_size * 0.65))
    tx = max(tx, 16 + bar_width + bar_gap)
    bar_right = tx - bar_gap
    title_width = max(1, int(w * 0.34) - tx - 5)
    f_title = ctx.fit_single_line_font(f_title, title, title_width)
    # Pillow 的 y 是字体起点，汉字墨迹顶部通常在 y 以下几十像素。
    title_bbox = draw.multiline_textbbox((tx, ty), str(title), font=f_title)
    bar_top = int(title_bbox[1])
    bar_height = max(1, f_title.getbbox(str(title).splitlines()[0] if title else '国')[3]
                     - f_title.getbbox(str(title).splitlines()[0] if title else '国')[1])
    if title:
        draw.rectangle([bar_right - bar_width, bar_top, bar_right, bar_top + bar_height - 1], fill=random_color)
    
    title_color = ctx.resolve_text_fill(config, 'title', '#FFFFFF')
    subtitle_color = ctx.resolve_text_fill(config, 'subtitle', '#FFFFFF')
    shadow_color = (0, 0, 0, 100)
    
    title_bottom = ty
    if title:
        ctx.draw_text_wrapper(draw, title, tx+5, ty+5, f_title, title_width, shadow_color)
        title_bottom = ctx.draw_text_wrapper(draw, title, tx, ty, f_title, title_width, title_color)

    if subtitle:
        sy = max(sy, title_bottom + gap - f_sub.getbbox(subtitle)[1]) if title else sy
        sy = min(sy, h - f_sub.getbbox(subtitle)[3] - gap - 3)
        ctx.draw_text_wrapper(draw, subtitle, sx+3, sy+3, f_sub, subtitle_width, shadow_color)
        ctx.draw_text_wrapper(draw, subtitle, sx, sy, f_sub, subtitle_width, subtitle_color)
        
    return bg