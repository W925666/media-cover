from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageColor, ImageChops
from io import BytesIO
import base64
import logging
import os
import re
import importlib.util
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("cover_factory.engine")

class PosterEngine:
    # 类变量：全局共享布局模块，只加载一次
    _shared_layouts = None
    _layouts_loaded = False

    # 注意：layouts_dir 默认指向根目录下的 layouts 文件夹
    def __init__(self, fonts_dir="fonts", layouts_dir="layouts"):
        self.fonts_dir = fonts_dir
        self.layouts_dir = layouts_dir
        self.default_font_path = os.path.join(fonts_dir, "OPPOSans-B.ttf")
        self._load_layouts()
        self._img_cache = {}
        self._img_cache_max = 50
        self._lock = threading.Lock()

    def _load_layouts(self):
        # 如果已经加载过，直接复用缓存
        if PosterEngine._layouts_loaded:
            self.layout_modules = PosterEngine._shared_layouts
            return

        logger.info(f"[Engine] 正在加载布局模块: {self.layouts_dir}...")
        if not os.path.exists(self.layouts_dir):
            try:
                os.makedirs(self.layouts_dir)
            except: pass

        self.layout_modules = {}
        if os.path.exists(self.layouts_dir):
            for filename in os.listdir(self.layouts_dir):
                if filename.endswith(".py") and filename != "__init__.py":
                    module_name = filename[:-3]
                    file_path = os.path.join(self.layouts_dir, filename)
                    try:
                        spec = importlib.util.spec_from_file_location(module_name, file_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        if hasattr(module, 'render'):
                            self.layout_modules[module_name] = module.render
                            logger.info(f"    ✅ 已加载布局: {module_name}")
                        else:
                            logger.warning(f"    ⚠️ 跳过 {filename}: 未找到 render 函数")
                    except Exception as e:
                        logger.error(f"    ❌ 加载失败 {filename}: {e}")

        # 缓存到类变量，后续实例直接复用
        PosterEngine._shared_layouts = self.layout_modules
        PosterEngine._layouts_loaded = True

    # === 工具函数 (增加 proxies) ===

    def download_img(self, url):
        if not url: return None
        with self._lock:
            if url in self._img_cache:
                return self._img_cache[url].copy()

        try:
            img = None
            if url.startswith("data:image"):
                base64_data = re.sub('^data:image/.+;base64,', '', url)
                image_data = base64.b64decode(base64_data)
                img = Image.open(BytesIO(image_data)).convert("RGBA")
            elif os.path.isfile(url):
                # 本地文件路径（已由宿主 ctx.download 落到工作目录）
                img = Image.open(url).convert("RGBA")
            
            if img:
                with self._lock:
                    if len(self._img_cache) >= self._img_cache_max:
                        # 删除最早的一半缓存
                        keys = list(self._img_cache.keys())
                        for k in keys[:len(keys) // 2]:
                            del self._img_cache[k]
                    self._img_cache[url] = img.copy()
                return img
            return None
        except Exception as e:
            return None

    @staticmethod
    def _glyph_signature(font, char):
        mask = font.getmask(char, mode="L")
        return mask.size, bytes(mask)

    @classmethod
    def _font_supports_text(cls, font, text):
        characters = {char for char in str(text or "") if ord(char) > 127 and not char.isspace()}
        if not characters:
            return True
        try:
            missing = cls._glyph_signature(font, chr(0x10FFFF))
            return all(cls._glyph_signature(font, char) != missing for char in characters)
        except (OSError, ValueError):
            return False

    def _available_font_names(self):
        try:
            names = [
                name for name in os.listdir(self.fonts_dir)
                if name.lower().endswith((".ttf", ".otf"))
            ]
        except OSError:
            return []
        preferred = [
            "OPPOSans-B.ttf",
            "优设标题黑.ttf",
            "仓耳舒圆体W02.ttf",
            "OPPOSans-M.ttf",
            "OPPOSans-R.ttf",
        ]
        rank = {name: index for index, name in enumerate(preferred)}
        return sorted(names, key=lambda name: (rank.get(name, len(preferred)), name.lower()))

    def _load_text_font(self, font_filename, size, text=""):
        size = max(8, min(500, int(size)))
        if font_filename:
            path = os.path.join(self.fonts_dir, font_filename)
            try:
                selected = ImageFont.truetype(path, size)
                if self._font_supports_text(selected, text):
                    return selected
            except OSError:
                pass

        for name in self._available_font_names():
            if name == font_filename:
                continue
            try:
                fallback = ImageFont.truetype(os.path.join(self.fonts_dir, name), size)
                if self._font_supports_text(fallback, text):
                    return fallback
            except OSError:
                continue
        return ImageFont.load_default()

    def draw_text_wrapper(self, draw, text, x, y, font, max_width, fill, line_spacing=10, align='left'):
        if not text: return y
        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            try: w = font.getlength(test_line)
            except: w = font.getbbox(test_line)[2]
            if w <= max_width: current_line = test_line
            else:
                if current_line: lines.append(current_line)
                current_line = char
        if current_line: lines.append(current_line)

        try: font_height = font.getmetrics()[0] + font.getmetrics()[1] + line_spacing
        except: bbox = font.getbbox("Hg"); font_height = (bbox[3] - bbox[1]) + line_spacing
        
        current_y = y
        for line in lines:
            try: line_w = font.getlength(line)
            except: line_w = font.getbbox(line)[2]
            draw_x = x
            if align == 'center': draw_x = x - (line_w / 2)
            elif align == 'right': draw_x = x - line_w
            target = getattr(draw, '_image', None)
            if isinstance(fill, dict) and target is not None:
                self._draw_text_paint(target, (draw_x, current_y), line, font, fill)
            else:
                draw.text((draw_x, current_y), line, font=font, fill=fill)
            current_y += font_height
        return current_y

    @staticmethod
    def _rgba(value, opacity=255, fallback='#ffffff'):
        try:
            color = ImageColor.getrgb(str(value or fallback))
            return (color[0], color[1], color[2], max(0, min(255, int(opacity))))
        except (TypeError, ValueError):
            color = ImageColor.getrgb(fallback)
            return (color[0], color[1], color[2], max(0, min(255, int(opacity))))

    def resolve_text_fill(self, config, prefix, fallback='#ffffff', opacity=255):
        solid = config.get(f'{prefix}_color', fallback)
        if config.get(f'{prefix}_color_mode', 'solid') != 'gradient':
            return self._rgba(solid, opacity, fallback)
        return {
            'colors': [
                config.get(f'{prefix}_gradient_start', solid),
                config.get(f'{prefix}_gradient_middle', solid),
                config.get(f'{prefix}_gradient_end', solid),
            ],
            'direction': config.get(f'{prefix}_gradient_direction', 'horizontal'),
            'opacity': max(0, min(255, int(opacity))),
        }

    @staticmethod
    def _gradient_color(colors, progress):
        progress = max(0.0, min(1.0, progress))
        segment = min(1, int(progress * 2))
        local = progress * 2 - segment
        start = colors[segment]
        end = colors[segment + 1]
        return tuple(round(start[i] + (end[i] - start[i]) * local) for i in range(4))

    def _make_gradient(self, size, paint):
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        opacity = max(0, min(255, int(paint.get('opacity', 255))))
        colors = [self._rgba(value, opacity) for value in paint.get('colors', ['#ffffff'] * 3)]
        while len(colors) < 3:
            colors.append(colors[-1])
        direction = paint.get('direction', 'horizontal')
        image = Image.new('RGBA', (width, height), colors[0])
        gradient_draw = ImageDraw.Draw(image)

        if direction == 'vertical':
            span = max(1, height - 1)
            for y in range(height):
                gradient_draw.line((0, y, width, y), fill=self._gradient_color(colors, y / span))
        elif direction in ('diagonal_down', 'diagonal_up'):
            span = max(1, width + height - 2)
            for offset in range(width + height - 1):
                color = self._gradient_color(colors, offset / span)
                x1 = max(0, offset - (height - 1))
                x2 = min(width - 1, offset)
                if direction == 'diagonal_down':
                    gradient_draw.line((x1, offset - x1, x2, offset - x2), fill=color)
                else:
                    gradient_draw.line((x1, height - 1 - (offset - x1), x2, height - 1 - (offset - x2)), fill=color)
        else:
            span = max(1, width - 1)
            for x in range(width):
                gradient_draw.line((x, 0, x, height), fill=self._gradient_color(colors, x / span))
        return image

    def _paint_mask(self, target, mask, paint):
        if isinstance(paint, dict):
            layer = self._make_gradient(target.size, paint)
        else:
            layer = Image.new('RGBA', target.size, paint)
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
        target.alpha_composite(layer)

    @staticmethod
    def _paint_opacity(paint, opacity):
        opacity = max(0, min(255, int(opacity)))
        if isinstance(paint, dict):
            adjusted = dict(paint)
            adjusted['opacity'] = opacity
            return adjusted
        return (paint[0], paint[1], paint[2], opacity)

    def _draw_text_paint(self, target, position, text, font, paint):
        if not isinstance(paint, dict):
            ImageDraw.Draw(target).text(position, text, font=font, fill=paint)
            return
        measure = ImageDraw.Draw(target)
        bbox = measure.textbbox(position, text, font=font)
        left, top = math.floor(bbox[0]), math.floor(bbox[1])
        right, bottom = math.ceil(bbox[2]), math.ceil(bbox[3])
        if right <= left or bottom <= top:
            return
        mask = Image.new('L', (right - left, bottom - top), 0)
        ImageDraw.Draw(mask).text((position[0] - left, position[1] - top), text, font=font, fill=255)
        layer = self._make_gradient(mask.size, paint)
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
        target.alpha_composite(layer, (left, top))

    def create_smart_mask(self, width, height, opacity, coverage_percent, direction='horizontal'):
        mask = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(mask)
        if direction == 'horizontal':
            end_x = int(width * (coverage_percent / 100))
            if end_x <= 0: return mask
            for x in range(width):
                if x <= end_x:
                    progress = x / end_x
                    alpha = int(opacity * (1 - progress))
                    draw.line([(x, 0), (x, height)], fill=alpha)
                else: draw.line([(x, 0), (x, height)], fill=0)
        else:
            start_y = int(height * (1 - coverage_percent / 100))
            if start_y >= height: return mask
            for y in range(height):
                if y >= start_y:
                    progress = (y - start_y) / (height - start_y)
                    alpha = int(opacity * progress)
                    draw.line([(0, y), (width, y)], fill=alpha)
                else: draw.line([(0, y), (width, y)], fill=0)
        return mask

    def _draw_badge(self, img, config, count, fonts):
        style = config.get('badge_style', 'none')
        if style == 'none' or not count: return img
        count_str = str(count)
        scale = 4
        w, h = img.size
        overlay_w, overlay_h = w * scale, h * scale
        overlay = Image.new('RGBA', (overlay_w, overlay_h), (0,0,0,0))
        safe_margin = 8 * scale
        try: badge_x_percent = max(0.0, min(100.0, float(config.get('badge_x_percent', 2))))
        except (TypeError, ValueError): badge_x_percent = 2.0
        try: badge_y_percent = max(0.0, min(100.0, float(config.get('badge_y_percent', 3))))
        except (TypeError, ValueError): badge_y_percent = 3.0
        badge_font_file = config.get('badge_font', 'default.ttf')
        base_size = 40 if style == 'box' else 50
        user_size = int(config.get('badge_size', base_size))
        scaled_font = None
        if badge_font_file:
            path = os.path.join(self.fonts_dir, badge_font_file)
            if os.path.exists(path):
                try: scaled_font = ImageFont.truetype(path, user_size * scale)
                except: pass
        if not scaled_font:
            try:
                available_fonts = [f for f in os.listdir(self.fonts_dir) if f.lower().endswith(('.ttf', '.otf'))]
                if available_fonts:
                    fallback_path = os.path.join(self.fonts_dir, available_fonts[0])
                    scaled_font = ImageFont.truetype(fallback_path, user_size * scale)
            except: pass
        if not scaled_font: scaled_font = ImageFont.load_default()

        text_color = config.get('badge_text_color', '#ffffff')
        if style == 'ribbon' and 'badge_text_color' not in config: text_color = '#ffffff'
        bg_hex = config.get('badge_bg_color', '#000000')
        if style == 'ribbon' and 'badge_bg_color' not in config: bg_hex = '#b91c1c'
        elif style == 'box' and 'badge_bg_color' not in config: bg_hex = '#0f172a'
        opacity = max(0, min(255, int(config.get('badge_opacity', 255))))
        border_alpha = max(0, min(255, int(config.get('badge_border_opacity', 40))))
        try: effect_strength = max(0, min(100, int(config.get('badge_effect_strength', 70))))
        except (TypeError, ValueError): effect_strength = 70
        effect_ratio = effect_strength / 100
        border_rgba = (255, 255, 255, border_alpha)

        text_fill = self.resolve_text_fill(config, 'badge_text', text_color)
        bg_fill = self.resolve_text_fill(config, 'badge_bg', bg_hex, opacity)
        accent_fill = self._paint_opacity(bg_fill, max(80, border_alpha))
        glow_fill = self._paint_opacity(bg_fill, max(70, min(180, border_alpha + 45)))

        measure = ImageDraw.Draw(overlay)
        bbox = measure.textbbox((0, 0), count_str, font=scaled_font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

        def paint_mask(target, callback, paint=bg_fill, blur=0):
            mask = Image.new('L', target.size, 0)
            callback(ImageDraw.Draw(mask))
            if blur:
                self._paint_mask(target, mask.filter(ImageFilter.GaussianBlur(blur)), paint)
            else:
                self._paint_mask(target, mask, paint)
            return mask

        def place_content(content):
            content_box = content.getbbox()
            if not content_box:
                return
            content = content.crop(content_box)
            positioned = Image.new('RGBA', (overlay_w, overlay_h), (0, 0, 0, 0))
            available_w = max(0, overlay_w - content.width - safe_margin * 2)
            available_h = max(0, overlay_h - content.height - safe_margin * 2)
            position_x = safe_margin + int(available_w * badge_x_percent / 100)
            position_y = safe_margin + int(available_h * badge_y_percent / 100)
            positioned.alpha_composite(content, (position_x, position_y))
            overlay.alpha_composite(positioned)

        if style == 'ribbon':
            padding_v = 40 * scale
            ribbon_w = text_h + padding_v
            axis_span = ribbon_w * 1.414
            gap_size = int(user_size * 1.0 * scale)
            start, end = gap_size, gap_size + axis_span
            points = [(start, 0), (end, 0), (0, end), (0, start)]
            layer_size = int(max(text_w, axis_span) * 2.5)
            content = Image.new('RGBA', (layer_size, layer_size), (0, 0, 0, 0))
            paint_mask(content, lambda d: d.polygon(points, fill=255))
            txt_layer = Image.new('RGBA', (layer_size, layer_size), (0, 0, 0, 0))
            center = layer_size / 2
            draw_x = center - (text_w / 2) - bbox[0]
            draw_y = center - (text_h / 2) - bbox[1]
            self._draw_text_paint(txt_layer, (draw_x, draw_y), count_str, scaled_font, text_fill)
            rotated_txt = txt_layer.rotate(45, resample=Image.BICUBIC)
            ribbon_center = (start + end) / 4
            paste_x = int(ribbon_center - layer_size / 2)
            paste_y = int(ribbon_center - layer_size / 2)
            content.alpha_composite(rotated_txt, (paste_x, paste_y))
            place_content(content)

        elif style == 'box':
            padding_x = int(user_size * 0.6 * scale)
            padding_y = int(user_size * 0.3 * scale)
            box_w = text_w + padding_x * 2
            box_h = text_h + padding_y * 2
            if box_w < box_h: box_w = box_h
            content = Image.new('RGBA', (box_w + 4 * scale, box_h + 4 * scale), (0, 0, 0, 0))
            radius = box_h / 2
            rect = (2 * scale, 2 * scale, box_w + 2 * scale - 1, box_h + 2 * scale - 1)
            paint_mask(content, lambda d: d.rounded_rectangle(rect, radius=radius, fill=255))
            ImageDraw.Draw(content).rounded_rectangle(rect, radius=radius, outline=border_rgba, width=2 * scale)
            txt_x = 2 * scale + (box_w - text_w) / 2 - bbox[0]
            txt_y = 2 * scale + (box_h - text_h) / 2 - bbox[1]
            self._draw_text_paint(content, (txt_x, txt_y), count_str, scaled_font, text_fill)
            place_content(content)

        elif style in ('circle', 'outline', 'hexagon', 'ticket', 'stamp', 'glitch', 'wave', 'burst', 'tape', 'split'):
            custom_text_drawn = False
            if style == 'circle':
                diameter = max(text_w, text_h) + int(user_size * 1.25 * scale)
                content_w = int(diameter * 1.4)
                content = Image.new('RGBA', (content_w, diameter), (0, 0, 0, 0))
                cx, cy = content_w // 2, diameter // 2
                ring = (cx - diameter // 2 + 5 * scale, 5 * scale, cx + diameter // 2 - 5 * scale, diameter - 5 * scale)

                def orbit_path(d):
                    d.ellipse(ring, outline=255, width=3 * scale)
                    inset = 10 * scale
                    d.arc((ring[0] + inset, ring[1] + inset, ring[2] - inset, ring[3] - inset), 205, 335, fill=255, width=scale)
                    d.ellipse((3 * scale, cy - 3 * scale, 9 * scale, cy + 3 * scale), fill=255)
                    d.ellipse((content_w - 9 * scale, cy - 3 * scale, content_w - 3 * scale, cy + 3 * scale), fill=255)
                    d.line((8 * scale, cy, ring[0], cy), fill=255, width=scale)
                    d.line((ring[2], cy, content_w - 8 * scale, cy), fill=255, width=scale)
                    for angle in range(0, 360, 45):
                        rad = math.radians(angle)
                        r1, r2 = diameter * .39, diameter * .44
                        d.line((cx + math.cos(rad) * r1, cy + math.sin(rad) * r1, cx + math.cos(rad) * r2, cy + math.sin(rad) * r2), fill=255, width=scale)
                paint_mask(content, orbit_path, accent_fill)
                txt_x = cx - text_w / 2 - bbox[0]
                txt_y = cy - text_h / 2 - bbox[1]

            elif style == 'glitch':
                padding_x = int(user_size * (1.0 + effect_ratio * .45) * scale)
                padding_y = int(user_size * .60 * scale)
                content_w = text_w + padding_x * 2
                content_h = text_h + padding_y * 2
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                txt_x = (content_w - text_w) / 2 - bbox[0]
                txt_y = (content_h - text_h) / 2 - bbox[1]
                text_mask = Image.new('L', content.size, 0)
                ImageDraw.Draw(text_mask).text((txt_x, txt_y), count_str, font=scaled_font, fill=255)
                slice_height = max(2 * scale, int((3 + effect_ratio * 3) * scale))
                slice_gap = max(slice_height + scale, int((8 - effect_ratio * 2) * scale))
                displacement = max(scale, int((2 + effect_ratio * 7) * scale))

                for phase, direction in ((0, 1), (1, -1)):
                    bands = Image.new('L', content.size, 0)
                    bands_draw = ImageDraw.Draw(bands)
                    y = phase * slice_gap + padding_y // 3
                    while y < content_h - padding_y // 3:
                        bands_draw.rectangle((0, y, content_w, min(content_h, y + slice_height)), fill=255)
                        y += slice_gap * 2
                    sliced = ImageChops.multiply(text_mask, bands)
                    shifted = Image.new('L', content.size, 0)
                    shifted.paste(sliced, (direction * displacement, 0))
                    channel_paint = bg_fill if direction > 0 else self._paint_opacity(text_fill, max(90, opacity - 65))
                    self._paint_mask(content, shifted, channel_paint)

                glitch_draw = ImageDraw.Draw(content)
                rail_y = content_h - max(4 * scale, padding_y // 3)
                paint_mask(content, lambda d: d.line((padding_x * .32, rail_y, content_w - padding_x * .18, rail_y), fill=255, width=scale), accent_fill)
                for idx, width_ratio in enumerate((.30, .16, .08)):
                    bar_w = int(content_w * width_ratio)
                    bar_y = int(padding_y * (.30 + idx * .24))
                    bar_x = padding_x // 4 + (idx % 2) * displacement
                    glitch_draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + max(scale, int(effect_ratio * 2 * scale))), fill=border_rgba)
                for x in (padding_x // 3, content_w - padding_x // 3):
                    glitch_draw.rectangle((x - scale, rail_y - 3 * scale, x + scale, rail_y + 3 * scale), fill=border_rgba)

            elif style == 'wave':
                side_space = int(user_size * (1.0 + effect_ratio * .65) * scale)
                padding_y = int(user_size * .70 * scale)
                content_w = text_w + side_space * 2
                content_h = text_h + padding_y * 2
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                txt_x = (content_w - text_w) / 2 - bbox[0]
                txt_y = (content_h - text_h) / 2 - bbox[1]
                center_y = content_h // 2
                bar_width = max(scale, int((1.2 + effect_ratio * 1.8) * scale))
                gap = max(bar_width + scale, int(user_size * .12 * scale))
                max_height = int((text_h * .48 + padding_y * .55) * (.45 + effect_ratio * .55))

                def waveform_path(d):
                    for side in (-1, 1):
                        origin = content_w // 2 + side * (text_w // 2 + gap * 2)
                        for idx in range(7):
                            amplitude = (.25, .55, .88, .48, 1.0, .62, .32)[idx]
                            bar_h = max(2 * scale, int(max_height * amplitude))
                            x = origin + side * idx * gap
                            d.rounded_rectangle((x - bar_width // 2, center_y - bar_h, x + bar_width // 2, center_y + bar_h), radius=bar_width // 2, fill=255)
                    baseline = center_y + max_height + 5 * scale
                    d.line((side_space * .22, baseline, content_w - side_space * .22, baseline), fill=255, width=scale)
                    d.ellipse((side_space * .22 - 2 * scale, baseline - 2 * scale, side_space * .22 + 2 * scale, baseline + 2 * scale), fill=255)
                    d.ellipse((content_w - side_space * .22 - 2 * scale, baseline - 2 * scale, content_w - side_space * .22 + 2 * scale, baseline + 2 * scale), fill=255)
                paint_mask(content, waveform_path, glow_fill, blur=max(scale, int(effect_ratio * 4 * scale)))
                paint_mask(content, waveform_path, accent_fill)

            elif style == 'burst':
                padding = int(user_size * (1.05 + effect_ratio * .5) * scale)
                content_w = text_w + padding * 2
                content_h = max(text_h + padding * 2, int(content_w * .48))
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                cx, cy = content_w / 2, content_h / 2
                outer_x, outer_y = content_w * .47, content_h * .46
                inner_ratio = .67 - effect_ratio * .16
                points = []
                ray_count = 14
                for idx in range(ray_count * 2):
                    angle = -math.pi / 2 + idx * math.pi / ray_count
                    radius = 1 if idx % 2 == 0 else inner_ratio
                    points.append((cx + math.cos(angle) * outer_x * radius, cy + math.sin(angle) * outer_y * radius))
                paint_mask(content, lambda d: d.polygon(points, fill=255), self._paint_opacity(bg_fill, opacity))
                burst_draw = ImageDraw.Draw(content)
                for angle in (-.48, -.20, .15, .42):
                    ray_x = cx + math.cos(angle) * outer_x
                    ray_y = cy + math.sin(angle) * outer_y
                    tail_x = cx + math.cos(angle) * outer_x * 1.12
                    tail_y = cy + math.sin(angle) * outer_y * 1.12
                    burst_draw.line((ray_x, ray_y, tail_x, tail_y), fill=border_rgba, width=2 * scale)
                sparkle = max(3 * scale, int((3 + effect_ratio * 4) * scale))
                for sx, sy in ((padding * .35, padding * .35), (content_w - padding * .30, content_h - padding * .38)):
                    burst_draw.line((sx - sparkle, sy, sx + sparkle, sy), fill=border_rgba, width=scale)
                    burst_draw.line((sx, sy - sparkle, sx, sy + sparkle), fill=border_rgba, width=scale)
                txt_x = cx - text_w / 2 - bbox[0]
                txt_y = cy - text_h / 2 - bbox[1]

            elif style == 'tape':
                padding_x = int(user_size * (1.0 + effect_ratio * .35) * scale)
                padding_y = int(user_size * .48 * scale)
                content_w = text_w + padding_x * 2
                content_h = text_h + padding_y * 2
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                tear = max(4 * scale, int((4 + effect_ratio * 5) * scale))
                step = max(8 * scale, int(user_size * .22 * scale))
                top = []
                bottom = []
                for idx, x in enumerate(range(1, content_w, step)):
                    top.append((x, tear if idx % 2 else 1))
                top.append((content_w - 2, tear // 2))
                for idx, x in enumerate(range(content_w - 2, 0, -step)):
                    bottom.append((x, content_h - (tear if idx % 2 else 2)))
                bottom.append((1, content_h - tear // 2))
                tape_points = top + bottom
                paint_mask(content, lambda d: d.polygon(tape_points, fill=255), self._paint_opacity(bg_fill, max(35, opacity)))
                texture_draw = ImageDraw.Draw(content)
                texture_alpha = max(25, min(120, border_alpha))
                texture_color = (255, 255, 255, texture_alpha)
                spacing = max(7 * scale, int(user_size * .16 * scale))
                for x in range(-content_h, content_w, spacing):
                    texture_draw.line((x, content_h, x + content_h, 0), fill=texture_color, width=scale)
                for x in (padding_x // 3, content_w - padding_x // 3):
                    texture_draw.line((x, tear * 1.4, x, content_h - tear * 1.4), fill=border_rgba, width=scale)
                txt_x = (content_w - text_w) / 2 - bbox[0]
                txt_y = (content_h - text_h) / 2 - bbox[1]

            elif style == 'split':
                gap = max(2 * scale, int(user_size * .10 * scale))
                char_boxes = [measure.textbbox((0, 0), char, font=scaled_font) for char in count_str]
                tile_widths = [max(box[2] - box[0] + int(user_size * .55 * scale), int(user_size * .85 * scale)) for box in char_boxes]
                tile_h = text_h + int(user_size * .62 * scale)
                rotation_pad = int(user_size * .30 * scale)
                content_w = sum(tile_widths) + gap * max(0, len(tile_widths) - 1) + rotation_pad * 2
                content_h = tile_h + rotation_pad * 2
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                cursor_x = rotation_pad
                rotation = 2 + effect_ratio * 5
                for idx, (char, char_box, tile_w) in enumerate(zip(count_str, char_boxes, tile_widths)):
                    tile = Image.new('RGBA', (tile_w, tile_h), (0, 0, 0, 0))
                    cut = max(3 * scale, int(tile_w * .10))
                    tile_points = [(cut, 1), (tile_w - 2, 1), (tile_w - cut, tile_h - 2), (1, tile_h - 2)] if idx % 2 == 0 else [(1, 1), (tile_w - cut, 1), (tile_w - 2, tile_h - 2), (cut, tile_h - 2)]
                    paint_mask(tile, lambda d, pts=tile_points: d.polygon(pts, fill=255), self._paint_opacity(bg_fill, opacity))
                    tile_draw = ImageDraw.Draw(tile)
                    tile_draw.line((cut, tile_h - 5 * scale, tile_w - cut, tile_h - 5 * scale), fill=border_rgba, width=scale)
                    dot = max(scale, int(1.6 * scale))
                    tile_draw.ellipse((tile_w - cut - dot * 2, 5 * scale, tile_w - cut, 5 * scale + dot * 2), fill=border_rgba)
                    char_w, char_h = char_box[2] - char_box[0], char_box[3] - char_box[1]
                    char_x = (tile_w - char_w) / 2 - char_box[0]
                    char_y = (tile_h - char_h) / 2 - char_box[1] - scale
                    self._draw_text_paint(tile, (char_x, char_y), char, scaled_font, text_fill)
                    angle = -rotation if idx % 2 == 0 else rotation
                    rotated_tile = tile.rotate(angle, resample=Image.BICUBIC, expand=True)
                    offset_y = rotation_pad + (int(effect_ratio * 3 * scale) if idx % 2 else 0)
                    content.alpha_composite(rotated_tile, (cursor_x - (rotated_tile.width - tile_w) // 2, offset_y - (rotated_tile.height - tile_h) // 2))
                    cursor_x += tile_w + gap
                custom_text_drawn = True

            elif style == 'stamp':
                content_w = text_w + int(user_size * 1.2 * scale)
                content_h = text_h + int(user_size * .95 * scale)
                content = Image.new('RGBA', (content_w, content_h), (0, 0, 0, 0))
                cut = 9 * scale
                outer = [(cut, 1), (content_w - cut - 1, 1), (content_w - 2, cut), (content_w - 2, content_h - cut - 1), (content_w - cut - 1, content_h - 2), (cut, content_h - 2), (1, content_h - cut - 1), (1, cut)]

                def stamp_path(d):
                    d.line(outer + [outer[0]], fill=255, width=4 * scale, joint='curve')
                    inset = 9 * scale
                    d.rectangle((inset, inset, content_w - inset - 1, content_h - inset - 1), outline=255, width=scale)
                    for x in range(inset + 4 * scale, content_w - inset, 12 * scale):
                        d.line((x, 2 * scale, min(x + 5 * scale, content_w - cut), 2 * scale), fill=255, width=scale)
                        d.line((x, content_h - 3 * scale, min(x + 5 * scale, content_w - cut), content_h - 3 * scale), fill=255, width=scale)
                paint_mask(content, stamp_path, accent_fill)
                txt_x = (content_w - text_w) / 2 - bbox[0]
                txt_y = (content_h - text_h) / 2 - bbox[1]

            else:
                padding_x = int(user_size * 0.9 * scale)
                padding_y = int(user_size * 0.5 * scale)
                shape_w = text_w + padding_x * 2
                shape_h = text_h + padding_y * 2
                content = Image.new('RGBA', (shape_w, shape_h), (0, 0, 0, 0))

                if style == 'outline':
                    corner = min(shape_h // 3, 24 * scale)
                    pad = 8 * scale

                    def circuit_path(d):
                        lines = [
                            (pad, pad + corner, pad, pad, pad + corner, pad),
                            (shape_w - pad - corner, pad, shape_w - pad, pad, shape_w - pad, pad + corner),
                            (pad, shape_h - pad - corner, pad, shape_h - pad, pad + corner, shape_h - pad),
                            (shape_w - pad - corner, shape_h - pad, shape_w - pad, shape_h - pad, shape_w - pad, shape_h - pad - corner),
                        ]
                        for line in lines: d.line(line, fill=255, width=3 * scale, joint='curve')
                        cy = shape_h // 2
                        d.line((1, cy, pad + corner // 2, cy), fill=255, width=scale)
                        d.line((shape_w - pad - corner // 2, cy, shape_w - 2, cy), fill=255, width=scale)
                        d.ellipse((pad - 2 * scale, cy - 2 * scale, pad + 2 * scale, cy + 2 * scale), fill=255)
                        d.ellipse((shape_w - pad - 2 * scale, cy - 2 * scale, shape_w - pad + 2 * scale, cy + 2 * scale), fill=255)
                    paint_mask(content, circuit_path, glow_fill, blur=5 * scale)
                    paint_mask(content, circuit_path, accent_fill)

                elif style == 'hexagon':
                    cut = max(12 * scale, shape_h // 3)
                    points = [(cut, 3 * scale), (shape_w - cut - 3 * scale, 3 * scale), (shape_w - 3 * scale, shape_h // 2), (shape_w - cut - 3 * scale, shape_h - 3 * scale), (cut, shape_h - 3 * scale), (3 * scale, shape_h // 2)]

                    def prism_path(d):
                        d.line(points + [points[0]], fill=255, width=3 * scale, joint='curve')
                        mid = shape_h // 2
                        d.line((1, mid, cut // 2, mid), fill=255, width=2 * scale)
                        d.line((shape_w - cut // 2, mid, shape_w - 2, mid), fill=255, width=2 * scale)
                        for side in (-1, 1):
                            center = shape_w // 2 + side * int(shape_w * .31)
                            d.line((center - 5 * scale, mid - 5 * scale, center, mid, center - 5 * scale, mid + 5 * scale), fill=255, width=2 * scale)
                        d.line((shape_w // 2 - 12 * scale, 1, shape_w // 2 + 12 * scale, 1), fill=255, width=2 * scale)
                    paint_mask(content, prism_path, accent_fill)

                else:
                    radius = min(12 * scale, shape_h // 4)
                    notch = max(6 * scale, shape_h // 7)
                    center_y = shape_h // 2

                    def ticket_shape(d):
                        d.rounded_rectangle((1, 1, shape_w - 2, shape_h - 2), radius=radius, fill=255)
                        d.ellipse((-notch, center_y - notch, notch, center_y + notch), fill=0)
                        d.ellipse((shape_w - notch, center_y - notch, shape_w + notch, center_y + notch), fill=0)
                    paint_mask(content, ticket_shape, bg_fill)
                    decor = ImageDraw.Draw(content)
                    dash_x = max(notch + 4 * scale, int(shape_w * .18))
                    for dash_y in range(6 * scale, shape_h - 6 * scale, 8 * scale):
                        decor.line((dash_x, dash_y, dash_x, min(dash_y + 4 * scale, shape_h - 6 * scale)), fill=border_rgba, width=scale)
                    barcode_x = shape_w - max(notch + 8 * scale, int(shape_w * .18))
                    for idx, bar_w in enumerate((1, 3, 1, 2, 1)):
                        x = barcode_x + idx * 4 * scale
                        decor.line((x, shape_h * .30, x, shape_h * .70), fill=border_rgba, width=bar_w * scale)

                if style not in ('circle', 'stamp'):
                    txt_x = (content.width - text_w) / 2 - bbox[0]
                    txt_y = (content.height - text_h) / 2 - bbox[1]
                    if style == 'ticket': txt_x -= shape_w * .04

            if style == 'outline':
                glow_layer = Image.new('RGBA', content.size, (0, 0, 0, 0))
                self._draw_text_paint(glow_layer, (txt_x, txt_y), count_str, scaled_font, self._paint_opacity(text_fill, 170))
                content.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(4 * scale)))
            if not custom_text_drawn:
                self._draw_text_paint(content, (txt_x, txt_y), count_str, scaled_font, text_fill)
            if style == 'stamp':
                content = content.rotate(-8, resample=Image.BICUBIC, expand=True)
            elif style == 'tape':
                content = content.rotate(-3 - effect_ratio * 4, resample=Image.BICUBIC, expand=True)
            place_content(content)

        overlay_resized = overlay.resize((w, h), resample=Image.LANCZOS)
        return Image.alpha_composite(img, overlay_resized)

    # === 动态 GIF 生成 ===
    def draw(self, config, assets):
        bg = None
        if assets.get('bg_url'):
            bg = self.download_img(assets['bg_url'])
        
        if not bg: bg = Image.new("RGBA", (1920, 1080), (20, 30, 50, 255))
        bg = bg.resize((1920, 1080))
        
        blur = int(config.get('blur_radius', 4))
        if blur > 0: bg = bg.filter(ImageFilter.GaussianBlur(blur))
        bg = ImageEnhance.Brightness(bg).enhance(float(config.get('brightness', 0.7)))

        fonts = {
            'main': self._load_text_font(
                config.get('font_title'),
                int(config.get('title_size', 160)),
                config.get('title', ''),
            ),
            'sub': self._load_text_font(
                config.get('font_subtitle'),
                int(config.get('subtitle_size', 80)),
                config.get('subtitle', ''),
            ),
            'count': self._load_text_font(
                config.get('font_count'),
                int(config.get('count_size', 40)),
                str(assets.get('count', '')),
            ),
        }

        engine_type = config.get('engine', 'classic')
        if engine_type == '旋转堆叠':
            engine_type = '经典九宫格'
        is_dynamic = config.get('enable_animation', False)
        
        output = BytesIO()

        if is_dynamic:
            print(f">>> [Engine] 启动多线程动态 GIF 渲染 (Max 300 Frames)...")

            # Warm the shared cache before frame workers start so the same Emby
            # poster/backdrop is not downloaded concurrently for every frame.
            asset_urls = [assets.get('bg_url')]
            asset_urls.extend(assets.get('posters') or [])
            asset_urls.extend(assets.get('poster_backdrops') or [])
            asset_urls.extend(assets.get('backdrops') or [])
            for asset_url in dict.fromkeys(url for url in asset_urls if url):
                self.download_img(asset_url)
            
            target_w = 533
            target_h = round(target_w * 9 / 16)
            duration = max(40, min(500, int(config.get('anim_duration', 100))))

            synchronized_dynamic_engines = {
                '经典堆叠(动态)',
                '经典平铺(动态)',
                '聚焦(动态)',
                '扇形展开(动态)',
                '旋转(动态)',
                '经典九宫格(动态)',
            }
            uses_synchronized_timeline = engine_type in synchronized_dynamic_engines

            if uses_synchronized_timeline:
                posters = list(assets.get('posters') or [])
                backdrops = list(assets.get('poster_backdrops') or [])
                paired_count = sum(
                    1 for index, poster in enumerate(posters)
                    if poster and index < len(backdrops) and backdrops[index]
                )
                item_count = paired_count or len(posters) or 1
                item_count = min(item_count, max(1, int(config.get('poster_count', item_count))))
                switch_interval = max(500, int(config.get('switch_interval', 2500)))
                frames_per_item = max(3, round(switch_interval / duration))
                total_frames = min(item_count * frames_per_item, 300)
            else:
                user_frames = int(config.get('anim_frames', 30))
                total_frames = min(user_frames, 300)
            
            frames_dict = {}

            def render_one_frame(idx):
                if uses_synchronized_timeline and total_frames > 1:
                    step = idx / (total_frames - 1)
                else:
                    step = idx / total_frames if total_frames > 1 else 0
                frame_canvas = bg.copy()
                
                if engine_type in self.layout_modules:
                    render_func = self.layout_modules[engine_type]
                    try:
                        final_frame = render_func(self, frame_canvas, config, assets, fonts, step=step)
                    except TypeError:
                        final_frame = render_func(self, frame_canvas, config, assets, fonts)
                else:
                    final_frame = frame_canvas
                
                count_val = assets.get('count', 0)
                final_frame = self._draw_badge(final_frame, config, count_val, fonts)
                resized = final_frame.resize((target_w, target_h), Image.LANCZOS)
                return idx, resized

            worker_count = 4 if target_w >= 960 else 8
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(render_one_frame, i) for i in range(total_frames)]
                for future in as_completed(futures):
                    try:
                        idx, img = future.result()
                        frames_dict[idx] = img
                    except Exception as e:
                        print(f"Frame Error: {e}")

            sorted_frames = [frames_dict[i] for i in range(total_frames) if i in frames_dict]

            if sorted_frames:
                coalesced_frames = []
                frame_durations = []
                previous_signature = None
                for frame in sorted_frames:
                    signature = frame.resize((32, 18), Image.BILINEAR).tobytes()
                    is_identical = (
                        coalesced_frames
                        and signature == previous_signature
                        and ImageChops.difference(coalesced_frames[-1], frame).getbbox() is None
                    )
                    if is_identical:
                        frame_durations[-1] += duration
                    else:
                        coalesced_frames.append(frame)
                        frame_durations.append(duration)
                        previous_signature = signature

                animation_format = 'gif'
                if animation_format == 'gif':
                    sample_count = min(12, len(coalesced_frames))
                    sample_indices = [round(index * (len(coalesced_frames) - 1) / max(1, sample_count - 1)) for index in range(sample_count)]
                    palette_sheet = Image.new('RGB', (160 * sample_count, 90))
                    for index, frame_index in enumerate(sample_indices):
                        palette_sheet.paste(coalesced_frames[frame_index].convert('RGB').resize((160, 90), Image.LANCZOS), (index * 160, 0))
                    palette = palette_sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
                    gif_frames = [
                        frame.convert('RGB').quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
                        for frame in coalesced_frames
                    ]
                    gif_frames[0].save(
                        output,
                        format='GIF',
                        save_all=True,
                        append_images=gif_frames[1:],
                        duration=frame_durations,
                        loop=0,
                        optimize=True,
                        disposal=2,
                    )
                    format_label = 'GIF'
                else:
                    coalesced_frames[0].save(
                        output,
                        format='PNG',
                        save_all=True,
                        append_images=coalesced_frames[1:],
                        duration=frame_durations,
                        loop=0,
                        optimize=False,
                    )
                    format_label = 'APNG'
                print(f">>> [Engine] {format_label} 完成. 帧数: {len(coalesced_frames)}, 大小: {output.getbuffer().nbytes / 1024 / 1024:.2f} MB")
        else:
            # 静态图逻辑，保持高清 1920x1080
            if engine_type in self.layout_modules:
                final_img = self.layout_modules[engine_type](self, bg, config, assets, fonts)
            else:
                if 'classic' in self.layout_modules:
                    final_img = self.layout_modules['classic'](self, bg, config, assets, fonts)
                else:
                    final_img = bg

            count_val = assets.get('count', 0)
            final_img = self._draw_badge(final_img, config, count_val, fonts)
            final_img.convert("RGB").save(output, format='JPEG', quality=90)
            
        return base64.b64encode(output.getvalue()).decode('utf-8')
