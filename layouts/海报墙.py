from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageColor
import math

def _fit_title_height(font, text, max_width, available_height):
    """Match wrapper line metrics before painting, leaving room for the subtitle."""
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


def get_schema():
    return [
        # --- 1. 核心资源与内容 ---
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "获取海报数量", "type": "range", "min": 0, "max": 40, "default": 20},
                {"key": "title", "label": "主标题内容", "type": "text", "default": ""},
                {"key": "subtitle", "label": "副标题内容", "type": "text", "default": ""},
            ]
        },
        # --- 2. 布局与背景 ---
        {
            "group": "布局与背景",
            "items": [
                {"key": "col_count", "label": "海报列数", "type": "range", "min": 1, "max": 10, "default": 6},
                {"key": "poster_ratio", "label": "海报长宽比 (1.5=2:3)", "type": "range", "min": 0.5, "max": 2.0, "step": 0.1, "default": 1.5},
                {"key": "brightness", "label": "背景海报亮度", "type": "range", "min": 0.1, "max": 1.0, "step": 0.1, "default": 0.3},
            ]
        },
        # --- 3. 特效与装饰 (横条设置) ---
        {
            "group": "装饰横条设置",
            "items": [
                {"key": "bar_height", "label": "横条高度 (px)", "type": "range", "min": 100, "max": 600, "default": 300},
                {"key": "bar_opacity", "label": "横条透明度 (0-255)", "type": "range", "min": 0, "max": 255, "default": 200},
                {"key": "bar_color", "label": "横条颜色", "type": "color", "default": "#000000"},
                {"key": "line_color", "label": "装饰线颜色", "type": "color", "default": "#3b82f6"},
                {"key": "line_width", "label": "装饰线粗细", "type": "range", "min": 0, "max": 20, "default": 4},
            ]
        },
        # --- 4. 字体与排版 (最全设置) ---
        {
            "group": "字体与排版",
            "items": [
                # 主标题设置
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 300, "default": 140},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_offset_y", "label": "主标题垂直偏移", "type": "range", "min": -200, "max": 200, "default": -40},
                
                # 副标题设置
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 30, "max": 150, "default": 60},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#CCCCCC"},
                {"key": "subtitle_offset_y", "label": "副标题垂直偏移", "type": "range", "min": -200, "max": 200, "default": 40},
                
                # 通用排版
                {"key": "text_width_percent", "label": "文字最大宽度 %", "type": "range", "min": 10, "max": 100, "default": 80},
            ]
        }
    ]

def render(ctx, bg, config, assets, fonts):
    width, height = bg.size
    
    # === 1. 铺满背景的海报墙 ===
    poster_urls = assets.get('posters', [])
    if poster_urls:
        cols = int(config.get('col_count', 6))
        # 防除零错误
        if cols <= 0: cols = 6
        
        ratio = float(config.get('poster_ratio', 1.5))
        
        cell_w = width // cols
        cell_h = int(cell_w * ratio) 
        # 避免 cell_h 为 0
        if cell_h <= 0: cell_h = 100

        rows = math.ceil(height / cell_h) + 1 
        
        p_bright = float(config.get('brightness', 0.3))

        for i in range(cols * rows):
            url_index = i % len(poster_urls)
            url = poster_urls[url_index]
            
            img = ctx.download_img(url)
            if img:
                img = img.resize((cell_w, cell_h))
                img = ImageEnhance.Brightness(img).enhance(p_bright)
                
                r = i // cols
                c = i % cols
                x = c * cell_w
                y = r * cell_h
                
                bg.paste(img, (x, y))
    else:
        # 没有海报时，处理背景亮度
        bg = ImageEnhance.Brightness(bg).enhance(float(config.get('brightness', 0.3)))

    # === 2. 中间加一个横向的半透明黑框 ===
    bar_height = int(config.get('bar_height', 300))
    bar_y = (height - bar_height) // 2
    
    # 解析横条颜色和透明度
    bar_hex = config.get('bar_color', '#000000')
    bar_alpha = int(config.get('bar_opacity', 200))
    try:
        r, g, b = ImageColor.getrgb(bar_hex)
        fill_color = (r, g, b, bar_alpha)
    except:
        fill_color = (0, 0, 0, bar_alpha)
        
    line_color = config.get('line_color', '#3b82f6')
    line_width = int(config.get('line_width', 4))
    
    overlay = Image.new('RGBA', (width, height), (0,0,0,0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # 画矩形
    draw_overlay.rectangle([(0, bar_y), (width, bar_y + bar_height)], fill=fill_color)
    
    # 画装饰线 
    if line_width > 0:
        # 只画底部线条，符合原设计意图
        draw_overlay.line([(0, bar_y + bar_height), (width, bar_y + bar_height)], fill=line_color, width=line_width)
    
    bg = Image.alpha_composite(bg, overlay)

    # === 3. 居中写字 ===
    draw = ImageDraw.Draw(bg)
    cx = width // 2
    cy_center = height // 2 
    mw = int(width * (float(config.get('text_width_percent', 80)) / 100))

    # --- 字体加载修复 ---
    # 优先使用 schema 中定义的 key，找不到则回退到旧版 key
    f_title = fonts.get('font_title') or fonts.get('main')
    f_sub = fonts.get('font_subtitle') or fonts.get('sub') or f_title

    # 绘制标题
    title = config.get('title', '')
    title_off = int(config.get('title_offset_y', -40))
    title_color = ctx.resolve_text_fill(config, 'title', '#FFFFFF')
    
    title_bottom = cy_center + title_off
    subtitle = config.get('subtitle', '')
    if subtitle:
        f_sub = ctx.fit_single_line_font(f_sub, subtitle, max(1, min(mw, width)))
    gap = max(4, round(height * 0.018))
    reserve = (f_sub.getbbox(subtitle)[3] - f_sub.getbbox(subtitle)[1] + 2 * gap) if subtitle else gap
    minimum_title_height = 13 * max(1, len(str(title).splitlines()))
    title_bottom = max(0, min(title_bottom, height - reserve - minimum_title_height))
    f_title = _fit_title_height(f_title, title, max(1, mw), max(1, height - title_bottom - reserve))
    if title:
        title_bottom = ctx.draw_text_wrapper(draw, title, cx, title_bottom, f_title, mw, title_color, align='center')
    
    # 绘制副标题
    subtitle = config.get('subtitle', '')
    sub_off = int(config.get('subtitle_offset_y', 40))
    sub_color = ctx.resolve_text_fill(config, 'subtitle', '#CCCCCC')
    
    if subtitle:
        f_sub = ctx.fit_single_line_font(f_sub, subtitle, max(1, min(mw, width)))
        gap = max(4, round(height * 0.018))
        sub_y = max(cy_center + sub_off, title_bottom + gap - f_sub.getbbox(subtitle)[1]) if title else cy_center + sub_off
        sub_y = min(sub_y, height - f_sub.getbbox(subtitle)[3] - gap)
        ctx.draw_text_wrapper(draw, subtitle, cx, sub_y, f_sub, mw, sub_color, align='center')
    
    return bg
