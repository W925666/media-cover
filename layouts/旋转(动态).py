import colorsys
import math
from collections import Counter

from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _rgb_to_hsv(color):
    return colorsys.rgb_to_hsv(*(channel / 255.0 for channel in color))


def _hsv_to_rgb(hue, saturation, value):
    return tuple(int(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value))


def _macaron(color):
    hue, saturation, value = _rgb_to_hsv(color)
    return _hsv_to_rgb(hue, max(0.3, min(0.7, saturation)), max(0.6, min(0.85, value)))


def _color_distance(first, second):
    first_h, first_s, first_v = _rgb_to_hsv(first)
    second_h, second_s, second_v = _rgb_to_hsv(second)
    return min(abs(first_h - second_h), 1 - abs(first_h - second_h)) * 5 + abs(first_s - second_s) + abs(first_v - second_v)


def _dominant_colors(image, count=3):
    sample = image.copy().convert("RGB")
    sample.thumbnail((150, 150))
    pixel_data = sample.get_flattened_data() if hasattr(sample, "get_flattened_data") else sample.getdata()
    pixels = [
        pixel for pixel in pixel_data
        if not (max(pixel) < 20 or min(pixel) > 235 or max(pixel) - min(pixel) < 10)
    ]
    candidates = Counter(pixels).most_common(count * 8)
    result = []
    for color, _ in candidates:
        adjusted = _macaron(color)
        if not any(_color_distance(adjusted, existing) < 0.15 for existing in result):
            result.append(adjusted)
            if len(result) >= count:
                break
    defaults = [(237, 159, 77), (186, 225, 255), (255, 223, 186)]
    while len(result) < count:
        result.append(defaults[len(result)])
    return result


def _darken(color, factor):
    return tuple(int(channel * factor) for channel in color)


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


def _crop_square(image):
    size = min(image.size)
    left = (image.width - size) // 2
    top = (image.height - size) // 2
    return image.crop((left, top, left + size, top + size))


def _add_film_grain(image, intensity):
    if intensity <= 0:
        return image.convert("RGBA")
    noise = Image.effect_noise(image.size, intensity * 510).convert("RGB")
    return Image.blend(image.convert("RGB"), noise, intensity * 0.5).convert("RGBA")


def _add_shadow_and_rotate(canvas, image, angle, config, center):
    width, height = image.size
    shadow_blur = int(config.get("shadow_blur", 20))
    padding = max(80, shadow_blur * 4)
    shadow = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
    opacity = max(0.0, min(1.0, float(config.get("shadow_opacity", 0.5))))
    shadow.paste((0, 0, 0, round(255 * opacity)), (padding, padding), image.getchannel("A"))
    if shadow_blur > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
    shadow = shadow.rotate(angle, Image.BICUBIC, expand=True)
    shadow_x = center[0] - shadow.width // 2 + int(config.get("shadow_offset_x", 15))
    shadow_y = center[1] - shadow.height // 2 + int(config.get("shadow_offset_y", 20))
    canvas.alpha_composite(shadow, (shadow_x, shadow_y))

    rotated = image.rotate(angle, Image.BICUBIC, expand=True)
    canvas.alpha_composite(rotated, (center[0] - rotated.width // 2, center[1] - rotated.height // 2))
    return canvas


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


def _state_background(ctx, fallback, config, pair, poster):
    backdrop = ctx.download_img(pair[1]) if pair and pair[1] else None
    if backdrop is None:
        background = fallback.copy().convert("RGBA")
    else:
        background = ImageOps.fit(
            backdrop.convert("RGBA"),
            fallback.size,
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )
    brightness = float(config.get("background_brightness", 0.82))
    if brightness != 1.0:
        background = ImageEnhance.Brightness(background).enhance(brightness)
    blur_radius = int(config.get("bg_blur", 0))
    if blur_radius > 0:
        background = background.filter(ImageFilter.GaussianBlur(blur_radius))

    colors = _dominant_colors(poster, 3)
    ratio = max(0.0, min(1.0, float(config.get("color_ratio", 0.35))))
    if ratio > 0:
        tint = Image.new("RGBA", background.size, (*_darken(colors[0], 0.72), 255))
        background = Image.blend(background, tint, ratio)
    return _add_film_grain(background, float(config.get("grain_intensity", 0.03)))


def _set_opacity(image, opacity):
    result = image.copy().convert("RGBA")
    opacity = max(0.0, min(1.0, float(opacity)))
    result.putalpha(result.getchannel("A").point(lambda value: round(value * opacity)))
    return result


def _scaled_card(image, scale):
    scale = max(0.1, float(scale))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def _layer_pose(depth, max_depth, config):
    top = float(config.get("angle_top", 0))
    middle = float(config.get("angle_mid", 12))
    bottom = float(config.get("angle_bot", 24))
    if depth <= 1.0:
        angle = top + (middle - top) * depth
    elif max_depth <= 1:
        angle = middle
    else:
        angle = middle + (bottom - middle) * ((depth - 1.0) / (max_depth - 1.0))
    scale = math.pow(float(config.get("layer_scale_step", 1.0)), depth)
    return angle, scale


def _draw_cards(ctx, canvas, config, posters, progress=0.0):
    width, height = canvas.size
    progress = max(0.0, min(1.0, float(progress)))
    card_size = max(120, int(height * float(config.get("card_scale", 0.65))))
    squares = [
        _crop_square(poster).resize((card_size, card_size), Image.LANCZOS).convert("RGBA")
        for poster in posters
    ]
    if not squares:
        return canvas

    radius = int(config.get("card_radius", 40))
    center = (
        int(width * float(config.get("card_x_percent", 68)) / 100),
        int(height * float(config.get("card_y_percent", 50)) / 100),
    )
    stage = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    max_depth = max(0, len(squares) - 1)
    offset_x = float(config.get("layer_offset_x", 0))
    offset_y = float(config.get("layer_offset_y", 0))

    def draw_depth(square, depth, opacity=1.0):
        angle, scale = _layer_pose(depth, max_depth, config)
        layer = _rounded(_scaled_card(square, scale), max(0, round(radius * scale)))
        layer = _set_opacity(layer, opacity)
        layer_center = (
            round(center[0] + offset_x * depth),
            round(center[1] + offset_y * depth),
        )
        _add_shadow_and_rotate(stage, layer, angle, config, layer_center)

    if progress <= 0.0 or len(squares) == 1:
        for depth in range(len(squares) - 1, -1, -1):
            draw_depth(squares[depth], depth)
        return Image.alpha_composite(canvas, stage)

    spin_degrees = float(config.get("transition_spin", 38))
    spin_sign = 1 if config.get("spin_direction", "clockwise") == "clockwise" else -1
    travel = card_size * float(config.get("transition_travel", 0.1))

    draw_depth(squares[0], max_depth, progress)
    for index in range(len(squares) - 1, 1, -1):
        draw_depth(squares[index], index - progress)
    draw_depth(squares[1], 1.0 - progress)

    outgoing = _set_opacity(
        _scaled_card(_rounded(squares[0], radius), 1.0 - 0.1 * progress),
        1.0 - progress,
    )
    current_center = (
        round(center[0] - spin_sign * travel * progress),
        round(center[1] + travel * 0.35 * progress),
    )
    _add_shadow_and_rotate(
        stage,
        outgoing,
        float(config.get("angle_top", 0)) - spin_sign * spin_degrees * progress,
        config,
        current_center,
    )
    return Image.alpha_composite(canvas, stage)


def _draw_text(ctx, canvas, config, fonts):
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    title_font = fonts.get("font_title") or fonts.get("main")
    subtitle_font = fonts.get("font_subtitle") or fonts.get("sub") or title_font
    title = config.get("title", "")
    subtitle = config.get("subtitle", "")
    shadow_opacity = int(config.get("text_shadow_opacity", 100))
    if title:
        x = int(width * float(config.get("title_pos_x", 6)) / 100)
        y = int(height * float(config.get("title_pos_y", 38)) / 100)
        max_width = int(width * float(config.get("title_width_pct", 40)) / 100)
        if shadow_opacity > 0:
            ctx.draw_text_wrapper(draw, title, x + 3, y + 3, title_font, max_width, (0, 0, 0, shadow_opacity))
        ctx.draw_text_wrapper(
            draw,
            title,
            x,
            y,
            title_font,
            max_width,
            ctx.resolve_text_fill(config, "title", "#FFFFFF", config.get("title_opacity", 255)),
        )
    if subtitle:
        x = int(width * float(config.get("subtitle_pos_x", 6)) / 100)
        y = int(height * float(config.get("subtitle_pos_y", 55)) / 100)
        max_width = int(width * float(config.get("sub_width_pct", 40)) / 100)
        if shadow_opacity > 0:
            ctx.draw_text_wrapper(draw, subtitle, x + 2, y + 2, subtitle_font, max_width, (0, 0, 0, shadow_opacity))
        ctx.draw_text_wrapper(
            draw,
            subtitle,
            x,
            y,
            subtitle_font,
            max_width,
            ctx.resolve_text_fill(config, "subtitle", "#DDDDDD", config.get("subtitle_opacity", 210)),
        )
    return canvas


def get_schema():
    return [
        {
            "group": "资源与内容",
            "items": [
                {"key": "poster_count", "label": "旋转堆叠海报数量", "type": "range", "min": 2, "max": 12, "default": 3},
                {"key": "title", "label": "主标题内容", "type": "text", "default": ""},
                {"key": "subtitle", "label": "副标题内容", "type": "text", "default": ""},
            ],
        },
        {
            "group": "动画设置",
            "items": [
                {"key": "enable_animation", "label": "开启动态轮播", "type": "boolean", "default": True},
                {"key": "switch_interval", "label": "顶层卡片轮换间隔 (ms)", "type": "range", "min": 1000, "max": 5000, "step": 100, "default": 2500},
                {"key": "transition_ms", "label": "顺滑过渡时长 (ms)", "type": "range", "min": 300, "max": 1400, "step": 50, "default": 900},
                {"key": "spin_direction", "label": "旋转方向", "type": "select", "options": [
                    {"value": "clockwise", "label": "顺时针"},
                    {"value": "counterclockwise", "label": "逆时针"},
                ], "default": "clockwise"},
                {"key": "transition_spin", "label": "替换旋转角度", "type": "range", "min": 10, "max": 90, "step": 2, "default": 38},
                {"key": "anim_duration", "label": "动画帧间隔 (ms)", "type": "range", "min": 60, "max": 150, "step": 10, "default": 100},
            ],
        },
        {
            "group": "卡片布局",
            "items": [
                {"key": "card_scale", "label": "卡片尺寸缩放", "type": "range", "min": 0.3, "max": 0.9, "step": 0.05, "default": 0.65},
                {"key": "card_x_percent", "label": "卡片中心 X%", "type": "range", "min": 0, "max": 100, "default": 68},
                {"key": "card_y_percent", "label": "卡片中心 Y%", "type": "range", "min": 0, "max": 100, "default": 50},
                {"key": "card_radius", "label": "卡片圆角大小", "type": "range", "min": 0, "max": 100, "default": 40},
                {"key": "angle_top", "label": "顶层旋转", "type": "range", "min": -180, "max": 180, "default": 0},
                {"key": "angle_mid", "label": "中层旋转", "type": "range", "min": -180, "max": 180, "default": 12},
                {"key": "angle_bot", "label": "底层旋转", "type": "range", "min": -180, "max": 180, "default": 24},
                {"key": "layer_scale_step", "label": "逐层缩放", "type": "range", "min": 0.92, "max": 1.0, "step": 0.01, "default": 1.0},
                {"key": "layer_offset_x", "label": "逐层水平错位", "type": "range", "min": -30, "max": 30, "default": 0},
                {"key": "layer_offset_y", "label": "逐层垂直错位", "type": "range", "min": -30, "max": 30, "default": 0},
            ],
        },
        {
            "group": "卡片光影",
            "items": [
                {"key": "shadow_opacity", "label": "阴影浓度", "type": "range", "min": 0.0, "max": 1.0, "step": 0.1, "default": 0.5},
                {"key": "shadow_blur", "label": "阴影模糊", "type": "range", "min": 0, "max": 100, "default": 20},
                {"key": "shadow_offset_x", "label": "阴影偏移 X", "type": "range", "min": -50, "max": 50, "default": 15},
                {"key": "shadow_offset_y", "label": "阴影偏移 Y", "type": "range", "min": -50, "max": 50, "default": 20},
            ],
        },
        {
            "group": "背景与装饰",
            "items": [
                {"key": "background_brightness", "label": "背景亮度", "type": "range", "min": 0.3, "max": 1.2, "step": 0.05, "default": 0.82},
                {"key": "bg_blur", "label": "背景模糊", "type": "range", "min": 0, "max": 80, "default": 0},
                {"key": "color_ratio", "label": "背景染色强度", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.35},
                {"key": "grain_intensity", "label": "背景噪点", "type": "range", "min": 0.0, "max": 0.2, "step": 0.01, "default": 0.03},
                {"key": "text_shadow_opacity", "label": "文字阴影浓度", "type": "range", "min": 0, "max": 255, "default": 100},
            ],
        },
        {
            "group": "主标题样式",
            "items": [
                {"key": "font_title", "label": "主标题字体", "type": "font", "default": "default.ttf"},
                {"key": "title_size", "label": "主标题字号", "type": "range", "min": 50, "max": 300, "default": 140},
                {"key": "title_color", "label": "主标题颜色", "type": "color", "default": "#FFFFFF"},
                {"key": "title_opacity", "label": "主标题不透明度", "type": "range", "min": 0, "max": 255, "default": 255},
                {"key": "title_pos_x", "label": "主标题 X%", "type": "range", "min": 0, "max": 100, "default": 6},
                {"key": "title_pos_y", "label": "主标题 Y%", "type": "range", "min": 0, "max": 100, "default": 38},
                {"key": "title_width_pct", "label": "主标题最大宽度 %", "type": "range", "min": 10, "max": 100, "default": 40},
            ],
        },
        {
            "group": "副标题样式",
            "items": [
                {"key": "font_subtitle", "label": "副标题字体", "type": "font", "default": "default.ttf"},
                {"key": "subtitle_size", "label": "副标题字号", "type": "range", "min": 20, "max": 150, "default": 60},
                {"key": "subtitle_color", "label": "副标题颜色", "type": "color", "default": "#DDDDDD"},
                {"key": "subtitle_opacity", "label": "副标题不透明度", "type": "range", "min": 0, "max": 255, "default": 210},
                {"key": "subtitle_pos_x", "label": "副标题 X%", "type": "range", "min": 0, "max": 100, "default": 6},
                {"key": "subtitle_pos_y", "label": "副标题 Y%", "type": "range", "min": 0, "max": 100, "default": 55},
                {"key": "sub_width_pct", "label": "副标题最大宽度 %", "type": "range", "min": 10, "max": 100, "default": 40},
            ],
        },
    ]


def render(ctx, bg, config, assets, fonts, step=0.0):
    pairs = _poster_pairs(assets, max(2, int(config.get("poster_count", 3))))
    if not pairs:
        return _draw_text(ctx, bg.copy().convert("RGBA"), config, fonts)
    switch_interval = max(500, int(config.get("switch_interval", 2500)))
    transition_ms = max(100, min(switch_interval - 100, int(config.get("transition_ms", 500))))
    current, upcoming, blend = _timeline_state(step, len(pairs), transition_ms / switch_interval)
    ordered_posters = []
    for offset in range(len(pairs)):
        poster = ctx.download_img(pairs[(current + offset) % len(pairs)][0])
        if poster is not None:
            ordered_posters.append(poster.convert("RGBA"))
    current_poster = ordered_posters[0] if ordered_posters else bg.copy().convert("RGBA")
    upcoming_poster = ordered_posters[1] if len(ordered_posters) > 1 else current_poster

    canvas = _state_background(ctx, bg, config, pairs[current], current_poster)
    if blend > 0.0 and current != upcoming:
        background_poster = upcoming_poster if upcoming_poster is not None else current_poster
        upcoming_background = _state_background(ctx, bg, config, pairs[upcoming], background_poster)
        canvas = Image.blend(canvas, upcoming_background, blend)
    canvas = _draw_cards(ctx, canvas, config, ordered_posters or [current_poster], blend)
    return _draw_text(ctx, canvas, config, fonts)
