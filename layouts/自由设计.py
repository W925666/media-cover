import os

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps


CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080


def get_schema():
    return [
        {
            "group": "自由画布",
            "items": [
                {"key": "canvas_bg_color", "label": "画布背景", "type": "color", "default": "#101820"},
            ],
        }
    ]


def _number(value, default, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _rgba(value, opacity=100):
    try:
        rgb = ImageColor.getrgb(value or "#000000")
    except (TypeError, ValueError):
        rgb = (0, 0, 0)
    alpha = round(255 * _number(opacity, 100, 0, 100) / 100)
    return (*rgb[:3], alpha)


def _gradient_color(colors, progress):
    progress = max(0.0, min(1.0, progress))
    segment = min(1, int(progress * 2))
    local = progress * 2 - segment
    start = colors[segment]
    end = colors[segment + 1]
    return tuple(round(start[index] + (end[index] - start[index]) * local) for index in range(4))


def _gradient_image(size, element, opacity):
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    solid = element.get("color", "#ffffff")
    colors = [
        _rgba(element.get("gradientStart", solid), opacity),
        _rgba(element.get("gradientMiddle", solid), opacity),
        _rgba(element.get("gradientEnd", solid), opacity),
    ]
    direction = element.get("gradientDirection", "horizontal")
    image = Image.new("RGBA", (width, height), colors[0])
    gradient_draw = ImageDraw.Draw(image)
    if direction == "vertical":
        span = max(1, height - 1)
        for y in range(height):
            gradient_draw.line((0, y, width, y), fill=_gradient_color(colors, y / span))
    elif direction in ("diagonal_down", "diagonal_up"):
        span = max(1, width + height - 2)
        for offset in range(width + height - 1):
            color = _gradient_color(colors, offset / span)
            x1 = max(0, offset - (height - 1))
            x2 = min(width - 1, offset)
            if direction == "diagonal_down":
                gradient_draw.line((x1, offset - x1, x2, offset - x2), fill=color)
            else:
                gradient_draw.line((x1, height - 1 - (offset - x1), x2, height - 1 - (offset - x2)), fill=color)
    else:
        span = max(1, width - 1)
        for x in range(width):
            gradient_draw.line((x, 0, x, height), fill=_gradient_color(colors, x / span))
    return image


def _glyph_signature(font, char):
    mask = font.getmask(char, mode="L")
    return mask.size, bytes(mask)


def _font_supports_text(font, text):
    characters = {char for char in str(text or "") if ord(char) > 127 and not char.isspace()}
    if not characters:
        return True
    try:
        missing = _glyph_signature(font, chr(0x10FFFF))
        return all(_glyph_signature(font, char) != missing for char in characters)
    except (OSError, ValueError):
        return False


def _available_fonts(ctx):
    try:
        names = [name for name in os.listdir(ctx.fonts_dir) if name.lower().endswith((".ttf", ".otf"))]
    except OSError:
        return []
    preferred = ["OPPOSans-B.ttf", "优设标题黑.ttf", "仓耳舒圆体W02.ttf", "OPPOSans-M.ttf", "OPPOSans-R.ttf"]
    rank = {name: index for index, name in enumerate(preferred)}
    return sorted(names, key=lambda name: (rank.get(name, len(preferred)), name.lower()))


def _load_font(ctx, filename, size, text=""):
    size = int(_number(size, 72, 8, 500))
    if filename:
        path = os.path.join(ctx.fonts_dir, filename)
        try:
            selected = ImageFont.truetype(path, size)
            if _font_supports_text(selected, text):
                return selected
        except OSError:
            pass
    for name in _available_fonts(ctx):
        if name == filename:
            continue
        try:
            fallback = ImageFont.truetype(os.path.join(ctx.fonts_dir, name), size)
            if _font_supports_text(fallback, text):
                return fallback
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_width):
    if not text:
        return []
    lines = []
    current = ""
    for char in str(text):
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        try:
            width = draw.textlength(candidate, font=font)
        except AttributeError:
            width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return lines


def _rounded_image(source, width, height, radius, opacity, fit="cover"):
    source = source.convert("RGBA")
    if fit == "contain":
        fitted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        contained = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS)
        fitted.alpha_composite(contained, ((width - contained.width) // 2, (height - contained.height) // 2))
    else:
        fitted = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    if opacity < 100:
        mask = mask.point(lambda alpha: round(alpha * opacity / 100))
    fitted.putalpha(mask)
    return fitted


def _element_image(ctx, element, assets, config):
    element_type = element.get("type")
    max_width = CANVAS_WIDTH * 2 if element_type == "poster" else CANVAS_WIDTH
    max_height = CANVAS_HEIGHT * 2 if element_type == "poster" else CANVAS_HEIGHT
    width = int(_number(element.get("w"), 480, 20, max_width))
    height = int(_number(element.get("h"), 720, 20, max_height))
    radius = int(_number(element.get("radius"), 0, 0, min(width, height) / 2))
    opacity = _number(element.get("opacity"), 100, 0, 100)
    if element_type in ("backdrop", "poster"):
        source = None
        if element_type == "backdrop":
            source = ctx.download_img(assets.get("designer_backdrop_url") or assets.get("bg_url"))
        else:
            posters = assets.get("posters") or []
            if posters:
                index = int(_number(element.get("assetIndex"), 0, 0)) % len(posters)
                source = ctx.download_img(posters[index])
        if source is None:
            source = Image.new("RGBA", (width, height), _rgba(element.get("fill", "#22313f"), opacity))
        fit = "cover" if element_type == "poster" else element.get("fit", "cover")
        layer = _rounded_image(source, width, height, radius, opacity, fit)
        border_width = int(_number(element.get("borderWidth"), 0, 0, 80))
        if border_width:
            ImageDraw.Draw(layer).rounded_rectangle(
                (border_width // 2, border_width // 2, width - 1 - border_width // 2, height - 1 - border_width // 2),
                radius=radius,
                outline=_rgba(element.get("borderColor", "#ffffff"), opacity),
                width=border_width,
            )
        return layer

    if element_type == "shape":
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        border_width = int(_number(element.get("borderWidth"), 0, 0, 80))
        ImageDraw.Draw(layer).rounded_rectangle(
            (border_width // 2, border_width // 2, width - 1 - border_width // 2, height - 1 - border_width // 2),
            radius=radius,
            fill=_rgba(element.get("fill", "#48ccff"), opacity),
            outline=_rgba(element.get("borderColor", "#ffffff"), opacity) if border_width else None,
            width=border_width,
        )
        return layer

    if element_type == "text":
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        source = element.get("textSource", "custom")
        if source == "title":
            text = config.get("title", "媒体库标题")
        elif source == "subtitle":
            text = config.get("subtitle", "")
        elif source == "count":
            text = str(assets.get("count", 0))
        else:
            text = element.get("text", "自定义文字")
        font = _load_font(ctx, element.get("font"), element.get("fontSize", 96), text)
        lines = _wrap_text(draw, text, font, max(10, width - 8))
        line_height = int(_number(element.get("lineHeight"), 1.15, 0.6, 3) * int(_number(element.get("fontSize"), 96, 8, 500)))
        total_height = max(line_height, len(lines) * line_height)
        vertical = element.get("verticalAlign", "center")
        if vertical == "bottom":
            y = max(0, height - total_height)
        elif vertical == "top":
            y = 0
        else:
            y = max(0, (height - total_height) / 2)
        align = element.get("align", "left")
        fill = _rgba(element.get("color", "#ffffff"), opacity)
        stroke_width = int(_number(element.get("strokeWidth"), 0, 0, 30))
        stroke_fill = _rgba(element.get("strokeColor", "#000000"), opacity)
        text_positions = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            text_width = bbox[2] - bbox[0]
            if align == "center":
                x = (width - text_width) / 2
            elif align == "right":
                x = width - text_width - 2
            else:
                x = 2
            text_positions.append((x, y, line))
            y += line_height
        if element.get("colorMode", "solid") == "gradient":
            mask = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask)
            for x, y, line in text_positions:
                if stroke_width:
                    draw.text((x, y), line, font=font, fill=stroke_fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
                mask_draw.text((x, y), line, font=font, fill=255)
            gradient = _gradient_image((width, height), element, opacity)
            gradient.putalpha(ImageChops.multiply(gradient.getchannel("A"), mask))
            layer.alpha_composite(gradient)
        else:
            for x, y, line in text_positions:
                draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        return layer

    return None


def render(ctx, bg, config, assets, fonts):
    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), _rgba(config.get("canvas_bg_color", "#101820")))
    elements = config.get("designer_elements") or []
    render_assets = dict(assets or {})
    posters = render_assets.get("posters") or []
    poster_backdrops = render_assets.get("poster_backdrops") or []
    if posters:
        for element in reversed(elements):
            if not isinstance(element, dict) or element.get("type") != "poster" or element.get("hidden"):
                continue
            index = int(_number(element.get("assetIndex"), 0, 0)) % len(posters)
            if index < len(poster_backdrops) and poster_backdrops[index]:
                render_assets["designer_backdrop_url"] = poster_backdrops[index]
                break
    for element in elements:
        if not isinstance(element, dict) or element.get("hidden"):
            continue
        layer = _element_image(ctx, element, render_assets, config)
        if layer is None:
            continue
        angle = _number(element.get("rotation"), 0, -360, 360)
        if angle:
            layer = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
        center_x = _number(element.get("x"), 0) + _number(element.get("w"), layer.width) / 2
        center_y = _number(element.get("y"), 0) + _number(element.get("h"), layer.height) / 2
        paste_x = round(center_x - layer.width / 2)
        paste_y = round(center_y - layer.height / 2)
        canvas.alpha_composite(layer, (paste_x, paste_y))
    return canvas
