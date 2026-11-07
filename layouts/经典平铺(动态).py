from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def add_rounded_corners(image, radius):
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


def _timeline_state(step, item_count, transition_ratio):
    if item_count <= 1:
        return 0, 0, 0.0
    position = (float(step or 0.0) % 1.0) * item_count
    current = int(position) % item_count
    phase = position - int(position)
    transition_ratio = max(0.05, min(0.7, float(transition_ratio)))
    transition_start = 1.0 - transition_ratio
    if phase <= transition_start:
        slide = 0.0
    else:
        linear = (phase - transition_start) / transition_ratio
        slide = linear * linear * (3.0 - 2.0 * linear)
    return current, (current + 1) % item_count, slide


def _pair_background(ctx, fallback, config, pair):
    backdrop = ctx.download_img(pair[1]) if pair and pair[1] else None
    if backdrop is None:
        return fallback.copy().convert("RGBA")
    backdrop = ImageOps.fit(
        backdrop.convert("RGBA"),
        fallback.size,
        Image.LANCZOS,
        centering=(0.5, 0.5),
    )
    brightness = float(config.get("background_brightness", 0.82))
    if brightness != 1.0:
        backdrop = ImageEnhance.Brightness(backdrop).enhance(brightness)
    blur_radius = int(config.get("blur_radius", 0))
    if blur_radius > 0:
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(blur_radius))
    return backdrop


def _background_for_state(ctx, fallback, config, pairs, current, upcoming, slide):
    current_bg = _pair_background(ctx, fallback, config, pairs[current])
    if slide <= 0.0 or upcoming == current:
        return current_bg
    upcoming_bg = _pair_background(ctx, fallback, config, pairs[upcoming])
    return Image.blend(current_bg, upcoming_bg, slide)


def _poster_layer(ctx, url, width, height, config, emphasis):
    poster = ctx.download_img(url)
    if poster is None:
        return None

    side_brightness = float(config.get("side_poster_brightness", 0.82))
    brightness = side_brightness + (1.0 - side_brightness) * emphasis
    poster = poster.resize((width, height), Image.LANCZOS)
    if brightness != 1.0:
        poster = ImageEnhance.Brightness(poster).enhance(brightness)

    radius = int(config.get("poster_corner_radius", 12))
    poster = add_rounded_corners(poster, radius)
    border_width = max(0, int(round(float(config.get("active_border_width", 4)) * emphasis)))
    if border_width:
        border = Image.new("RGBA", poster.size, (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle(
            (border_width // 2, border_width // 2, width - 1 - border_width // 2, height - 1 - border_width // 2),
            radius=max(1, radius),
            outline=config.get("active_border_color", "#FFFFFF"),
            width=border_width,
        )
        poster = Image.alpha_composite(poster, border)

    shadow_blur = int(config.get("shadow_blur", 14))
    margin = max(8, shadow_blur * 2)
    shadow_opacity = int(config.get("poster_shadow_opacity", 150))
    layer = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    if shadow_opacity > 0:
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin + 5, margin + width, margin + 5 + height),
            radius=radius,
            fill=(0, 0, 0, shadow_opacity),
        )
        if shadow_blur > 0:
            shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        layer.alpha_composite(shadow)
    layer.alpha_composite(poster, (margin, margin))
    return layer


def _with_opacity(image, opacity):
    if opacity >= 1.0:
        return image
    result = image.copy()
    result.putalpha(result.getchannel("A").point(lambda value: round(value * max(0.0, opacity))))
    return result


def _draw_carousel(ctx, canvas, config, pairs, current, slide):
    width, height = canvas.size
    pair_count = len(pairs)
    if not pair_count:
        return canvas

    poster_height = int(height * (float(config.get("poster_height_percent", 31)) / 100))
    poster_height = max(140, min(int(height * 0.52), poster_height))
    poster_width = max(90, round(poster_height * (float(config.get("poster_ratio", 0.6667)))))
    spacing = int(config.get("poster_spacing", 18))
    unit_width = max(40, poster_width + spacing)
    center_x = int(width * (float(config.get("active_x_percent", 50)) / 100))
    baseline_y = int(height * (float(config.get("poster_baseline_percent", 98)) / 100))
    hero_scale = float(config.get("active_scale", 1.08))

    visible_count = min(pair_count, max(1, int(config.get("poster_count", pair_count))))
    left_count = visible_count // 2
    normal_offsets = list(range(-left_count, visible_count - left_count))
    center_bias = (normal_offsets[0] + normal_offsets[-1]) / 2.0
    incoming_offset = normal_offsets[-1] + 1
    render_items = []
    for offset in normal_offsets + [incoming_offset]:
        distance = abs(offset - slide)
        emphasis = max(0.0, 1.0 - min(1.0, distance))
        scale = 1.0 + (hero_scale - 1.0) * emphasis
        target_w = max(1, int(poster_width * scale))
        target_h = max(1, int(poster_height * scale))
        center = center_x + (offset - slide - center_bias) * unit_width
        pair_index = (current + offset) % pair_count
        render_items.append({
            "url": pairs[pair_index][0],
            "x": int(center - target_w / 2),
            "y": int(baseline_y - target_h),
            "width": target_w,
            "height": target_h,
            "emphasis": emphasis,
            "distance": distance,
            "opacity": slide if offset == incoming_offset else 1.0,
        })

    render_items.sort(key=lambda item: item["distance"], reverse=True)
    for item in render_items:
        layer = _poster_layer(
            ctx,
            item["url"],
            item["width"],
            item["height"],
            config,
            item["emphasis"],
        )
        if layer is None:
            continue
        layer = _with_opacity(layer, item["opacity"])
        margin_x = (layer.width - item["width"]) // 2
        margin_y = (layer.height - item["height"]) // 2
        canvas.alpha_composite(layer, (item["x"] - margin_x, item["y"] - margin_y))
    return canvas


def _draw_overlay_and_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    mask_opacity = int(config.get("mask_opacity", 150))
    mask_coverage = int(config.get("mask_coverage", 72))
    if mask_opacity > 0:
        mask = ctx.create_smart_mask(width, height, mask_opacity, mask_coverage, "horizontal")
        shade = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
        shade.putalpha(mask)
        canvas = Image.alpha_composite(canvas, shade)

    draw = ImageDraw.Draw(canvas)
    x = int(width * (float(config.get("text_left_percent", 5)) / 100))
    y = int(height * (float(config.get("text_top_percent", 21)) / 100))
    max_width = int(width * (float(config.get("text_width_percent", 46)) / 100))
    title_font = fonts.get("font_title") or fonts.get("main")
    subtitle_font = fonts.get("font_subtitle") or fonts.get("sub") or title_font
    title_fill = ctx.resolve_text_fill(config, "title", "#FFFFFF", config.get("title_opacity", 255))
    subtitle_fill = ctx.resolve_text_fill(config, "subtitle", "#DDDDDD", config.get("subtitle_opacity", 210))

    title = config.get("title", "")
    if title:
        y = ctx.draw_text_wrapper(draw, title, x, y, title_font, max_width, title_fill)
        y += int(config.get("text_gap", 20))
    subtitle = config.get("subtitle", "")
    if subtitle:
        ctx.draw_text_wrapper(draw, subtitle, x, y, subtitle_font, max_width, subtitle_fill)
    return canvas


def get_schema():
    return [
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "轮播海报数量（整体居中）", "type": "range", "min": 3, "max": 12, "default": 8},
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
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.82},
                {"key": "blur_radius", "label": "背景模糊", "type": "range", "min": 0, "max": 30, "default": 0},
                {"key": "active_x_percent", "label": "主海报水平位置 %", "type": "range", "min": 20, "max": 80, "default": 50},
                {"key": "poster_baseline_percent", "label": "海报底部位置 %", "type": "range", "min": 65, "max": 100, "default": 98},
                {"key": "poster_height_percent", "label": "海报高度 %", "type": "range", "min": 18, "max": 50, "default": 31},
                {"key": "poster_ratio", "label": "海报宽高比", "type": "range", "min": 0.5, "max": 0.9, "step": 0.01, "default": 0.67},
                {"key": "poster_spacing", "label": "海报间距", "type": "range", "min": -80, "max": 100, "default": 18},
                {"key": "active_scale", "label": "主海报突出比例", "type": "range", "min": 1.0, "max": 1.3, "step": 0.01, "default": 1.08},
            ],
        },
        {
            "group": "海报与遮罩",
            "items": [
                {"key": "poster_corner_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 50, "default": 12},
                {"key": "side_poster_brightness", "label": "侧边海报亮度", "type": "range", "min": 0.3, "max": 1.0, "step": 0.05, "default": 0.82},
                {"key": "active_border_width", "label": "主海报描边", "type": "range", "min": 0, "max": 12, "default": 4},
                {"key": "active_border_color", "label": "主海报描边颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "poster_shadow_opacity", "label": "海报阴影", "type": "range", "min": 0, "max": 255, "default": 150},
                {"key": "shadow_blur", "label": "阴影模糊", "type": "range", "min": 0, "max": 40, "default": 14},
                {"key": "mask_opacity", "label": "文字区遮罩浓度", "type": "range", "min": 0, "max": 255, "default": 150},
                {"key": "mask_coverage", "label": "遮罩范围 %", "type": "range", "min": 10, "max": 100, "default": 72},
            ],
        },
        {
            "group": "字体与排版",
            "items": [
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 260, "default": 130},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 30, "max": 150, "default": 80},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#DDDDDD"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 210},
                {"key": "text_left_percent", "label": "文字左边距 %", "type": "range", "min": 0, "max": 100, "default": 5},
                {"key": "text_top_percent", "label": "文字上边距 %", "type": "range", "min": 0, "max": 100, "default": 21},
                {"key": "text_width_percent", "label": "文字最大宽度 %", "type": "range", "min": 10, "max": 80, "default": 46},
                {"key": "text_gap", "label": "标题与副标题间距", "type": "range", "min": 0, "max": 100, "default": 20},
            ],
        },
    ]


def render(ctx, bg, config, assets, fonts, step=0.0):
    pairs = _poster_pairs(assets, max(3, int(config.get("poster_count", 8))))
    if not pairs:
        return _draw_overlay_and_text(ctx, bg.copy().convert("RGBA"), config, fonts)

    switch_interval = max(500, int(config.get("switch_interval", 2500)))
    transition_ms = max(100, min(switch_interval - 100, int(config.get("transition_ms", 500))))
    current, upcoming, slide = _timeline_state(step, len(pairs), transition_ms / switch_interval)
    canvas = _background_for_state(ctx, bg, config, pairs, current, upcoming, slide)
    canvas = _draw_overlay_and_text(ctx, canvas, config, fonts)
    return _draw_carousel(ctx, canvas, config, pairs, current, slide)
