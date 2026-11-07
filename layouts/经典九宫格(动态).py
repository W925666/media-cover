from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _rounded(image, radius):
    image = image.convert("RGBA")
    if radius <= 0:
        return image
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1),
        radius=radius,
        fill=255,
    )
    image.putalpha(mask)
    return image


def _poster_pairs(assets, limit):
    posters = list(assets.get("posters") or [])[:limit]
    backdrops = list(assets.get("poster_backdrops") or [])
    pairs = [
        (poster, backdrops[index] if index < len(backdrops) else None)
        for index, poster in enumerate(posters)
        if poster
    ]
    matched = [pair for pair in pairs if pair[1]]
    return matched or pairs


def _timeline_state(step, item_count, direction="diagonal_down"):
    if item_count <= 1:
        return 0, 0, 0.0
    position = (float(step or 0.0) % 1.0) * item_count
    tick = int(position)
    index_step = -1 if direction == "diagonal_down" else 1
    current = (4 + tick * index_step) % item_count
    return current, (current + index_step) % item_count, position - tick


def _background(ctx, fallback, config, pair):
    backdrop = ctx.download_img(pair[1]) if pair and pair[1] else None
    if backdrop is None:
        canvas = fallback.copy().convert("RGBA")
    else:
        canvas = ImageOps.fit(backdrop.convert("RGBA"), fallback.size, method=Image.LANCZOS)
    brightness = float(config.get("background_brightness", 0.72))
    if brightness != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
    blur_radius = int(config.get("bg_blur", 0))
    if blur_radius > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur_radius))
    overlay = int(config.get("background_overlay", 70))
    if overlay > 0:
        canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, overlay)))
    return canvas


def _add_shadow(image, blur_radius, shadow_opacity):
    offset = (15, 15)
    width, height = image.size
    shadow_width = width + abs(offset[0]) + blur_radius * 2
    shadow_height = height + abs(offset[1]) + blur_radius * 2
    shadow = Image.new("RGBA", (shadow_width, shadow_height), (0, 0, 0, 0))
    if shadow_opacity > 0:
        shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, shadow_opacity))
        shadow.paste(
            shadow_layer,
            (blur_radius + max(0, offset[0]), blur_radius + max(0, offset[1])),
        )
        if blur_radius > 0:
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    result = Image.new("RGBA", (shadow_width, shadow_height), (0, 0, 0, 0))
    result.alpha_composite(shadow)
    result.alpha_composite(
        image,
        (blur_radius + max(0, -offset[0]), blur_radius + max(0, -offset[1])),
    )
    return result


def _draw_grid(ctx, canvas, config, pairs, tick, progress, direction):
    cell_width = int(config.get("cell_width", 410))
    cell_height = int(config.get("cell_height", 610))
    radius = int(config.get("corner_radius", 46))
    rotation = float(config.get("rotation", -16))
    start_x = int(config.get("start_x", 350))
    start_y = int(config.get("start_y", -200))
    column_gap = int(config.get("col_spacing", 50))
    row_gap = int(config.get("row_spacing", 22))
    column_lift = int(config.get("column_lift", -80))
    shadow_blur = int(config.get("shadow_blur", 15))
    shadow_opacity = int(config.get("shadow_opacity", 180))

    card_cache = {}
    for poster_url, _ in pairs:
        if poster_url in card_cache:
            continue
        url = poster_url
        poster = ctx.download_img(url)
        if poster is None:
            continue
        poster = ImageOps.fit(poster.convert("RGBA"), (cell_width, cell_height), method=Image.LANCZOS)
        poster = _rounded(poster, radius)
        card_cache[url] = _add_shadow(poster, shadow_blur, shadow_opacity)

    stage = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    if not card_cache:
        return Image.alpha_composite(canvas, stage)
    sample = next(iter(card_cache.values()))
    row_step = sample.height - 30
    index_step = -1 if direction == "diagonal_down" else 1
    travel = row_step * progress * (1 if direction == "diagonal_down" else -1)
    rows = range(-1, 3) if direction == "diagonal_down" else range(0, 4)
    for column in range(3):
        column_canvas = Image.new(
            "RGBA",
            (sample.width, sample.height * 3 + row_gap * 3),
            (0, 0, 0, 0),
        )
        for row in rows:
            pair_index = (column * 3 + row + tick * index_step) % len(pairs)
            card = card_cache.get(pairs[pair_index][0])
            if card is None:
                continue
            column_canvas.alpha_composite(card, (0, round(row * row_step + travel)))
        rotated_column = column_canvas.rotate(rotation, resample=Image.BICUBIC, expand=True)
        x = start_x + column * (cell_width - 50 + column_gap)
        y = start_y + column * column_lift
        stage.alpha_composite(rotated_column, (round(x), round(y)))
    return Image.alpha_composite(canvas, stage)


def _draw_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    title = config.get("title", "")
    subtitle = config.get("subtitle", "")
    title_font = fonts.get("font_title") or fonts.get("main")
    subtitle_font = fonts.get("font_subtitle") or fonts.get("sub") or title_font
    title_x = int(width * float(config.get("title_x", 5)) / 100)
    title_y = int(height * float(config.get("title_y", 40)) / 100)
    subtitle_x = int(width * float(config.get("subtitle_x", 8)) / 100)
    subtitle_y = int(height * float(config.get("subtitle_y", 58)) / 100)
    accent = config.get("accent_color", "#32D6C6")
    draw.rectangle((subtitle_x - 40, subtitle_y, subtitle_x - 20, subtitle_y + 100), fill=accent)
    if title:
        ctx.draw_text_wrapper(
            draw, title, title_x, title_y, title_font, width // 2,
            ctx.resolve_text_fill(config, "title", "#FFFFFF", config.get("title_opacity", 255)),
        )
    if subtitle:
        ctx.draw_text_wrapper(
            draw, subtitle, subtitle_x, subtitle_y, subtitle_font, width // 3,
            ctx.resolve_text_fill(config, "subtitle", "#FFFFFF", config.get("subtitle_opacity", 230)),
        )
    return canvas


def get_schema():
    return [
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "九宫格海报数量", "type": "range", "min": 3, "max": 9, "default": 9},
                {"key": "title", "label": "主标题内容", "type": "text", "default": ""},
                {"key": "subtitle", "label": "副标题内容", "type": "text", "default": ""},
            ],
        },
        {
            "group": "动画设置",
            "items": [
                {"key": "enable_animation", "label": "开启动态斜滑", "type": "boolean", "default": True},
                {"key": "switch_interval", "label": "单格连续滑动时长 (ms)", "type": "range", "min": 1000, "max": 5000, "step": 100, "default": 2500},
                {"key": "background_transition_ms", "label": "背景渐变时长 (ms)", "type": "range", "min": 300, "max": 1200, "step": 50, "default": 700},
                {"key": "slide_direction", "label": "斜滑方向", "type": "select", "options": [
                    {"value": "diagonal_down", "label": "向下斜滑"},
                    {"value": "diagonal_up", "label": "向上斜滑"},
                ], "default": "diagonal_down"},
                {"key": "anim_duration", "label": "动画帧间隔 (ms)", "type": "range", "min": 60, "max": 150, "step": 10, "default": 100},
            ],
        },
        {
            "group": "九宫格布局",
            "items": [
                {"key": "cell_width", "label": "单张海报宽", "type": "range", "min": 200, "max": 600, "default": 410},
                {"key": "cell_height", "label": "单张海报高", "type": "range", "min": 300, "max": 900, "default": 610},
                {"key": "corner_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 100, "default": 46},
                {"key": "rotation", "label": "九宫格旋转角度", "type": "range", "min": -45, "max": 45, "default": -16},
                {"key": "start_x", "label": "起始位置 X", "type": "range", "min": 0, "max": 2000, "default": 350},
                {"key": "start_y", "label": "起始位置 Y", "type": "range", "min": -1000, "max": 1000, "default": -200},
                {"key": "col_spacing", "label": "列间距", "type": "range", "min": -100, "max": 300, "default": 50},
                {"key": "row_spacing", "label": "行间距", "type": "range", "min": -100, "max": 200, "default": 22},
                {"key": "column_lift", "label": "列错落高度", "type": "range", "min": -200, "max": 200, "default": -80},
            ],
        },
        {
            "group": "背景与文字",
            "items": [
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.72},
                {"key": "bg_blur", "label": "背景模糊", "type": "range", "min": 0, "max": 80, "default": 0},
                {"key": "background_overlay", "label": "背景压暗", "type": "range", "min": 0, "max": 255, "default": 70},
                {"key": "shadow_opacity", "label": "海报阴影", "type": "range", "min": 0, "max": 255, "default": 180},
                {"key": "shadow_blur", "label": "阴影模糊", "type": "range", "min": 0, "max": 40, "default": 15},
                {"key": "accent_color", "label": "装饰色", "type": "color", "default": "#32D6C6"},
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 300, "default": 160},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "title_x", "label": "主标题 X%", "type": "range", "min": 0, "max": 100, "default": 5},
                {"key": "title_y", "label": "主标题 Y%", "type": "range", "min": 0, "max": 100, "default": 40},
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 20, "max": 150, "default": 50},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 230},
                {"key": "subtitle_x", "label": "副标题 X%", "type": "range", "min": 0, "max": 100, "default": 8},
                {"key": "subtitle_y", "label": "副标题 Y%", "type": "range", "min": 0, "max": 100, "default": 58},
            ],
        },
    ]


def render(ctx, bg, config, assets, fonts, step=0.0):
    pairs = _poster_pairs(assets, max(3, int(config.get("poster_count", 9))))
    if not pairs:
        return _draw_text(ctx, bg.copy().convert("RGBA"), config, fonts)
    direction = config.get("slide_direction", "diagonal_down")
    current, upcoming, progress = _timeline_state(step, len(pairs), direction)
    tick = int((float(step or 0.0) % 1.0) * len(pairs))
    canvas = _background(ctx, bg, config, pairs[current])
    switch_interval = max(500, int(config.get("switch_interval", 2500)))
    transition_ms = max(100, min(switch_interval, int(config.get("background_transition_ms", 700))))
    transition_start = 1.0 - transition_ms / switch_interval
    if progress > transition_start and current != upcoming:
        blend = (progress - transition_start) / max(0.001, 1.0 - transition_start)
        blend = blend * blend * (3.0 - 2.0 * blend)
        canvas = Image.blend(canvas, _background(ctx, bg, config, pairs[upcoming]), blend)
    canvas = _draw_grid(ctx, canvas, config, pairs, tick, progress, direction)
    return _draw_text(ctx, canvas, config, fonts)
