"""MediaTidy 封面工厂插件。

复用 FrameBase 的 Pillow 封面渲染核心，走 MediaTidy 宿主能力：
- emby.request    查询媒体库/条目、上传封面（base64）
- files.*         读取本地授权目录中的素材图片
- storage         保存预览与配置草稿

标题自动取自所选 Emby 条目名；素材图从本地授权目录读取。
入口：plugin.py:Plugin，继承 mt_plugin.Plugin。
"""
from __future__ import annotations

import base64
import os
import urllib.request
from pathlib import Path
from typing import Any

from mt_plugin import Plugin as BasePlugin, Context, action, query
from mt_plugin import ui

try:
    from engine import PosterEngine
except ImportError:  # 宿主以文件路径加载 plugin.py 时包内相对导入兜底
    from engine.engine import PosterEngine  # type: ignore[no-redef]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def _to_options(items: list, label_key: str, value_key: str) -> list[dict]:
    # ⚠️ Emby 的 Id 是数字（如 3、17381），但 inputSchema 声明为 string，
    # 不转 str 会报 $.libraryId: Schema 须为 string
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        value = it.get(value_key)
        out.append({
            "label": it.get(label_key) or "",
            "value": str(value) if value is not None else None,
        })
    return out


def _parse_size(value: Any, default: int) -> int:
    """宽容解析字号入参：非数字/越界时回退默认值并钳制到引擎范围 8~500。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    return max(8, min(500, size))


def _storage_value(data: Any) -> Any:
    """storage.get 的返回形状不一：None / 无 found 字段时安全取 value。"""
    if not isinstance(data, dict) or not data.get("found"):
        return None
    return data.get("value")


class EmbyDirect:
    """直连 Emby 取图片二进制。

    参考 kunmini/mediatidy-cover-studio 的做法：SDK 的 emby.request 只支持
    JSON/UTF-8 响应，拿不到图片二进制，所以下载海报和上传封面都用标准库
    urllib 直连 Emby，需要用户在插件配置里填 Emby 地址 + API Key
    （Emby 后台 → 设置 → 高级 → API 密钥 → 新建）。
    读库/读条目仍然走 SDK（宿主自动鉴权）。
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 25):
        self.base = (base_url or "").rstrip("/")
        self.key = (api_key or "").strip()
        self.timeout = timeout

    @property
    def ok(self) -> bool:
        return bool(self.base and self.key)

    def image_url(self, item_id: str, max_width: int = 1000) -> str:
        return f"{self.base}/Items/{item_id}/Images/Primary?MaxWidth={max_width}&Quality=90"

    def image(self, item_id: str, max_width: int = 1000) -> bytes | None:
        try:
            req = urllib.request.Request(
                self.image_url(item_id, max_width),
                headers={"X-Emby-Token": self.key, "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception:
            return None


class Plugin(BasePlugin):
    """封面工厂插件实例。"""

    def on_init(self, ctx: Context) -> None:
        self._engine: PosterEngine | None = None
        ctx.mt.log(level="info", message="封面工厂插件已初始化")

    # ------------------------------------------------------------------ #
    def engine(self) -> PosterEngine:
        if self._engine is None:
            base = Path(os.path.dirname(os.path.abspath(__file__)))
            self._engine = PosterEngine(
                fonts_dir=str(base / "fonts"),
                layouts_dir=str(base / "layouts"),
            )
        return self._engine

    # ------------------------------------------------------------------ #
    # 查询：Emby 服务器（返回 {label,value} 选项）
    # ------------------------------------------------------------------ #
    @query("servers")
    def servers(self, input: dict, ctx: Context):
        servers = ctx.mt.emby.servers.list()
        return _to_options(servers, "name", "id")

    # ------------------------------------------------------------------ #
    # 查询：媒体库列表（读 storage 缓存）
    # ------------------------------------------------------------------ #
    @query("libraries")
    def libraries(self, input: dict, ctx: Context):
        """实时经 SDK 读媒体库（参考 cover-studio 的 status 查询做法）；
        失败时退回 load-libraries 写入的 storage 缓存。"""
        server_id = (input or {}).get("serverId") or self._first_server_id(ctx)
        if server_id:
            try:
                result = ctx.mt.emby.request(
                    server_id=server_id, path="/Library/SelectableMediaFolders") or {}
                if result.get("status") == 200:
                    opts = _to_options(result.get("data") or [], "Name", "Id")
                    if opts:
                        try:
                            ctx.mt.storage.set(key="libraries", value=opts)
                        except Exception:
                            pass
                        return opts
            except Exception as e:
                ctx.mt.log(level="warn", message=f"实时读取媒体库失败，改用缓存: {e}")
        return _storage_value(ctx.mt.storage.get(key="libraries")) or []

    # ------------------------------------------------------------------ #
    # 查询：条目列表（读 storage 缓存）
    # ------------------------------------------------------------------ #
    @query("items")
    def items(self, input: dict, ctx: Context):
        return _storage_value(ctx.mt.storage.get(key="items")) or []

    # ------------------------------------------------------------------ #
    # 动作：加载媒体库（结果写入 storage）
    # ------------------------------------------------------------------ #
    @action("load-libraries")
    def load_libraries(self, input: dict, ctx: Context):
        server_id = input.get("serverId") or self._first_server_id(ctx)
        if not server_id:
            raise ValueError("请先在设置中授权 Emby 服务器")
        result = ctx.mt.emby.request(
            server_id=server_id,
            path="/Library/SelectableMediaFolders",
        ) or {}
        if result.get("status") != 200:
            raise ValueError(f"Emby 返回 HTTP {result.get('status')}")
        data = result.get("data") or []
        opts = _to_options(data, "Name", "Id")
        ctx.mt.storage.set(key="libraries", value=opts)
        ctx.mt.log(level="info", message=f"已加载 {len(opts)} 个媒体库")
        return {"count": len(opts)}

    # ------------------------------------------------------------------ #
    # 动作：加载条目（结果写入 storage）
    # ------------------------------------------------------------------ #
    @action("load-items")
    def load_items(self, input: dict, ctx: Context):
        server_id = input.get("serverId") or self._first_server_id(ctx)
        library_id = input.get("libraryId")
        if not server_id or not library_id:
            raise ValueError("请先选择服务器和媒体库")
        q: dict[str, Any] = {
            "ParentId": library_id,
            "Recursive": True,
            "IncludeItemTypes": "Movie,Series,BoxSet",
            "Fields": "ProviderIds,ProductionYear",
            "Limit": 100,
            "StartIndex": 0,
        }
        result = ctx.mt.emby.request(server_id=server_id, path="/Items", query=q) or {}
        if result.get("status") != 200:
            raise ValueError(f"Emby 返回 HTTP {result.get('status')}")
        items = ((result.get("data") or {}).get("Items", []) if isinstance(result.get("data"), dict) else [])
        opts = []
        for it in items:
            name = it.get("Name") or ""
            year = it.get("ProductionYear")
            label = f"{name} ({year})" if year else name
            opts.append({"label": label, "value": str(it.get("Id")) if it.get("Id") is not None else None})
        ctx.mt.storage.set(key="items", value=opts)
        ctx.mt.log(level="info", message=f"已加载 {len(opts)} 个条目")
        return {"count": len(opts)}

    def _plugin_config(self, ctx: Context) -> dict:
        """读插件配置：优先宿主下发的 ctx.config，再退回 storage。"""
        try:
            raw = getattr(ctx, "config", None)
            if isinstance(raw, dict) and raw:
                return dict(raw)
        except Exception:
            pass
        return _storage_value(ctx.mt.storage.get(key="config")) or {}

    def _fetch_emby_posters(self, ctx: Context, server_id: str, library_id: str,
                            direct: EmbyDirect, count: int = 9) -> list[str]:
        """经 SDK 读同库条目（去重：作品名+年份只取一张），直连下载海报。

        返回可交给 PosterEngine 的图片 URL 列表（引擎支持 http 直连下载）。
        """
        result = ctx.mt.emby.request(
            server_id=server_id, path="/Items",
            query={"ParentId": library_id, "Recursive": True,
                   "IncludeItemTypes": "Movie,Series,BoxSet",
                   "Fields": "ProductionYear,ImageTags", "Limit": 100,
                   "EnableTotalRecordCount": True},
        ) or {}
        if result.get("status") != 200 or not isinstance(result.get("data"), dict):
            return []
        items = result["data"].get("Items", []) or []
        seen: set[str] = set()
        picked: list[str] = []
        for it in items:
            if not isinstance(it, dict) or not it.get("Id"):
                continue
            if not (it.get("ImageTags") or {}).get("Primary"):
                continue
            key = f"{str(it.get('Name') or '').strip().lower()}|{it.get('ProductionYear') or ''}"
            if key in seen:
                continue
            seen.add(key)
            picked.append(direct.image_url(str(it["Id"])))
            if len(picked) >= count:
                break
        return picked

    def _first_server_id(self, ctx: Context) -> str | None:
        """返回第一个已授权且启用的 Emby 服务器 ID，无则 None。"""
        servers = ctx.mt.emby.servers.list()
        for s in servers:
            if s.get("enabled") and s.get("hasApiKey"):
                return s.get("id")
        if servers:
            return servers[0].get("id")
        return None

    # ------------------------------------------------------------------ #
    # 动作：探测 Emby 图片接口（临时调试）
    # ------------------------------------------------------------------ #
    @action("probe-image")
    def probe_image(self, input: dict, ctx: Context):
        server_id = input.get("serverId") or self._first_server_id(ctx)
        item_id = input.get("itemId")
        if not server_id:
            raise ValueError("请先在设置中授权 Emby 服务器")
        if not item_id:
            raise ValueError("请先选择条目")
        # 先拿 ImageTags 里的 Primary tag
        info = ctx.mt.emby.request(
            server_id=server_id, path="/Items", query={"Ids": item_id, "Fields": "ImageTags"}
        ) or {}
        tag = None
        if info.get("status") == 200 and isinstance(info.get("data"), dict):
            rows = info["data"].get("Items", [])
            if rows:
                tag = (rows[0].get("ImageTags") or {}).get("Primary")
        # 试图片接口
        img = ctx.mt.emby.request(
            server_id=server_id, path=f"/Items/{item_id}/Images/Primary",
            query={"tag": tag, "maxWidth": 300} if tag else {"maxWidth": 300},
        ) or {}
        data = img.get("data")
        result = {
            "itemId": item_id,
            "primaryTag": tag,
            "status": img.get("status"),
            "dataType": type(data).__name__,
            "dataLen": len(data) if data else 0,
            "dataPreview": (repr(data)[:200] if data else None),
        }
        ctx.mt.log(level="info", message=f"probe-image 结果: {result}")
        return result

    # ------------------------------------------------------------------ #
    # 动作：生成封面并上传到 Emby 条目
    # ------------------------------------------------------------------ #
    @action("generate-cover")
    def generate_cover(self, input: dict, ctx: Context):
        server_id = input.get("serverId") or self._first_server_id(ctx)
        target_id = input.get("targetId")
        layout = input.get("layout") or "经典九宫格"
        title_size = _parse_size(input.get("titleSize"), 160)
        subtitle_size = _parse_size(input.get("subtitleSize"), 80)
        upload = bool(input.get("upload"))

        if not server_id:
            raise ValueError("请先在设置中授权 Emby 服务器")
        # 一键模式：不选条目时直接给媒体库生成封面（target = libraryId）
        library_id_early = input.get("libraryId")
        if not target_id:
            target_id = library_id_early
        if not target_id:
            raise ValueError("请先选择媒体库")

        # 1. 标题自动取条目名/库名
        title = ""
        subtitle = ""
        item = ctx.mt.emby.request(
            server_id=server_id,
            path="/Items",
            query={"Ids": target_id, "Fields": "ProductionYear"},
        ) or {}
        if item.get("status") == 200 and isinstance(item.get("data"), dict):
            rows = item["data"].get("Items", [])
            if rows:
                title = rows[0].get("Name") or ""
                year = rows[0].get("ProductionYear")
                subtitle = str(year) if year else ""
        if not title:
            title = input.get("title") or "封面"

        # 2. 素材图（两种来源，Emby 优先，不需要映射本地目录）：
        #    a) Emby 模式：配置 embyUrl + embyApiKey 且传入 libraryId 时，
        #       从同库作品海报取图（参考 kunmini/mediatidy-cover-studio）。
        #       只需 emby.api 授权 + 勾选 Emby 服务器，无需 files.read/目录映射。
        #    b) 本地模式：files.read 授权的本地目录素材图（需要 roots 目录映射）。
        cfg = self._plugin_config(ctx)
        direct = EmbyDirect(input.get("embyUrl") or cfg.get("embyUrl") or "",
                            input.get("embyApiKey") or cfg.get("embyApiKey") or "")
        library_id = input.get("libraryId")
        poster_paths: list[str] = []
        if direct.ok and library_id:
            try:
                poster_paths = self._fetch_emby_posters(ctx, server_id, str(library_id), direct)
            except Exception as e:
                ctx.mt.log(level="warn", message=f"Emby 海报获取失败，改用本地素材: {e}")
                poster_paths = []

        root_id = input.get("rootId")
        asset_path = input.get("path") or "."
        if root_id:
            page = 0
            while True:
                res = ctx.mt.files.list(root_id=root_id, path=asset_path, page=page) or {}
                for f in res.get("items", []) or []:
                    if not isinstance(f, dict) or f.get("isDir"):
                        continue
                    name = f.get("name") or ""
                    if Path(name).suffix.lower() in IMAGE_EXTS and f.get("ref"):
                        local = ctx.download(f["ref"])
                        poster_paths.append(str(local))
                if not res.get("hasMore"):
                    break
                page += 1

        # 同时接受 FilePicker 直接选中的图片（无选择时 ctx.files 可能为 None）
        for f in ctx.files or []:
            if not isinstance(f, dict) or f.get("isDir") or not f.get("ref"):
                continue
            local = ctx.download(f["ref"])
            poster_paths.append(str(local))

        if not poster_paths:
            raise ValueError("未找到素材图片：请先在文件选择器中选择图片，或授权素材目录")

        bg_path = poster_paths[0] if poster_paths else None
        assets = {
            "bg_url": bg_path,
            "posters": poster_paths,
            "backdrops": [],
            "poster_backdrops": [],
            "count": len(poster_paths),
        }
        config: dict[str, Any] = {
            "engine": layout,
            "title": title,
            "subtitle": subtitle,
            "font_title": input.get("fontTitle") or cfg.get("fontTitle") or "OPPOSans-B.ttf",
            "font_subtitle": input.get("fontSubtitle") or cfg.get("fontSubtitle") or "OPPOSans-B.ttf",
            "title_size": title_size,
            "subtitle_size": subtitle_size,
            "enable_animation": False,
        }
        # 字体/角标/排版等可选配置：页面或插件 config 里填了才透传，
        # 引擎对不认识的布局会直接忽略多余键
        _extra_str = [
            "fontTitle", "fontSubtitle", "fontCount",
            "titleColor", "subtitleColor",
            "badgeStyle", "badgeFont", "badgeTextColor", "badgeBgColor",
        ]
        _extra_num = [
            "badgeSize", "badgeXPercent", "badgeYPercent",
            "badgeOpacity", "badgeBorderOpacity",
            "titleOffsetY", "subtitleOffsetY",
            "posterCount", "brightness", "blurRadius",
            "fanRadius", "fanSpread", "centerScale", "sideScaleShrink",
            "layoutYOffset",
        ]
        for key in _extra_str:
            value = input.get(key)
            if value not in (None, ""):
                # camelCase 入参 -> snake_case 引擎键
                snake = "".join(
                    f"_{c.lower()}" if c.isupper() else c for c in key
                ).lstrip("_")
                config[snake] = value
        for key in _extra_num:
            value = input.get(key)
            if value in (None, ""):
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            snake = "".join(
                f"_{c.lower()}" if c.isupper() else c for c in key
            ).lstrip("_")
            config[snake] = int(num) if num == int(num) else num

        ctx.mt.log(level="info", message=f"生成封面：标题={title}, 布局={layout}, 素材={len(poster_paths)} 张")
        b64 = self.engine().draw(config, assets)
        image_bytes = base64.b64decode(b64)
        ctx.mt.log(level="info", message=f"封面已生成，大小 {len(image_bytes)} 字节")

        result: dict[str, Any] = {"title": title, "size": len(image_bytes)}

        # 3. 上传到 Emby 条目封面（尽力而为）
        if upload:
            ctx.mt.log(level="info", message=f"上传封面到 Emby 条目 {target_id}")
            resp = ctx.mt.emby.request(
                server_id=server_id,
                method="POST",
                path=f"/Items/{target_id}/Images/Primary",
                json=b64,
            ) or {}
            result["uploadStatus"] = resp.get("status")
            result["uploaded"] = resp.get("status") in (200, 204)
            if result["uploaded"]:
                ctx.mt.log(level="info", message="封面已上传到 Emby")
            else:
                ctx.mt.log(level="warn", message=f"上传返回 HTTP {resp.get('status')}")

        # 4. 保存预览
        if len(image_bytes) <= 8 * 1024 * 1024:
            ctx.mt.storage.set(key="preview", value={"b64": b64, "size": len(image_bytes)})

        return result

    # ------------------------------------------------------------------ #
    @query("preview")
    def preview(self, input: dict, ctx: Context):
        return _storage_value(ctx.mt.storage.get(key="preview")) or {}

    # ------------------------------------------------------------------ #
    @query("fonts")
    def fonts(self, input: dict, ctx: Context):
        """内置字体列表（供页面字体下拉；引擎按文件名加载，找不到自动 fallback）。"""
        import os as _os
        fonts_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "fonts")
        try:
            names = sorted(
                n for n in _os.listdir(fonts_dir)
                if n.lower().endswith((".ttf", ".otf"))
            )
        except OSError:
            return []
        return [{"label": n, "value": n} for n in names]

    # ------------------------------------------------------------------ #
    def get_page(self, ctx: Context):
        """一键生成页（对齐 cover-studio）：配置一次，之后只点一个按钮。

        - Emby 地址/API Key 在插件「配置并启用」时填一次（plugin.json config）
        - 媒体库列表由 libraries 查询实时经 SDK 读取，无需手动加载
        - 点「一键生成」即自动取海报、合成、上传，无需选条目/选文件/调字号
        """
        return ui.page(
            ui.Card(
                ui.Text(text="配置好 Emby 地址和 API Key 后，选好媒体库点一键生成即可。"),
                ui.Select(label="媒体库", bind="state.libraryId",
                          options=ui.ref("query.libraries.data")),
                ui.Select(label="布局模板", bind="state.layout", options=[
                    {"label": "经典九宫格", "value": "经典九宫格"},
                    {"label": "经典堆叠", "value": "经典堆叠"},
                    {"label": "经典平铺", "value": "经典平铺"},
                    {"label": "极简", "value": "极简"},
                    {"label": "海报墙", "value": "海报墙"},
                    {"label": "扇形展开", "value": "扇形展开"},
                    {"label": "斜线分割", "value": "斜线分割"},
                    {"label": "自由设计", "value": "自由设计"},
                    {"label": "旋转", "value": "旋转"},
                ]),
                ui.Button(label="🚀 一键生成封面", onClick=[{
                    "type": "invoke", "actionId": "generate-cover",
                    "input": {
                        "serverId": ui.ref("state.serverId"),
                        "libraryId": ui.ref("state.libraryId"),
                        "layout": ui.ref("state.layout"),
                        "fontTitle": ui.ref("state.fontTitle"),
                        "fontSubtitle": ui.ref("state.fontSubtitle"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "titleColor": ui.ref("state.titleColor"),
                        "subtitleColor": ui.ref("state.subtitleColor"),
                        "titleOffsetY": ui.ref("state.titleOffsetY"),
                        "subtitleOffsetY": ui.ref("state.subtitleOffsetY"),
                        "posterCount": ui.ref("state.posterCount"),
                        "brightness": ui.ref("state.brightness"),
                        "blurRadius": ui.ref("state.blurRadius"),
                        "badgeStyle": ui.ref("state.badgeStyle"),
                        "badgeFont": ui.ref("state.badgeFont"),
                        "badgeSize": ui.ref("state.badgeSize"),
                        "badgeXPercent": ui.ref("state.badgeXPercent"),
                        "badgeYPercent": ui.ref("state.badgeYPercent"),
                        "badgeTextColor": ui.ref("state.badgeTextColor"),
                        "badgeBgColor": ui.ref("state.badgeBgColor"),
                        "badgeOpacity": ui.ref("state.badgeOpacity"),
                        "badgeBorderOpacity": ui.ref("state.badgeBorderOpacity"),
                        "fanRadius": ui.ref("state.fanRadius"),
                        "fanSpread": ui.ref("state.fanSpread"),
                        "centerScale": ui.ref("state.centerScale"),
                        "sideScaleShrink": ui.ref("state.sideScaleShrink"),
                        "layoutYOffset": ui.ref("state.layoutYOffset"),
                        "upload": True,
                    },
                }]),
            ),
            ui.Card(
                ui.Text(text="字体与排版：字体从内置字体库选，找不到时引擎自动用同目录其他字体兜底。"),
                ui.Select(label="主标题字体", bind="state.fontTitle",
                          options=ui.ref("query.fonts.data")),
                ui.Select(label="副标题字体", bind="state.fontSubtitle",
                          options=ui.ref("query.fonts.data")),
                ui.Input(label="主标题字号", bind="state.titleSize", type="number"),
                ui.Input(label="副标题字号", bind="state.subtitleSize", type="number"),
                ui.Input(label="主标题颜色", bind="state.titleColor"),
                ui.Input(label="副标题颜色", bind="state.subtitleColor"),
                ui.Input(label="主标题垂直偏移", bind="state.titleOffsetY", type="number"),
                ui.Input(label="副标题垂直偏移", bind="state.subtitleOffsetY", type="number"),
            ),
            ui.Card(
                ui.Text(text="徽章（左上角数量角标）：样式 none=不显示，box/ribbon/circle 等见引擎。"),
                ui.Select(label="徽章样式", bind="state.badgeStyle", options=[
                    {"label": "不显示", "value": "none"},
                    {"label": "圆角框", "value": "box"},
                    {"label": "斜带", "value": "ribbon"},
                    {"label": "圆环", "value": "circle"},
                    {"label": "描边", "value": "outline"},
                    {"label": "六边形", "value": "hexagon"},
                    {"label": "票根", "value": "ticket"},
                    {"label": "邮戳", "value": "stamp"},
                    {"label": "故障风", "value": "glitch"},
                    {"label": "波形", "value": "wave"},
                    {"label": "爆发", "value": "burst"},
                    {"label": "胶带", "value": "tape"},
                    {"label": "分段", "value": "split"},
                ]),
                ui.Select(label="徽章字体", bind="state.badgeFont",
                          options=ui.ref("query.fonts.data")),
                ui.Input(label="徽章大小", bind="state.badgeSize", type="number"),
                ui.Input(label="水平位置 %", bind="state.badgeXPercent", type="number"),
                ui.Input(label="垂直位置 %", bind="state.badgeYPercent", type="number"),
                ui.Input(label="文字配色", bind="state.badgeTextColor"),
                ui.Input(label="图形配色", bind="state.badgeBgColor"),
                ui.Input(label="背景透明度", bind="state.badgeOpacity", type="number"),
                ui.Input(label="边框透明度", bind="state.badgeBorderOpacity", type="number"),
            ),
            ui.Card(
                ui.Text(text="资源与扇形布局参数（其他布局自动忽略不认识的参数）。"),
                ui.Input(label="海报数量", bind="state.posterCount", type="number"),
                ui.Input(label="背景压暗程度 (0-1，如 0.7)", bind="state.brightness", type="number"),
                ui.Input(label="背景模糊程度", bind="state.blurRadius", type="number"),
                ui.Input(label="扇形半径 (曲率)", bind="state.fanRadius", type="number"),
                ui.Input(label="扇形展开总角度", bind="state.fanSpread", type="number"),
                ui.Input(label="C位海报大小", bind="state.centerScale", type="number"),
                ui.Input(label="侧边缩小比例", bind="state.sideScaleShrink", type="number"),
                ui.Input(label="海报整体垂直位置", bind="state.layoutYOffset", type="number"),
            ),
            ui.RunHistory(),
            ui.RunLog(),
            queries=[
                {"id": "servers", "name": "servers"},
                {"id": "libraries", "name": "libraries"},
                {"id": "fonts", "name": "fonts"},
                {"id": "preview", "name": "preview"},
            ],
            state={
                "serverId": None,
                "libraryId": None,
                "layout": "经典九宫格",
                "fontTitle": "OPPOSans-B.ttf",
                "fontSubtitle": "OPPOSans-B.ttf",
                "titleSize": 160,
                "subtitleSize": 80,
                "titleColor": "#FFFFFF",
                "subtitleColor": "#AAAAAA",
                "titleOffsetY": 0,
                "subtitleOffsetY": 0,
                "posterCount": 9,
                "brightness": 0.7,
                "blurRadius": 4,
                "badgeStyle": "none",
                "badgeFont": "OPPOSans-B.ttf",
                "badgeSize": 90,
                "badgeXPercent": 2,
                "badgeYPercent": 3,
                "badgeTextColor": "#FFFFFF",
                "badgeBgColor": "#000000",
                "badgeOpacity": 255,
                "badgeBorderOpacity": 40,
                "fanRadius": 1200,
                "fanSpread": 40,
                "centerScale": 0.55,
                "sideScaleShrink": 0.95,
                "layoutYOffset": 100,
            },
        )
