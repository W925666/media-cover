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


def create_reflection(image, opacity):
    reflection = ImageOps.flip(image.convert("RGBA"))
    mask = Image.new("L", reflection.size, 0)
    draw = ImageDraw.Draw(mask)
    for y in range(reflection.height):
        progress = y / max(1, reflection.height - 1)
        draw.line((0, y, reflection.width, y), fill=max(0, round(opacity * (1.0 - progress))))
    reflection.putalpha(mask)
    return reflection


def create_card_group(image, reflection_opacity, shadow_blur):
    width, height = image.size
    padding = max(20, shadow_blur * 2)
    group = Image.new("RGBA", (width + padding * 2, height * 2 + padding * 2), (0, 0, 0, 0))
    if shadow_blur > 0:
        shadow = Image.new("RGBA", group.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (padding, padding + 8, padding + width, padding + 8 + height),
            radius=15,
            fill=(0, 0, 0, 155),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        group.alpha_composite(shadow)
    if reflection_opacity > 0:
        group.alpha_composite(create_reflection(image, reflection_opacity), (padding, padding + height))
    group.alpha_composite(image, (padding, padding))
    return group


def _with_opacity(image, opacity):
    if opacity >= 1.0:
        return image
    result = image.copy()
    result.putalpha(result.getchannel("A").point(lambda value: round(value * max(0.0, opacity))))
    return result


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
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )
    brightness = float(config.get("background_brightness", 0.82))
    if brightness != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
    blur_radius = int(config.get("bg_blur", 0))
    if blur_radius > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur_radius))
    darkness = int(config.get("bg_darkness", 100))
    if darkness > 0:
        canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, darkness)))
    return canvas


def _draw_fan(ctx, canvas, config, pairs, active_index, progress=0.0):
    width, height = canvas.size
    requested_count = min(len(pairs), max(1, int(config.get("poster_count", 9))))
    if requested_count > 1 and requested_count % 2 == 0:
        requested_count -= 1
    midpoint = requested_count // 2
    fan_radius = int(config.get("fan_radius", 850))
    fan_spread = float(config.get("fan_spread", 90))
    center_scale = float(config.get("center_scale", 0.5))
    shrink_ratio = float(config.get("side_scale_shrink", 0.88))
    y_offset = int(config.get("layout_y_offset", 491))
    corner_radius = int(config.get("poster_corner_radius", 15))
    reflection_opacity = int(config.get("reflection_opacity", 50))
    shadow_blur = int(config.get("shadow_blur", 20))

    base_height = max(120, int(height * center_scale))
    base_width = max(80, int(base_height * float(config.get("poster_ratio", 0.6667))))
    pivot_x = width // 2
    pivot_y = height // 2 + fan_radius - base_height // 2 + y_offset
    angle_step = fan_spread / max(1, requested_count - 1)
    render_queue = []

    direction = -1 if config.get("transition_direction") == "left_to_right" else 1
    progress = max(0.0, min(1.0, float(progress)))

    normal_slots = list(range(-midpoint, midpoint + 1))
    incoming_slot = direction * (midpoint + 1)
    for slot in normal_slots + [incoming_slot]:
        visual_offset = slot - direction * progress
        angle = visual_offset * angle_step
        radians = math.radians(angle)
        scale = math.pow(shrink_ratio, abs(visual_offset))
        item_width = max(1, int(base_width * scale))
        item_height = max(1, int(base_height * scale))
        render_queue.append({
            "url": pairs[(active_index + direction * slot) % len(pairs)][0],
            "width": item_width,
            "height": item_height,
            "x": pivot_x + fan_radius * math.sin(radians),
            "y": pivot_y - fan_radius * math.cos(radians),
            "angle": angle,
            "distance": abs(visual_offset),
            "opacity": progress if slot == incoming_slot else 1.0,
        })

    render_queue.sort(key=lambda item: item["distance"], reverse=True)
    for item in render_queue:
        poster = ctx.download_img(item["url"])
        if poster is None:
            continue
        poster = add_rounded_corners(
            poster.resize((item["width"], item["height"]), Image.LANCZOS),
            corner_radius,
        )
        card = create_card_group(
            poster,
            reflection_opacity,
            shadow_blur if item["distance"] == 0 else max(4, shadow_blur // 2),
        )
        card = _with_opacity(card, item["opacity"])
        card = card.rotate(-item["angle"], resample=Image.BICUBIC, expand=True)
        paste_x = int(item["x"] - card.width / 2)
        paste_y = int(item["y"] - card.height / 2 + item["height"] / 2)
        canvas.alpha_composite(card, (paste_x, paste_y))
    return canvas


def _draw_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    title_font = fonts.get("font_title") or fonts.get("main")
    subtitle_font = fonts.get("font_subtitle") or fonts.get("sub") or title_font
    title = config.get("title", "")
    subtitle = config.get("subtitle", "")
    if title:
        ctx.draw_text_wrapper(
            draw,
            title,
            width // 2,
            height // 2 + int(config.get("title_offset_y", -400)),
            title_font,
            width,
            ctx.resolve_text_fill(config, "title", "#FFFFFF", config.get("title_opacity", 255)),
            align="center",
        )
    if subtitle:
        ctx.draw_text_wrapper(
            draw,
            subtitle,
            width // 2,
            height // 2 + int(config.get("subtitle_offset_y", -171)),
            subtitle_font,
            width,
            ctx.resolve_text_fill(config, "subtitle", "#AAAAAA", config.get("subtitle_opacity", 210)),
            align="center",
        )
    return canvas


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
                {"key": "transition_ms", "label": "顺滑过渡时长 (ms)", "type": "range", "min": 300, "max": 1400, "step": 50, "default": 900},
                {"key": "transition_direction", "label": "扇面轮换方向", "type": "select", "options": [
                    {"value": "right_to_left", "label": "从右向左"},
                    {"value": "left_to_right", "label": "从左向右"},
                ], "default": "right_to_left"},
                {"key": "anim_duration", "label": "动画帧间隔 (ms)", "type": "range", "min": 60, "max": 150, "step": 10, "default": 100},
            ],
        },
        {
            "group": "扇形布局与背景",
            "items": [
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.82},
                {"key": "bg_darkness", "label": "背景压暗程度", "type": "range", "min": 0, "max": 255, "default": 100},
                {"key": "bg_blur", "label": "背景模糊程度", "type": "range", "min": 0, "max": 80, "default": 0},
                {"key": "fan_radius", "label": "扇形半径 (曲率)", "type": "range", "min": 500, "max": 3000, "step": 50, "default": 850},
                {"key": "fan_spread", "label": "扇形展开总角度", "type": "range", "min": 10, "max": 90, "step": 1, "default": 90},
                {"key": "center_scale", "label": "C 位海报大小", "type": "range", "min": 0.3, "max": 0.8, "step": 0.01, "default": 0.5},
                {"key": "side_scale_shrink", "label": "侧边缩小比例", "type": "range", "min": 0.75, "max": 1.0, "step": 0.01, "default": 0.88},
                {"key": "layout_y_offset", "label": "海报整体垂直位置", "type": "range", "min": -500, "max": 500, "default": 491},
                {"key": "poster_ratio", "label": "海报宽高比", "type": "range", "min": 0.5, "max": 0.9, "step": 0.01, "default": 0.67},
            ],
        },
        {
            "group": "海报光影",
            "items": [
                {"key": "poster_corner_radius", "label": "海报圆角", "type": "range", "min": 0, "max": 50, "default": 15},
                {"key": "reflection_opacity", "label": "倒影强度", "type": "range", "min": 0, "max": 100, "default": 50},
                {"key": "shadow_blur", "label": "海报阴影模糊", "type": "range", "min": 0, "max": 40, "default": 20},
            ],
        },
        {
            "group": "字体与排版",
            "items": [
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 20, "max": 260, "default": 200},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "title_offset_y", "label": "主标题垂直偏移", "type": "range", "min": -600, "max": 600, "default": -400},
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 10, "max": 180, "default": 100},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#AAAAAA"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 210},
                {"key": "subtitle_offset_y", "label": "副标题垂直偏移", "type": "range", "min": -600, "max": 600, "default": -171},
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
    canvas = _state_background(ctx, bg, config, pairs[current])
    if blend > 0.0 and current != upcoming:
        upcoming_background = _state_background(ctx, bg, config, pairs[upcoming])
        canvas = Image.blend(canvas, upcoming_background, blend)
    canvas = _draw_fan(ctx, canvas, config, pairs, current, blend)
    return _draw_text(ctx, canvas, config, fonts)
