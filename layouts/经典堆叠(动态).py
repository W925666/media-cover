from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _fit_text_block(ctx, title, subtitle, title_font, subtitle_font, width, height, gap):
    width, height = max(1, width), max(1, height)
    for text, is_sub in ((title, False), (subtitle, True)):
        font = subtitle_font if is_sub else title_font
        if text and (is_sub or str(text).isascii()):
            for line in str(text).splitlines():
                font = ctx.fit_single_line_font(font, line, width)
        if is_sub:
            subtitle_font = font
        else:
            title_font = font
    def text_height(text, font):
        class Measure:
            def text(self, *args, **kwargs):
                pass
        return ctx.draw_text_wrapper(Measure(), text, 0, 0, font, width, '#FFFFFF') if text else 0
    gap = max(0, gap) if title and subtitle else 0
    while text_height(title, title_font) + gap + text_height(subtitle, subtitle_font) > height:
        if not any(text and getattr(font, 'size', 1) > 1 for text, font in ((title, title_font), (subtitle, subtitle_font))):
            gap = 0
            break
        if title and getattr(title_font, 'size', 1) > 1:
            title_font = title_font.font_variant(size=title_font.size - 1)
        if subtitle and getattr(subtitle_font, 'size', 1) > 1:
            subtitle_font = subtitle_font.font_variant(size=subtitle_font.size - 1)
    return title_font, subtitle_font, gap


def add_rounded_corners(image, radius):
    if radius <= 0:
        return image.convert('RGBA')
    image = image.convert('RGBA')
    mask = Image.new('L', image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width, image.height), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def _poster_pairs(assets, limit):
    posters = list(assets.get('posters') or [])[:limit]
    backdrops = list(assets.get('poster_backdrops') or [])
    pairs = [
        (poster, backdrops[index] if index < len(backdrops) else None)
        for index, poster in enumerate(posters)
        if poster
    ]
    matched = [pair for pair in pairs if pair[1]]
    return matched or pairs


def _timeline_state(step, item_count, transition_ratio):
    if item_count <= 1:
        return 0, 0, 0.0
    position = (float(step or 0.0) % 1.0) * item_count
    current = int(position) % item_count
    phase = position - int(position)
    transition_ratio = max(0.05, min(0.7, float(transition_ratio)))
    transition_start = 1.0 - transition_ratio
    if phase <= transition_start:
        blend = 0.0
    else:
        linear = (phase - transition_start) / transition_ratio
        blend = linear * linear * (3.0 - 2.0 * linear)
    return current, (current + 1) % item_count, blend


def _state_background(ctx, fallback, config, pair):
    backdrop = ctx.download_img(pair[1]) if pair and pair[1] else None
    if backdrop is None:
        return fallback.copy()
    backdrop = ImageOps.fit(backdrop.convert('RGBA'), fallback.size, Image.LANCZOS, centering=(0.5, 0.5))
    brightness = float(config.get('background_brightness', 0.78))
    if brightness != 1.0:
        backdrop = ImageEnhance.Brightness(backdrop).enhance(brightness)
    blur_radius = int(config.get('blur_radius', 0))
    if blur_radius > 0:
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(blur_radius))
    return backdrop


def _draw_stack(ctx, canvas, config, pairs, active_index):
    ordered = pairs[active_index:] + pairs[:active_index]
    poster_urls = [poster for poster, _ in ordered]
    if not poster_urls:
        return canvas

    width, height = canvas.size
    # 动态 GIF 会直接按最终尺寸（默认 533×300）渲染。这里的尺寸参数来自
    # FrameBase 的 1920×1080 预设，必须随画布等比缩放，否则海报会比画布还高、
    # 堆叠间距也会把后排海报全部推出画面。
    design_scale = width / 1920.0
    scale = float(config.get('poster_scale', 0.9))
    front_h = max(1, int(550 * scale * design_scale))
    front_w = max(1, int(366 * scale * design_scale))
    anchor_x = int(width * (float(config.get('poster_x_percent', 55)) / 100))
    anchor_y = int(height * (float(config.get('poster_y_percent', 50)) / 100))
    offset_step = int(float(config.get('poster_gap', 140)) * scale * design_scale)
    radius = max(0, int(float(config.get('poster_radius', 20)) * design_scale))
    poster_brightness = float(config.get('poster_brightness', 1.0))
    shadow_opacity = int(config.get('poster_shadow_opacity', 160))
    shadow_blur = max(0, int(config.get('shadow_blur', 15)))
    shadow_offset = int(float(config.get('shadow_offset', 10)) * design_scale)

    for depth in range(len(poster_urls) - 1, -1, -1):
        poster = ctx.download_img(poster_urls[depth])
        if poster is None:
            continue
        if poster_brightness != 1.0:
            poster = ImageEnhance.Brightness(poster).enhance(poster_brightness)
        depth_scale = 0.9 ** depth
        target_w = max(1, int(front_w * depth_scale))
        target_h = max(1, int(front_h * depth_scale))
        poster = add_rounded_corners(poster.resize((target_w, target_h), Image.LANCZOS), radius)

        margin = max(1, shadow_blur * 2)
        layer = Image.new('RGBA', (target_w + margin * 2, target_h + margin * 2), (0, 0, 0, 0))
        shadow = Image.new('RGBA', layer.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (margin + shadow_offset, margin + shadow_offset,
             margin + shadow_offset + target_w, margin + shadow_offset + target_h),
            radius=radius,
            fill=(0, 0, 0, shadow_opacity),
        )
        if shadow_blur > 0:
            shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        layer.alpha_composite(shadow)
        layer.alpha_composite(poster, (margin, margin))

        # 预设中的锚点表示首张海报中心，而不是左上角。
        pos_x = anchor_x - front_w // 2 + depth * offset_step - margin
        pos_y = anchor_y - front_h // 2 + (front_h - target_h) // 2 - margin
        if pos_x < width + 200:
            canvas.alpha_composite(layer, (pos_x, pos_y))
    return canvas


def _draw_overlay_and_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    mask_opacity = int(config.get('mask_opacity', 120))
    mask_coverage = int(config.get('mask_coverage', 100))
    if mask_opacity > 0:
        mask = ctx.create_smart_mask(width, height, mask_opacity, mask_coverage, 'horizontal')
        black_layer = Image.new('RGBA', (width, height), (0, 0, 0, 255))
        black_layer.putalpha(mask)
        canvas = Image.alpha_composite(canvas, black_layer)

    draw = ImageDraw.Draw(canvas)
    x = int(width * (float(config.get('text_left_percent', 2)) / 100))
    y = int(height * (float(config.get('text_top_percent', 35)) / 100))
    max_width = int(width * (float(config.get('text_width_percent', 50)) / 100))
    title_font = fonts.get('font_title') or fonts.get('main')
    subtitle_font = fonts.get('font_subtitle') or fonts.get('sub') or title_font
    title_fill = ctx.resolve_text_fill(config, 'title', '#FFFFFF', config.get('title_opacity', 255))
    subtitle_fill = ctx.resolve_text_fill(config, 'subtitle', '#DDDDDD', config.get('subtitle_opacity', 200))

    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    max_width = max(1, min(max_width, width - x))
    title = config.get('title', '')
    subtitle = config.get('subtitle', '')
    title_font, subtitle_font, gap = _fit_text_block(
        ctx, title, subtitle, title_font, subtitle_font, max_width, height - y, int(config.get('gap_1', 20)))
    if title:
        y = ctx.draw_text_wrapper(draw, title, x, y, title_font, max_width, title_fill)
        y += gap
    if subtitle and y < height:
        ctx.draw_text_wrapper(draw, subtitle, x, y, subtitle_font, max_width, subtitle_fill)
    return canvas


def _render_state(ctx, fallback, config, pairs, fonts, active_index):
    canvas = _state_background(ctx, fallback, config, pairs[active_index])
    canvas = _draw_stack(ctx, canvas, config, pairs, active_index)
    return _draw_overlay_and_text(ctx, canvas, config, fonts)


def get_schema():
    return [
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "轮播海报数量", "type": "range", "min": 2, "max": 8, "default": 6},
                {"key": "title", "label": "主标题内容", "type": "text", "default": ""},
                {"key": "subtitle", "label": "副标题内容", "type": "text", "default": ""},
            ],
        },
        {
            "group": "动画设置",
            "items": [
                {"key": "enable_animation", "label": "开启动态轮播", "type": "boolean", "default": True},
                {"key": "switch_interval", "label": "主海报轮换间隔 (ms)", "type": "range", "min": 1000, "max": 5000, "step": 100, "default": 2500},
                {"key": "transition_ms", "label": "顺滑过渡时长 (ms)", "type": "range", "min": 200, "max": 1000, "step": 50, "default": 500},
                {"key": "anim_duration", "label": "动画帧间隔 (ms)", "type": "range", "min": 60, "max": 150, "step": 10, "default": 100},
            ],
        },
        {
            "group": "布局与背景",
            "items": [
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.78},
                {"key": "blur_radius", "label": "背景模糊", "type": "range", "min": 0, "max": 40, "default": 0},
                {"key": "poster_scale", "label": "海报缩放", "type": "range", "min": 0.5, "max": 1.5, "step": 0.05, "default": 0.9},
                {"key": "poster_x_percent", "label": "海报水平位置 %", "type": "range", "min": 0, "max": 100, "default": 55},
                {"key": "poster_y_percent", "label": "海报垂直位置 %", "type": "range", "min": 0, "max": 100, "default": 50},
            ],
        },
        {
            "group": "海报与遮罩",
            "items": [
                {"key": "poster_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 80, "default": 20},
                {"key": "poster_gap", "label": "海报堆叠间距", "type": "range", "min": 0, "max": 260, "default": 140},
                {"key": "poster_brightness", "label": "海报亮度", "type": "range", "min": 0.4, "max": 1.4, "step": 0.05, "default": 1.0},
                {"key": "poster_shadow_opacity", "label": "阴影浓度", "type": "range", "min": 0, "max": 255, "default": 160},
                {"key": "shadow_blur", "label": "阴影模糊", "type": "range", "min": 0, "max": 40, "default": 15},
                {"key": "shadow_offset", "label": "阴影偏移", "type": "range", "min": 0, "max": 40, "default": 10},
                {"key": "mask_opacity", "label": "背景遮罩浓度", "type": "range", "min": 0, "max": 255, "default": 120},
                {"key": "mask_coverage", "label": "遮罩范围 %", "type": "range", "min": 10, "max": 100, "default": 100},
            ],
        },
        {
            "group": "字体与排版",
            "items": [
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 260, "default": 141},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 30, "max": 150, "default": 80},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#DDDDDD"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 200},
                {"key": "text_left_percent", "label": "文字左边距 %", "type": "range", "min": 0, "max": 100, "default": 2},
                {"key": "text_top_percent", "label": "文字上边距 %", "type": "range", "min": 0, "max": 100, "default": 35},
                {"key": "text_width_percent", "label": "文字最大宽度 %", "type": "range", "min": 10, "max": 100, "default": 50},
                {"key": "gap_1", "label": "标题与副标题间距", "type": "range", "min": 0, "max": 100, "default": 20},
            ],
        },
    ]


def render(ctx, bg, config, assets, fonts, step=0.0):
    pairs = _poster_pairs(assets, max(2, int(config.get('poster_count', 6))))
    if not pairs:
        return _draw_overlay_and_text(ctx, bg.copy(), config, fonts)

    switch_interval = max(500, int(config.get('switch_interval', 2500)))
    transition_ms = max(100, min(switch_interval - 100, int(config.get('transition_ms', 500))))
    current, upcoming, blend = _timeline_state(step, len(pairs), transition_ms / switch_interval)
    current_frame = _render_state(ctx, bg, config, pairs, fonts, current)
    if blend <= 0.0 or upcoming == current:
        return current_frame
    upcoming_frame = _render_state(ctx, bg, config, pairs, fonts, upcoming)
    return Image.blend(current_frame, upcoming_frame, blend)
