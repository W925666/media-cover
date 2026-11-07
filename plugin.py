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
import json
import os
import random
import urllib.error
import urllib.parse
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
        base = (base_url or "").strip().rstrip("/")
        # 兼容历史配置里常见的 http:IP:PORT / https:IP:PORT 写法。
        if base.startswith("http:") and not base.startswith("http://"):
            base = "http://" + base[5:].lstrip("/")
        elif base.startswith("https:") and not base.startswith("https://"):
            base = "https://" + base[6:].lstrip("/")
        elif base and "://" not in base:
            base = "http://" + base
        self.base = base
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

    @staticmethod
    def _image_mime(data: bytes) -> str | None:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    def upload_image(self, item_id: str, img: bytes) -> tuple[bool, int, str]:
        """写回 Emby Primary 图并保留具体失败原因。

        Emby 的该接口接收 base64 文本，但 Content-Type 要保持原始图片 MIME。
        动态 GIF 可达数 MB，上传超时单独放宽，避免生成成功却在网络阶段误报。
        """
        mime = self._image_mime(img)
        if not mime:
            return False, 0, "无法识别生成图片格式"
        url = f"{self.base}/Items/{urllib.parse.quote(str(item_id), safe='')}/Images/Primary"
        payload = base64.b64encode(img)
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={
                "X-Emby-Token": self.key,
                "Content-Type": mime,
                "Content-Length": str(len(payload)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status = int(getattr(resp, "status", 0) or 0)
                return 200 <= status < 300, status, ""
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(1024).decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            return False, int(exc.code or 0), detail or str(exc.reason or exc)
        except Exception as exc:
            return False, 0, f"{type(exc).__name__}: {exc}"


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
        """读 load-libraries 动作写入的 storage 缓存。

        ⚠️ query 场景不允许 emby.request（实测 CONTEXT_DENIED），
        所以库列表只能由 action 写入缓存，query 只读缓存。
        页面上有「加载媒体库」按钮先点一次把缓存填上。
        """
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
        _sid = input.get("serverId")
        server_id = str(_sid) if _sid not in (None, "") else self._first_server_id(ctx)
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
        _sid = input.get("serverId")
        server_id = str(_sid) if _sid not in (None, "") else self._first_server_id(ctx)
        library_id = str(input.get("libraryId")) if input.get("libraryId") not in (None, "") else None
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
                            direct: EmbyDirect, count: int = 9,
                            source_mode: str = "random") -> tuple[list[str], int, list[str]]:
        """经 SDK 读同库条目（去重：作品名+年份只取一张），直连下载海报。

        返回 (图片 URL 列表, 库总数)。库总数用于角标显示——角标必须是
        媒体库的作品总数，不是取到的海报张数。
        """
        query = {
            "ParentId": library_id, "Recursive": True,
            "IncludeItemTypes": "Movie,Series,BoxSet",
            "Fields": "ProductionYear,ImageTags,BackdropImageTags,PremiereDate,DateCreated",
            "Limit": 300,
            "EnableTotalRecordCount": True,
        }
        # 最新入库交给 Emby 排序；随机模式拉取更大的候选池后在插件端洗牌。
        if source_mode == "latest":
            query.update({"SortBy": "DateCreated", "SortOrder": "Descending"})
        elif source_mode == "premiere":
            query.update({"SortBy": "PremiereDate", "SortOrder": "Descending"})
        result = ctx.mt.emby.request(
            server_id=server_id, path="/Items", query=query,
        ) or {}
        if result.get("status") != 200 or not isinstance(result.get("data"), dict):
            return [], 0, []
        try:
            total = int(result["data"].get("TotalRecordCount") or 0)
        except (TypeError, ValueError):
            total = 0
        items = list(result["data"].get("Items", []) or [])
        if source_mode == "random":
            random.SystemRandom().shuffle(items)
        seen: set[str] = set()
        picked: list[str] = []
        backdrops: list[str] = []
        for it in items:
            if not isinstance(it, dict) or not it.get("Id"):
                continue
            # 背景图：用同库条目的 Backdrop（和主海报分开，底图不再是封面同一张）
            bd_tags = it.get("BackdropImageTags") or []
            if bd_tags:
                backdrops.append(f"{direct.base}/Items/{it['Id']}/Images/Backdrop/0?MaxWidth=1280&Quality=90")
            if not (it.get("ImageTags") or {}).get("Primary"):
                continue
            key = f"{str(it.get('Name') or '').strip().lower()}|{it.get('ProductionYear') or ''}"
            if key in seen:
                continue
            seen.add(key)
            picked.append(direct.image_url(str(it["Id"])))
            if len(picked) >= count and len(backdrops) >= 3:
                break
        # 少数条目没有背景图时，不能让 backdrops 条件导致海报数量超过用户设置。
        # 背景不足由布局自行复用已有背景或主海报兜底。
        return picked[:count], (total or len(items)), backdrops[:max(count, 3)]

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
        _sid = input.get("serverId")
        server_id = str(_sid) if _sid not in (None, "") else self._first_server_id(ctx)
        item_id = str(input.get("itemId")) if input.get("itemId") not in (None, "") else None
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
    # 动作：保存简化页面的常用设置
    # ------------------------------------------------------------------ #
    @action("save-settings")
    def save_settings(self, input: dict, ctx: Context):
        allowed = (
            "sourceMode", "layout", "fontTitle", "fontSubtitle",
            "badgeStyle", "badgeFont", "badgeSize", "posterCount",
            "title", "subtitle", "titleSize", "subtitleSize", "titleColorMode", "titleColor",
            "subtitleColorMode", "subtitleColor", "badgeColorMode",
            "badgeTextColor", "badgeFollowTextColor",
        )
        # 支持页面中多个区域分别保存：仅更新这次提交的字段，保留其他已保存项。
        saved = _storage_value(ctx.mt.storage.get(key="ui-settings")) or {}
        if not isinstance(saved, dict):
            saved = {}
        saved.update({key: input.get(key) for key in allowed if key in input and input.get(key) is not None})
        ctx.mt.storage.set(key="ui-settings", value=saved)
        ctx.mt.log(level="info", message="当前生成设置已保存")
        return {"saved": True, "count": len(saved)}

    # ------------------------------------------------------------------ #
    # 动作：生成封面并上传到 Emby 条目
    # ------------------------------------------------------------------ #
    @action("generate-cover")
    def generate_cover(self, input: dict, ctx: Context):
        _sid = input.get("serverId")
        server_id = str(_sid) if _sid not in (None, "") else self._first_server_id(ctx)
        target_id = str(input.get("targetId")) if input.get("targetId") not in (None, "") else None
        layout = input.get("layout") or "经典九宫格"
        upload = bool(input.get("upload"))

        if not server_id:
            raise ValueError("请先在设置中授权 Emby 服务器")
        # 一键模式：不选条目时直接给媒体库生成封面（target = libraryId）
        library_id_early = str(input.get("libraryId")) if input.get("libraryId") not in (None, "") else None
        if not target_id:
            target_id = library_id_early
        if not target_id:
            # 兜底：action 场景可以调 emby.request，自动取第一个媒体库，
            # 保证一键生成永远不会因为没选库而失败
            try:
                lib_res = ctx.mt.emby.request(
                    server_id=server_id, path="/Library/SelectableMediaFolders") or {}
                lib_rows = lib_res.get("data") or [] if lib_res.get("status") == 200 else []
                first = next((r for r in lib_rows if isinstance(r, dict) and r.get("Id") is not None), None)
                if first:
                    target_id = str(first["Id"])
                    library_id_early = target_id
                    ctx.mt.log(level="warn", message=f"未选择媒体库，自动使用第一个：{first.get('Name')}")
                    try:
                        ctx.mt.storage.set(key="libraries", value=_to_options(lib_rows, "Name", "Id"))
                    except Exception:
                        pass
            except Exception as e:
                ctx.mt.log(level="warn", message=f"自动取媒体库失败: {e}")
        if not target_id:
            raise ValueError("请先选择媒体库")

        # 1. 标题自动取条目名/库名
        title = ""
        subtitle = ""
        item = ctx.mt.emby.request(
            server_id=server_id,
            path="/Items",
            query={"Ids": target_id, "Fields": "ProductionYear,CollectionType"},
        ) or {}
        if item.get("status") == 200 and isinstance(item.get("data"), dict):
            rows = item["data"].get("Items", [])
            if rows:
                row = rows[0]
                title = row.get("Name") or ""
                collection_type = str(row.get("CollectionType") or "").lower()
                # Emby 会把很多中文媒体库都归为 tvshows，必须先按库名细分。
                # 规则从更具体的复合名称开始，避免“日韩剧”被“韩剧”子串误判。
                normalized_name = str(title).strip().lower()
                exact_subtitles = {
                    "国漫": "CHINESE ANIMATION",
                    "儿童": "KIDS & FAMILY",
                    "日韩剧": "JAPAN & KOREA TV",
                    "综艺剧": "VARIETY SHOWS",
                    "纪录片": "DOCUMENTARIES",
                    "短剧": "SHORT DRAMAS",
                    "华语电影": "CHINESE MOVIES",
                    "外语电影": "INTERNATIONAL MOVIES",
                    "动画电影": "ANIMATED MOVIES",
                    "步兵": "UNCENSORED",
                    "骑兵": "CENSORED",
                    "中字": "CHINESE SUBTITLES",
                    "国产": "CHINESE CONTENT",
                    "fc2": "FC2 CONTENT",
                    "欧美": "WESTERN CONTENT",
                    "糖心vlog&玩偶姐姐": "CREATOR VIDEOS",
                    "日番": "JAPANESE ANIMATION",
                    "欧美漫": "WESTERN ANIMATION",
                    "特摄电影": "TOKUSATSU MOVIES",
                    "特摄剧": "TOKUSATSU SERIES",
                    "r级电影": "R-RATED MOVIES",
                    "日韩电影": "JAPANESE & KOREAN MOVIES",
                    "欧美剧": "WESTERN TV SERIES",
                    "港台剧": "HONG KONG & TAIWAN DRAMA",
                    "记录电影": "DOCUMENTARY FILMS",
                    "演唱会": "CONCERTS",
                    "ltx破解": "LTX MEDIA",
                }
                name_subtitles = (
                    (("国产动漫", "国产动画", "国漫"), "CHINESE ANIMATION"),
                    (("日本动漫", "日本动画", "日漫", "日番"), "JAPANESE ANIMATION"),
                    (("欧美动漫", "欧美动画", "欧美漫"), "WESTERN ANIMATION"),
                    (("动画电影",), "ANIMATED MOVIES"),
                    (("动漫", "动画", "anime"), "ANIMATION"),
                    (("国产剧", "国剧", "大陆剧"), "CHINESE DRAMA"),
                    (("日韩剧",), "JAPAN & KOREA TV"),
                    (("韩剧", "韩国剧"), "KOREAN DRAMA"),
                    (("日剧", "日本剧"), "JAPANESE DRAMA"),
                    (("美剧", "欧美剧", "英剧"), "WESTERN TV SERIES"),
                    (("纪录电影", "记录电影"), "DOCUMENTARY FILMS"),
                    (("纪录片", "纪录"), "DOCUMENTARIES"),
                    (("综艺",), "VARIETY SHOWS"),
                    (("演唱会",), "CONCERTS"),
                    (("电影",), "MOVIES"),
                )
                subtitle = exact_subtitles.get(normalized_name) or next(
                    (label for keywords, label in name_subtitles
                     if any(keyword in normalized_name for keyword in keywords)),
                    None,
                ) or {
                    "movies": "MOVIES",
                    "tvshows": "TV SHOWS",
                    "music": "MUSIC",
                    "boxsets": "COLLECTIONS",
                    "books": "BOOKS",
                    "homevideos": "VIDEOS",
                    "musicvideos": "MUSIC VIDEOS",
                    "photos": "PHOTOS",
                    "playlists": "PLAYLISTS",
                }.get(collection_type, "MEDIA LIBRARY")
                # 普通媒体条目仍用年份；媒体库封面使用媒体库英文类型。
                if not collection_type:
                    year = row.get("ProductionYear")
                    subtitle = str(year) if year else "MEDIA LIBRARY"
        if not title:
            title = "封面"

        # 2. 素材图（两种来源，Emby 优先，不需要映射本地目录）：
        #    a) Emby 模式：配置 embyUrl + embyApiKey 且传入 libraryId 时，
        #       从同库作品海报取图（参考 kunmini/mediatidy-cover-studio）。
        #       只需 emby.api 授权 + 勾选 Emby 服务器，无需 files.read/目录映射。
        #    b) 本地模式：files.read 授权的本地目录素材图（需要 roots 目录映射）。
        cfg = self._plugin_config(ctx)
        direct = EmbyDirect(input.get("embyUrl") or cfg.get("embyUrl") or "",
                            input.get("embyApiKey") or cfg.get("embyApiKey") or "")
        # 自动选库时 input 里没有 libraryId，用自动选中的库取图
        library_id = library_id_early or (str(input.get("libraryId")) if input.get("libraryId") not in (None, "") else None)
        poster_paths: list[str] = []
        backdrop_paths: list[str] = []
        library_total = 0
        if direct.ok and library_id:
            try:
                source_mode = str(input.get("sourceMode") or "random")
                poster_paths, library_total, backdrop_paths = self._fetch_emby_posters(
                    ctx, server_id, str(library_id), direct,
                    count=max(1, int(input.get("posterCount") or 9)),
                    source_mode=source_mode,
                )
                ctx.mt.log(level="info", message=f"取图模式={source_mode}，候选海报已选 {len(poster_paths)} 张")
            except Exception as e:
                ctx.mt.log(level="warn", message=f"Emby 海报获取失败，改用本地素材: {e}")
                poster_paths, library_total, backdrop_paths = [], 0, []

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
            # 背景图用真实 Backdrop（底图和主海报分开）
            "backdrops": backdrop_paths,
            "poster_backdrops": backdrop_paths,
            # 角标数字 = 媒体库作品总数（不是海报张数）
            "count": library_total or len(poster_paths),
        }
        # 先加载 FrameBase 随布局发布的原版默认预设，再用布局 schema 补齐预设
        # 未声明的键。这样布局、坐标、比例、阴影、模糊与上游默认效果保持一致。
        config: dict[str, Any] = self._load_layout_preset(layout)
        layout_module = self.engine().layout_modules.get(layout)
        if layout_module is not None and hasattr(layout_module, "get_schema"):
            try:
                for group in layout_module.get_schema() or []:
                    for item_schema in group.get("items", []) or []:
                        key = item_schema.get("key")
                        if key and "default" in item_schema and key not in config:
                            config[key] = item_schema["default"]
            except Exception as e:
                ctx.mt.log(level="warn", message=f"读取布局默认参数失败：{layout}；{e}")
        config.update({
            "engine": layout,
            "title": input.get("title") or title,
            "subtitle": input.get("subtitle") or subtitle,
            "font_title": input.get("fontTitle") or cfg.get("fontTitle") or config.get("font_title") or "OPPOSans-B.ttf",
            "force_uniform_title_font": bool(input.get("forceUniformTitleFont")),
            "font_subtitle": input.get("fontSubtitle") or cfg.get("fontSubtitle") or config.get("font_subtitle") or "OPPOSans-B.ttf",
            # 动态布局（文件名含"动态"）开启动画，引擎输出 GIF
            # 容器 worker 内存预算小，帧数必须压低，否则 worker 被 OOM 杀掉
            "enable_animation": "动态" in layout,
            "anim_duration": 100,
            # 默认取 9 张；页面可自行调整。旋转动态同样遵循所选数量。
            "poster_count": max(1, int(input.get("posterCount") or 9)),
            "switch_interval": 2500,
            "transition_ms": 900,
            "transition_spin": 38,
        })
        # 字号仅在调用方明确填写时覆盖；默认由每个布局自己的 schema 决定。
        if input.get("titleSize") not in (None, ""):
            config["title_size"] = _parse_size(input.get("titleSize"), 160)
        if input.get("subtitleSize") not in (None, ""):
            config["subtitle_size"] = _parse_size(input.get("subtitleSize"), 80)
        # 用户未设置字号时严格保留各 FrameBase 布局原版默认值；仅在页面明确
        # 输入字号时覆盖。长名称由公共换行逻辑处理，不再偷偷放大标题。
        # 自由设计依赖画布元素。简化页面不提供设计器时，使用可见的通用构图，
        # 而不是只输出一张空色背景。
        if layout == "自由设计" and not config.get("designer_elements"):
            config["designer_elements"] = [
                {"type": "backdrop", "x": 0, "y": 0, "w": 1920, "h": 1080,
                 "opacity": 100, "fit": "cover"},
                {"type": "poster", "assetIndex": 0, "x": 1110, "y": 120,
                 "w": 560, "h": 840, "radius": 42, "borderWidth": 5,
                 "borderColor": "#ffffff"},
                {"type": "text", "textSource": "title", "x": 145, "y": 350,
                 "w": 820, "h": 210, "font": config.get("font_title"),
                 "fontSize": 160, "color": "#ffffff", "align": "left"},
                {"type": "text", "textSource": "subtitle", "x": 150, "y": 550,
                 "w": 820, "h": 180, "font": config.get("font_subtitle"),
                 "fontSize": 64, "color": "#ffffff", "align": "left"},
            ]
        # 字体/角标/排版等可选配置：页面或插件 config 里填了才透传，
        # 引擎对不认识的布局会直接忽略多余键
        _extra_str = [
            "fontTitle", "fontSubtitle", "fontCount",
            "titleColor", "subtitleColor", "titleColorMode", "subtitleColorMode",
            "badgeStyle", "badgeFont", "badgeTextColor", "badgeBgColor", "badgeTextColorMode",
        ]
        _extra_num = [
            "badgeSize", "badgeXPercent", "badgeYPercent",
            "badgeOpacity", "badgeBorderOpacity",
            "titleOffsetY", "subtitleOffsetY",
            "posterCount", "brightness", "blurRadius",
            "fanRadius", "fanSpread", "centerScale", "sideScaleShrink",
            "layoutYOffset", "bgDarkness", "bgBlur",
        ]
        for key in _extra_str:
            value = input.get(key)
            if value not in (None, ""):
                # camelCase 入参 -> snake_case 引擎键
                snake = "".join(
                    f"_{c.lower()}" if c.isupper() else c for c in key
                ).lstrip("_")
                config[snake] = value
        badge_mode = input.get("badgeColorMode") or "follow"
        config["badge_follow_text_color"] = badge_mode == "follow"
        if badge_mode in ("solid", "random", "rainbow"):
            config["badge_text_color_mode"] = badge_mode
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
        #    SDK 的 emby.request JSON 体上限 256 KiB，封面 b64 通常超限，
        #    所以优先走 EmbyDirect 直连（参考 cover-studio）；没配 Key 才走 SDK。
        if upload:
            ctx.mt.log(level="info", message=f"上传封面到 Emby 条目 {target_id}")
            if direct.ok:
                ok, status, detail = direct.upload_image(str(target_id), image_bytes)
                result["uploaded"] = ok
                result["uploadStatus"] = status
                if ok:
                    ctx.mt.log(level="info", message=f"封面已上传到 Emby（直连，HTTP {status}）")
                else:
                    ctx.mt.log(
                        level="warn",
                        message=f"直连上传失败：HTTP {status or '无响应'}；{detail or '未知错误'}",
                    )
            else:
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

        # 4. 保存预览（宿主 storage 单值上限 256 KiB，完整封面 b64 常超限，
        #    存一张缩小预览图；失败就跳过，不影响生成结果）
        try:
            from io import BytesIO as _BytesIO
            from PIL import Image as _Image
            _img = _Image.open(_BytesIO(image_bytes))
            _img.thumbnail((480, 480))
            _buf = _BytesIO()
            _img.convert("RGB").save(_buf, format="JPEG", quality=60)
            _thumb_b64 = base64.b64encode(_buf.getvalue()).decode("utf-8")
            if len(_thumb_b64) <= 200 * 1024:
                ctx.mt.storage.set(key="preview", value={"b64": _thumb_b64, "size": len(_buf.getvalue())})
            else:
                ctx.mt.log(level="warn", message="预览图仍超 storage 上限，已跳过保存")
        except Exception as e:
            ctx.mt.log(level="warn", message=f"预览保存跳过: {e}")

        return result

    # ------------------------------------------------------------------ #
    @action("generate-all")
    def generate_all(self, input: dict, ctx: Context):
        """一键生成全部媒体库（参考 cover-studio 的 generate 动作）。

        遍历全部媒体库逐个生成并上传，返回汇总；单个库失败不中断整体。
        """
        server_id = self._first_server_id(ctx)
        if not server_id:
            raise ValueError("请先在设置中授权 Emby 服务器")
        result = ctx.mt.emby.request(
            server_id=server_id, path="/Library/SelectableMediaFolders") or {}
        rows = result.get("data") or [] if result.get("status") == 200 else []
        libs = [r for r in rows if isinstance(r, dict) and r.get("Id") is not None]
        if not libs:
            raise ValueError("未读取到媒体库")
        ctx.mt.log(level="info", message=f"开始批量生成，共 {len(libs)} 个媒体库")
        # 先用全部媒体库名称选出同一款可覆盖所有字符的字体。此前每个库独立
        # fallback，某些名称会切到另一字体，视觉字面高度不同，看起来一大一小。
        batch_input = dict(input or {})
        all_names = [str(lib.get("Name") or "") for lib in libs]
        chosen_title_font, title_missing = self.engine().resolve_font_filename_for_texts(
            batch_input.get("fontTitle") or "剑豪体.ttf", all_names, 96,
            return_missing=True,
        )
        if chosen_title_font:
            batch_input["fontTitle"] = chosen_title_font
            batch_input["forceUniformTitleFont"] = True
        # 英文副标题和数字角标也做同样的签名字形检查。当前语义副标题为 ASCII，
        # 但用户可换字体；提前锁定能避免未来某款字体缺少 &、数字或连字符。
        subtitle_samples = [
            "CHINESE DRAMA", "CHINESE ANIMATION", "KIDS & FAMILY",
            "JAPAN & KOREA TV", "VARIETY SHOWS", "DOCUMENTARIES",
            "SHORT DRAMAS", "CHINESE MOVIES", "INTERNATIONAL MOVIES",
            "ANIMATED MOVIES", "UNCENSORED", "CENSORED",
            "CHINESE SUBTITLES", "CHINESE CONTENT", "FC2 CONTENT",
            "WESTERN CONTENT", "CREATOR VIDEOS", "JAPANESE ANIMATION",
            "WESTERN ANIMATION", "TOKUSATSU MOVIES", "TOKUSATSU SERIES",
            "R-RATED MOVIES", "JAPANESE & KOREAN MOVIES",
            "WESTERN TV SERIES", "HONG KONG & TAIWAN DRAMA",
            "DOCUMENTARY FILMS", "CONCERTS", "LTX MEDIA", "MEDIA LIBRARY",
        ]
        # 加入用户可能输入的自定义副标题；没有自定义值时检查全部自动语义文本。
        requested_subtitle = str(batch_input.get("subtitle") or "").strip()
        subtitle_texts = [requested_subtitle] if requested_subtitle else subtitle_samples
        chosen_subtitle_font, subtitle_missing = self.engine().resolve_font_filename_for_texts(
            batch_input.get("fontSubtitle") or batch_input.get("fontTitle") or "OPPOSans-B.ttf",
            subtitle_texts, 64, return_missing=True,
        )
        chosen_badge_font, badge_missing = self.engine().resolve_font_filename_for_texts(
            batch_input.get("badgeFont") or batch_input.get("fontTitle") or "OPPOSans-B.ttf",
            ["0123456789"], 64, return_missing=True,
        )
        if chosen_subtitle_font:
            batch_input["fontSubtitle"] = chosen_subtitle_font
        if chosen_badge_font:
            batch_input["badgeFont"] = chosen_badge_font
        unresolved = {
            "标题": title_missing,
            "副标题": subtitle_missing,
            "角标": badge_missing,
        }
        for label, missing_chars in unresolved.items():
            if missing_chars:
                rendered = " ".join(
                    f"{char}(U+{ord(char):04X})" for char in missing_chars[:30]
                )
                ctx.mt.log(
                    level="warn",
                    message=f"{label}仍有未覆盖字形：{rendered}",
                )
        ctx.mt.log(level="info", message=(
            f"批量参数已锁定：布局={batch_input.get('layout') or '默认'}，"
            f"标题字体={batch_input.get('fontTitle') or '默认'}，"
            f"副标题字体={batch_input.get('fontSubtitle') or '默认'}，"
            f"角标字体={batch_input.get('badgeFont') or '默认'}，"
            f"海报数量={max(1, int(batch_input.get('posterCount') or 9))}"
        ))
        results: list[dict] = []
        for lib in libs:
            entry: dict[str, Any] = {"libraryId": str(lib["Id"]), "library": lib.get("Name") or ""}
            try:
                one = self.generate_cover({
                    **batch_input,
                    "serverId": server_id,
                    "libraryId": str(lib["Id"]),
                    "upload": True,
                }, ctx)
                entry.update({"status": "uploaded" if one.get("uploaded") else "generated",
                              "size": one.get("size", 0)})
            except Exception as e:
                entry.update({"status": "failed", "error": str(e)[:200]})
                ctx.mt.log(level="warn", message=f"媒体库 {entry['library']} 生成失败: {e}")
            results.append(entry)
            # 每生成完一个库就回收内存，避免 28 个库在同一 worker 里撑爆内存预算
            import gc as _gc
            _gc.collect()
        ok = sum(1 for r in results if r["status"] == "uploaded")
        return {"ok": True, "total": len(results), "uploaded": ok,
                "failed": len(results) - ok, "results": results}

    # ------------------------------------------------------------------ #
    @query("preview")
    def preview(self, input: dict, ctx: Context):
        return _storage_value(ctx.mt.storage.get(key="preview")) or {}

    # ------------------------------------------------------------------ #
    @query("templates")
    def templates(self, input: dict, ctx: Context):
        """模板列表：内置预设（FrameBase defaults/templates）+ 用户保存的。"""
        import json as _json
        import os as _os
        out: list[dict] = []
        tpl_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "templates")
        try:
            for name in sorted(_os.listdir(tpl_dir)):
                if not name.lower().endswith(".json"):
                    continue
                try:
                    data = _json.load(open(_os.path.join(tpl_dir, name), encoding="utf-8"))
                    out.append({"label": data.get("name") or name, "value": name})
                except Exception:
                    continue
        except OSError:
            pass
        for t in _storage_value(ctx.mt.storage.get(key="templates")) or []:
            if isinstance(t, dict) and t.get("name"):
                out.append({"label": str(t["name"]), "value": str(t["name"])})
        return out

    def _load_layout_preset(self, layout: str) -> dict:
        """按布局加载 FrameBase 随仓库提供的原版默认预设。"""
        tpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        try:
            for filename in sorted(os.listdir(tpl_dir)):
                if not filename.lower().endswith(".json"):
                    continue
                try:
                    with open(os.path.join(tpl_dir, filename), encoding="utf-8") as fh:
                        data = json.load(fh)
                    if str(data.get("engine") or "") == layout:
                        cfg = data.get("config")
                        return dict(cfg) if isinstance(cfg, dict) else {}
                except (OSError, ValueError, TypeError):
                    continue
        except OSError:
            pass
        return {}

    def _load_template(self, ctx: Context, name: str) -> dict:
        """按模板名加载配置：优先用户保存的，其次内置预设 JSON。"""
        import json as _json
        import os as _os
        if not name:
            return {}
        for t in _storage_value(ctx.mt.storage.get(key="templates")) or []:
            if isinstance(t, dict) and str(t.get("name")) == name:
                cfg = t.get("config")
                return dict(cfg) if isinstance(cfg, dict) else {}
        tpl_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "templates", name)
        try:
            data = _json.load(open(tpl_path, encoding="utf-8"))
            cfg = data.get("config")
            return dict(cfg) if isinstance(cfg, dict) else {}
        except (OSError, ValueError):
            return {}

    @action("save-template")
    def save_template(self, input: dict, ctx: Context):
        """保存当前页面参数为模板（对齐 FrameBase 的保存当前/另存为）。"""
        name = str((input or {}).get("name") or "").strip()
        if not name:
            raise ValueError("模板名不能为空")
        cfg = {k: v for k, v in (input or {}).items() if k not in ("name", "serverId", "libraryId", "upload")}
        saved = _storage_value(ctx.mt.storage.get(key="templates")) or []
        saved = [t for t in saved if not (isinstance(t, dict) and t.get("name") == name)]
        saved.append({"name": name, "config": cfg})
        ctx.mt.storage.set(key="templates", value=saved)
        return {"ok": True, "name": name}

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
        - 首次安装时先由 load-libraries 动作读取媒体库并写入缓存
        - 点「一键生成」即自动取海报、合成、上传，无需选条目/选文件/调字号
        """
        saved_ui = _storage_value(ctx.mt.storage.get(key="ui-settings")) or {}
        defaults = {
            "serverId": None,
            "libraryId": None,
            "sourceMode": "random",
            "layout": "扇形展开",
            "fontTitle": "剑豪体.ttf",
            "fontSubtitle": "剑豪体.ttf",
            "posterCount": 9,
            "title": "",
            "subtitle": "",
            "titleSize": None,
            "subtitleSize": None,
            "badgeStyle": "box",
            "badgeFont": "剑豪体.ttf",
            "badgeSize": 100,
            "titleColorMode": "solid",
            "titleColor": "#FFFFFF",
            "subtitleColorMode": "solid",
            "subtitleColor": "#FFFFFF",
            "badgeColorMode": "follow",
            "badgeTextColor": "#FFFFFF",
            "badgeFollowTextColor": True,
        }
        if isinstance(saved_ui, dict):
            for key in defaults:
                if key in saved_ui and saved_ui[key] is not None:
                    defaults[key] = saved_ui[key]
        # 0.4.2 曾提供 rainbow_gradient，现统一迁移成用户确认保留的彩虹闪烁。
        for key in ("titleColorMode", "subtitleColorMode", "badgeColorMode"):
            if defaults.get(key) == "rainbow_gradient":
                defaults[key] = "rainbow"
        return ui.page(
            ui.Card(
                ui.Text(text="Cover Studio · 封面创作。默认随机从目标媒体库取背景图和海报。"),
                ui.Select(label="服务器", bind="state.serverId",
                          options=ui.ref("query.servers.data")),
                ui.Button(label="加载 / 刷新媒体库", onClick=[{
                    "type": "invoke", "actionId": "load-libraries",
                    "input": {"serverId": ui.ref("state.serverId")},
                }]),
                ui.Select(label="媒体库", bind="state.libraryId",
                          options=ui.ref("query.libraries.data")),
                ui.Text(text="首次安装或切换服务器时，请先点“加载 / 刷新媒体库”；加载完成后媒体库下拉框会显示已授权服务器中的媒体库。"),
                ui.Select(label="取图模式", bind="state.sourceMode", options=[
                    {"label": "随机取图", "value": "random"},
                    {"label": "最新入库", "value": "latest"},
                    {"label": "最新发行", "value": "premiere"},
                ]),
                ui.Text(text="随机取图：从最多 300 个候选中重新洗牌；最新入库：按 DateCreated 倒序；最新发行：按 PremiereDate 倒序。"),
                ui.Select(label="封面布局", bind="state.layout", options=[
                    {"label": "经典九宫格", "value": "经典九宫格"},
                    {"label": "经典堆叠", "value": "经典堆叠"},
                    {"label": "经典平铺", "value": "经典平铺"},
                    {"label": "极简", "value": "极简"},
                    {"label": "海报墙", "value": "海报墙"},
                    {"label": "扇形展开", "value": "扇形展开"},
                    {"label": "斜线分割", "value": "斜线分割"},
                    {"label": "自由设计", "value": "自由设计"},
                    {"label": "旋转", "value": "旋转"},
                    {"label": "经典九宫格(动态)", "value": "经典九宫格(动态)"},
                    {"label": "经典堆叠(动态)", "value": "经典堆叠(动态)"},
                    {"label": "经典平铺(动态)", "value": "经典平铺(动态)"},
                    {"label": "扇形展开(动态)", "value": "扇形展开(动态)"},
                    {"label": "旋转(动态)", "value": "旋转(动态)"},
                    {"label": "聚焦(动态)", "value": "聚焦(动态)"},
                    {"label": "荣耀视界", "value": "荣耀视界"},
                    {"label": "聚焦C佬", "value": "聚焦C佬"},
                ]),
                ui.Text(text="点击后按钮会显示运行中，并自动切换到本次运行；静态封面通常数秒，动态封面约 15–40 秒。"),
                ui.Button(label="🚀 一键生成封面", onClick=[{
                    "type": "invoke", "actionId": "generate-cover",
                    "input": {
                        # 下拉框初次打开时 state 可能是 null。允许 null 传到动作，
                        # generate_cover 内部会自动选择首个已授权服务器/媒体库；用户
                        # 手动选择后则原样使用选择值。避免引用 query 数组下标——MT UI
                        # v2 的绑定路径语法不支持这种数组引用。
                        "serverId": ui.ref("state.serverId"),
                        "libraryId": ui.ref("state.libraryId"),
                        "sourceMode": ui.ref("state.sourceMode"),
                        "layout": ui.ref("state.layout"),
                        "fontTitle": ui.ref("state.fontTitle"),
                        "fontSubtitle": ui.ref("state.fontSubtitle"),
                        "badgeStyle": ui.ref("state.badgeStyle"),
                        "badgeFont": ui.ref("state.badgeFont"),
                        "badgeSize": ui.ref("state.badgeSize"),
                        "posterCount": ui.ref("state.posterCount"),
                        "title": ui.ref("state.title"),
                        "subtitle": ui.ref("state.subtitle"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "titleColorMode": ui.ref("state.titleColorMode"),
                        "titleColor": ui.ref("state.titleColor"),
                        "subtitleColorMode": ui.ref("state.subtitleColorMode"),
                        "subtitleColor": ui.ref("state.subtitleColor"),
                        "badgeColorMode": ui.ref("state.badgeColorMode"),
                        "badgeTextColor": ui.ref("state.badgeTextColor"),
                        "badgeFollowTextColor": ui.ref("state.badgeFollowTextColor"),
                        "upload": True,
                    },
                }]),
                ui.Button(label="🚀 一键生成全部媒体库", onClick=[{
                    "type": "invoke", "actionId": "generate-all",
                    "input": {
                        "sourceMode": ui.ref("state.sourceMode"),
                        "layout": ui.ref("state.layout"),
                        "fontTitle": ui.ref("state.fontTitle"),
                        "fontSubtitle": ui.ref("state.fontSubtitle"),
                        "badgeStyle": ui.ref("state.badgeStyle"),
                        "badgeFont": ui.ref("state.badgeFont"),
                        "badgeSize": ui.ref("state.badgeSize"),
                        "posterCount": ui.ref("state.posterCount"),
                        "title": ui.ref("state.title"),
                        "subtitle": ui.ref("state.subtitle"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "titleColorMode": ui.ref("state.titleColorMode"),
                        "titleColor": ui.ref("state.titleColor"),
                        "subtitleColorMode": ui.ref("state.subtitleColorMode"),
                        "subtitleColor": ui.ref("state.subtitleColor"),
                        "badgeColorMode": ui.ref("state.badgeColorMode"),
                        "badgeTextColor": ui.ref("state.badgeTextColor"),
                        "badgeFollowTextColor": ui.ref("state.badgeFollowTextColor"),
                    },
                }]),
            ),
            ui.Card(
                ui.Text(text="资源与内容。标题留空时：主标题自动使用媒体库名称，副标题自动使用媒体库英文类型。"),
                ui.Input(label="海报数量（奇数）", bind="state.posterCount", type="number"),
                ui.Input(label="主标题内容（留空自动）", bind="state.title"),
                ui.Input(label="副标题内容（留空自动）", bind="state.subtitle"),
                ui.Input(label="主标题大小（留空使用布局默认）", bind="state.titleSize", type="number"),
                ui.Input(label="副标题大小（留空使用布局默认）", bind="state.subtitleSize", type="number"),
                ui.Select(label="主标题颜色模式", bind="state.titleColorMode", options=[
                    {"label": "固定颜色", "value": "solid"},
                    {"label": "动态随机颜色", "value": "random"},
                    {"label": "彩虹闪烁", "value": "rainbow"},
                ]),
                ui.Input(label="主标题固定颜色（HEX）", bind="state.titleColor"),
                ui.Select(label="副标题颜色模式", bind="state.subtitleColorMode", options=[
                    {"label": "固定颜色", "value": "solid"},
                    {"label": "动态随机颜色", "value": "random"},
                    {"label": "彩虹闪烁", "value": "rainbow"},
                ]),
                ui.Input(label="副标题固定颜色（HEX）", bind="state.subtitleColor"),
                ui.Text(text="彩虹闪烁：同一帧文字同时显示红橙黄绿青蓝紫色带，动态时色带位置会逐帧轮换；随机颜色则整段文字一次只显示一种随机色。"),
                ui.Button(label="💾 保存资源与标题设置", onClick=[{
                    "type": "invoke", "actionId": "save-settings",
                    "input": {
                        "posterCount": ui.ref("state.posterCount"),
                        "title": ui.ref("state.title"),
                        "subtitle": ui.ref("state.subtitle"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "titleColorMode": ui.ref("state.titleColorMode"),
                        "titleColor": ui.ref("state.titleColor"),
                        "subtitleColorMode": ui.ref("state.subtitleColorMode"),
                        "subtitleColor": ui.ref("state.subtitleColor"),
                    },
                }]),
                ui.Text(text="这里保存后，海报数量、自定义主副标题和文字颜色会在刷新页面、重新打开插件及重启后恢复。"),
            ),
            ui.Card(
                ui.Text(text="字体与角标（其他位置、颜色和布局细节均使用所选布局的上游默认值）。"),
                ui.Select(label="主标题字体", bind="state.fontTitle",
                          options=ui.ref("query.fonts.data")),
                ui.Select(label="副标题字体", bind="state.fontSubtitle",
                          options=ui.ref("query.fonts.data")),
                ui.Text(text="角标显示媒体库作品总数。"),
                ui.Select(label="徽章样式", bind="state.badgeStyle", options=[
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
                    {"label": "不显示", "value": "none"},
                ]),
                ui.Select(label="徽章字体", bind="state.badgeFont",
                          options=ui.ref("query.fonts.data")),
                ui.Select(label="徽章文字颜色模式", bind="state.badgeColorMode", options=[
                    {"label": "跟随主标题颜色", "value": "follow"},
                    {"label": "固定颜色", "value": "solid"},
                    {"label": "动态随机颜色", "value": "random"},
                    {"label": "彩虹闪烁", "value": "rainbow"},
                ]),
                ui.Input(label="徽章文字固定颜色（HEX）", bind="state.badgeTextColor"),
                ui.Input(label="徽章大小", bind="state.badgeSize", type="number"),
                ui.Button(label="💾 保存当前设置", onClick=[{
                    "type": "invoke", "actionId": "save-settings",
                    "input": {
                        "sourceMode": ui.ref("state.sourceMode"),
                        "layout": ui.ref("state.layout"),
                        "fontTitle": ui.ref("state.fontTitle"),
                        "fontSubtitle": ui.ref("state.fontSubtitle"),
                        "badgeStyle": ui.ref("state.badgeStyle"),
                        "badgeFont": ui.ref("state.badgeFont"),
                        "badgeSize": ui.ref("state.badgeSize"),
                        "posterCount": ui.ref("state.posterCount"),
                        "title": ui.ref("state.title"),
                        "subtitle": ui.ref("state.subtitle"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "titleColorMode": ui.ref("state.titleColorMode"),
                        "titleColor": ui.ref("state.titleColor"),
                        "subtitleColorMode": ui.ref("state.subtitleColorMode"),
                        "subtitleColor": ui.ref("state.subtitleColor"),
                        "badgeColorMode": ui.ref("state.badgeColorMode"),
                        "badgeTextColor": ui.ref("state.badgeTextColor"),
                        "badgeFollowTextColor": ui.ref("state.badgeFollowTextColor"),
                    },
                }]),
                ui.Text(text="保存后刷新页面或下次打开插件时，会自动恢复这些字体、角标和生成设置。"),
            ),
            ui.RunHistory(),
            ui.RunLog(),
            queries=[
                {"id": "servers", "name": "servers"},
                {"id": "libraries", "name": "libraries"},
                {"id": "fonts", "name": "fonts"},
                {"id": "preview", "name": "preview"},
            ],
            state=defaults,
        )
