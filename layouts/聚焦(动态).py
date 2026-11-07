import math

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


def add_reflection(image, opacity=50, decay_rate=1.5):
    reflection = ImageOps.flip(image.convert("RGBA"))
    mask = Image.new("L", reflection.size, 0)
    draw = ImageDraw.Draw(mask)
    for y in range(reflection.height):
        progress = y / max(1, reflection.height - 1)
        alpha = max(0, int(opacity * (1.0 - math.pow(progress, decay_rate))))
        draw.line((0, y, reflection.width, y), fill=alpha)
    reflection.putalpha(mask)
    return reflection


def create_radial_gradient(size, center_alpha, edge_alpha):
    width, height = size
    light = Image.new("L", size, 0)
    radius = int(min(width, height) / 1.5)
    center_x, center_y = width // 2, height // 2
    ImageDraw.Draw(light).ellipse(
        (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
        fill=255,
    )
    light = light.filter(ImageFilter.GaussianBlur(max(1, radius // 2)))
    center = Image.new("RGBA", size, (255, 255, 255, center_alpha))
    edge = Image.new("RGBA", size, (0, 0, 0, edge_alpha))
    return Image.composite(center, edge, light)


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
        blend = 0.0
    else:
        linear = (phase - transition_start) / transition_ratio
        blend = linear * linear * (3.0 - 2.0 * linear)
    return current, (current + 1) % item_count, blend


def _state_background(ctx, fallback, config, pair):
    backdrop = ctx.download_img(pair[1]) if pair and pair[1] else None
    if backdrop is None:
        canvas = fallback.copy().convert("RGBA")
    else:
        canvas = ImageOps.fit(
            backdrop.convert("RGBA"),
            fallback.size,
            Image.LANCZOS,
            centering=(0.5, 0.5),
        )

    brightness = float(config.get("background_brightness", 0.78))
    if brightness != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
    blur_radius = int(config.get("bg_blur", 0))
    if blur_radius > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur_radius))

    overlay_opacity = int(config.get("bg_overlay_opacity", 85))
    if overlay_opacity > 0:
        canvas = Image.alpha_composite(
            canvas,
            Image.new("RGBA", canvas.size, (0, 0, 0, overlay_opacity)),
        )
    spotlight = create_radial_gradient(
        canvas.size,
        int(config.get("spotlight_intensity", 28)),
        int(config.get("bg_vignette", 150)),
    )
    return Image.alpha_composite(canvas, spotlight)


def _slot_x(hero_width, scale_step, overlap, distance):
    result = 0.0
    for index in range(distance):
        result += hero_width * math.pow(scale_step, index) * (1.0 - overlap)
    return result


def _draw_focus_stage(ctx, canvas, config, pairs, active_index):
    width, height = canvas.size
    requested_count = min(len(pairs), max(1, int(config.get("poster_count", 9))))
    if requested_count > 1 and requested_count % 2 == 0:
        requested_count -= 1
    half = requested_count // 2

    hero_height = int(height * (float(config.get("hero_height_percent", 51)) / 100))
    hero_width = int(hero_height * float(config.get("poster_ratio", 0.66)))
    baseline = int(height * (float(config.get("baseline_percent", 94)) / 100))
    scale_step = float(config.get("side_scale_step", 0.9))
    overlap = float(config.get("overlap_percent", 40)) / 100
    darken_step = int(config.get("perspective_darken", 40))
    radius = int(config.get("corner_radius", 16))
    reflection_opacity = int(config.get("reflection_opacity", 42))
    shadow_opacity = int(config.get("hero_shadow_opacity", 180))
    center_x = width // 2

    render_queue = []
    for offset in range(-half, half + 1):
        distance = abs(offset)
        scale = math.pow(scale_step, distance)
        item_height = max(1, int(hero_height * scale))
        item_width = max(1, int(hero_width * scale))
        displacement = _slot_x(hero_width, scale_step, overlap, distance)
        x = center_x - displacement if offset < 0 else center_x + displacement
        pair_index = (active_index + offset) % len(pairs)
        render_queue.append({
            "url": pairs[pair_index][0],
            "width": item_width,
            "height": item_height,
            "x": int(x),
            "brightness": max(0.25, 1.0 - distance * darken_step / 255.0),
            "distance": distance,
            "hero": offset == 0,
        })

    render_queue.sort(key=lambda item: item["distance"], reverse=True)
    for item in render_queue:
        poster = ctx.download_img(item["url"])
        if poster is None:
            continue
        poster = add_rounded_corners(
            poster.resize((item["width"], item["height"]), Image.LANCZOS),
            radius,
        )
        if item["brightness"] < 1.0:
            poster = ImageEnhance.Brightness(poster).enhance(item["brightness"])
        paste_x = item["x"] - item["width"] // 2
        paste_y = baseline - item["height"]

        if reflection_opacity > 0:
            reflection = add_reflection(poster, reflection_opacity)
            canvas.alpha_composite(reflection, (paste_x, baseline))

        if item["hero"] and shadow_opacity > 0:
            margin = 40
            shadow = Image.new(
                "RGBA",
                (item["width"] + margin * 2, item["height"] + margin * 2),
                (0, 0, 0, 0),
            )
            ImageDraw.Draw(shadow).rounded_rectangle(
                (margin, margin, margin + item["width"], margin + item["height"]),
                radius=radius,
                fill=(0, 0, 0, shadow_opacity),
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(20))
            canvas.alpha_composite(shadow, (paste_x - margin, paste_y - margin + 10))
        canvas.alpha_composite(poster, (paste_x, paste_y))
    return canvas


def _draw_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    hero_height = int(height * (float(config.get("hero_height_percent", 51)) / 100))
    baseline = int(height * (float(config.get("baseline_percent", 94)) / 100))
    base_y = max(35, (baseline - hero_height) // 2)
    center_x = width // 2
    max_width = int(width * 0.82)
    title_font = fonts.get("font_title") or fonts.get("main")
    subtitle_font = fonts.get("font_subtitle") or fonts.get("sub") or title_font
    draw = ImageDraw.Draw(canvas)

    title = config.get("title", "")
    if title:
        title_fill = ctx.resolve_text_fill(config, "title", "#FFFFFF", config.get("title_opacity", 255))
        ctx.draw_text_wrapper(
            draw,
            title,
            center_x,
            base_y + int(config.get("title_y_offset", -80)),
            title_font,
            max_width,
            title_fill,
            align="center",
        )
    subtitle = config.get("subtitle", "")
    if subtitle:
        subtitle_fill = ctx.resolve_text_fill(config, "subtitle", "#AAAAAA", config.get("subtitle_opacity", 210))
        subtitle_y = (
            base_y
            + int(config.get("title_y_offset", -80))
            + int(config.get("title_size", 150))
            + int(config.get("subtitle_offset_y", 20))
        )
        ctx.draw_text_wrapper(
            draw,
            subtitle,
            center_x,
            subtitle_y,
            subtitle_font,
            max_width,
            subtitle_fill,
            align="center",
        )
    return canvas


def _render_state(ctx, fallback, config, pairs, fonts, active_index):
    canvas = _state_background(ctx, fallback, config, pairs[active_index])
    canvas = _draw_focus_stage(ctx, canvas, config, pairs, active_index)
    return _draw_text(ctx, canvas, config, fonts)


def get_schema():
    return [
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "轮播海报数量（奇数最佳）", "type": "range", "min": 3, "max": 11, "step": 2, "default": 9},
                {"key": "title", "label": "主标题内容", "type": "text", "default": ""},
                {"key": "subtitle", "label": "副标题内容", "type": "text", "default": ""},
            ],
        },
        {
            "group": "动画设置",
            "items": [
                {"key": "enable_animation", "label": "开启动态轮播", "type": "boolean", "default": True},
                {"key": "switch_interval", "label": "C 位轮换间隔 (ms)", "type": "range", "min": 1000, "max": 5000, "step": 100, "default": 2500},
                {"key": "transition_ms", "label": "顺滑过渡时长 (ms)", "type": "range", "min": 200, "max": 1000, "step": 50, "default": 500},
                {"key": "anim_duration", "label": "动画帧间隔 (ms)", "type": "range", "min": 60, "max": 150, "step": 10, "default": 100},
            ],
        },
        {
            "group": "舞台布局与背景",
            "items": [
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.78},
                {"key": "bg_blur", "label": "背景模糊", "type": "range", "min": 0, "max": 50, "default": 0},
                {"key": "hero_height_percent", "label": "C 位海报高度 %", "type": "range", "min": 30, "max": 75, "default": 51},
                {"key": "poster_ratio", "label": "海报宽高比", "type": "range", "min": 0.5, "max": 0.9, "step": 0.01, "default": 0.66},
                {"key": "baseline_percent", "label": "舞台垂直基线 %", "type": "range", "min": 60, "max": 100, "default": 94},
                {"key": "side_scale_step", "label": "侧边缩放级差", "type": "range", "min": 0.6, "max": 0.98, "step": 0.01, "default": 0.9},
                {"key": "overlap_percent", "label": "海报重叠程度 %", "type": "range", "min": 0, "max": 80, "step": 5, "default": 40},
            ],
        },
        {
            "group": "光影与特效",
            "items": [
                {"key": "corner_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 40, "default": 16},
                {"key": "perspective_darken", "label": "侧边压暗程度", "type": "range", "min": 0, "max": 255, "default": 40},
                {"key": "reflection_opacity", "label": "倒影强度", "type": "range", "min": 0, "max": 100, "default": 42},
                {"key": "hero_shadow_opacity", "label": "C 位阴影", "type": "range", "min": 0, "max": 255, "default": 180},
                {"key": "bg_overlay_opacity", "label": "背景压暗遮罩", "type": "range", "min": 0, "max": 255, "default": 85},
                {"key": "spotlight_intensity", "label": "中心聚光灯", "type": "range", "min": 0, "max": 100, "default": 28},
                {"key": "bg_vignette", "label": "边缘暗角", "type": "range", "min": 0, "max": 255, "default": 150},
            ],
        },
        {
            "group": "字体与排版",
            "items": [
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 280, "default": 150},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "title_y_offset", "label": "主标题垂直偏移", "type": "range", "min": -300, "max": 200, "default": -80},
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 20, "max": 150, "default": 70},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#AAAAAA"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 210},
                {"key": "subtitle_offset_y", "label": "副标题垂直偏移", "type": "range", "min": -100, "max": 200, "default": 20},
            ],
        },
    ]


def render(ctx, bg, config, assets, fonts, step=0.0):
    pairs = _poster_pairs(assets, max(3, int(config.get("poster_count", 9))))
    if not pairs:
        return _draw_text(ctx, bg.copy().convert("RGBA"), config, fonts)

    switch_interval = max(500, int(config.get("switch_interval", 2500)))
    transition_ms = max(100, min(switch_interval - 100, int(config.get("transition_ms", 500))))
    current, upcoming, blend = _timeline_state(step, len(pairs), transition_ms / switch_interval)
    current_frame = _render_state(ctx, bg, config, pairs, fonts, current)
    if blend <= 0.0 or current == upcoming:
        return current_frame
    upcoming_frame = _render_state(ctx, bg, config, pairs, fonts, upcoming)
    return Image.blend(current_frame, upcoming_frame, blend)
