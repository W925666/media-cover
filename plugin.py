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
from pathlib import Path
from typing import Any

from mt_plugin import Plugin as BasePlugin, Context, action, query
from mt_plugin import ui

from engine import PosterEngine

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def _to_options(items: list[dict], label_key: str, value_key: str) -> list[dict]:
    return [{"label": it.get(label_key) or "", "value": it.get(value_key)} for it in items]


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
        data = ctx.mt.storage.get(key="libraries")
        return data.get("value") or [] if data.get("found") else []

    # ------------------------------------------------------------------ #
    # 查询：条目列表（读 storage 缓存）
    # ------------------------------------------------------------------ #
    @query("items")
    def items(self, input: dict, ctx: Context):
        data = ctx.mt.storage.get(key="items")
        return data.get("value") or [] if data.get("found") else []

    # ------------------------------------------------------------------ #
    # 动作：加载媒体库（结果写入 storage）
    # ------------------------------------------------------------------ #
    @action("load-libraries")
    def load_libraries(self, input: dict, ctx: Context):
        server_id = input.get("serverId")
        if not server_id:
            raise ValueError("请先选择 Emby 服务器")
        result = ctx.mt.emby.request(
            server_id=server_id,
            path="/Library/SelectableMediaFolders",
        )
        if result["status"] != 200:
            raise ValueError(f"Emby 返回 HTTP {result['status']}")
        data = result["data"] or []
        opts = _to_options(data, "Name", "Id")
        ctx.mt.storage.set(key="libraries", value=opts)
        ctx.mt.log(level="info", message=f"已加载 {len(opts)} 个媒体库")
        return {"count": len(opts)}

    # ------------------------------------------------------------------ #
    # 动作：加载条目（结果写入 storage）
    # ------------------------------------------------------------------ #
    @action("load-items")
    def load_items(self, input: dict, ctx: Context):
        server_id = input.get("serverId")
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
        result = ctx.mt.emby.request(server_id=server_id, path="/Items", query=q)
        if result["status"] != 200:
            raise ValueError(f"Emby 返回 HTTP {result['status']}")
        items = (result["data"] or {}).get("Items", [])
        opts = []
        for it in items:
            name = it.get("Name") or ""
            year = it.get("ProductionYear")
            label = f"{name} ({year})" if year else name
            opts.append({"label": label, "value": it.get("Id")})
        ctx.mt.storage.set(key="items", value=opts)
        ctx.mt.log(level="info", message=f"已加载 {len(opts)} 个条目")
        return {"count": len(opts)}

    # ------------------------------------------------------------------ #
    # 动作：生成封面并上传到 Emby 条目
    # ------------------------------------------------------------------ #
    @action("generate-cover")
    def generate_cover(self, input: dict, ctx: Context):
        server_id = input.get("serverId")
        target_id = input.get("targetId")
        layout = input.get("layout") or "经典九宫格"
        title_size = int(input.get("titleSize") or 160)
        subtitle_size = int(input.get("subtitleSize") or 80)
        upload = bool(input.get("upload"))

        if not server_id:
            raise ValueError("请先选择 Emby 服务器")
        if not target_id:
            raise ValueError("请先选择目标条目")

        # 1. 标题自动取条目名
        title = ""
        subtitle = ""
        if target_id:
            item = ctx.mt.emby.request(
                server_id=server_id,
                path="/Items",
                query={"Ids": target_id, "Fields": "ProductionYear"},
            )
            if item["status"] == 200:
                rows = (item["data"] or {}).get("Items", [])
                if rows:
                    title = rows[0].get("Name") or ""
                    year = rows[0].get("ProductionYear")
                    subtitle = str(year) if year else ""
        if not title:
            title = input.get("title") or "封面"

        # 2. 素材图：本地授权目录中的图片
        root_id = input.get("rootId")
        asset_path = input.get("path") or "."
        poster_paths: list[str] = []
        if root_id:
            page = 0
            while True:
                res = ctx.mt.files.list(root_id=root_id, path=asset_path, page=page)
                for f in res.get("items", []):
                    if f.get("isDir"):
                        continue
                    name = f.get("name") or ""
                    if Path(name).suffix.lower() in IMAGE_EXTS:
                        local = ctx.download(f["ref"])
                        poster_paths.append(str(local))
                if not res.get("hasMore"):
                    break
                page += 1

        # 同时接受 FilePicker 直接选中的图片
        for f in ctx.files:
            if not f.get("isDir"):
                local = ctx.download(f["ref"])
                poster_paths.append(str(local))

        bg_path = poster_paths[0] if poster_paths else None
        assets = {
            "bg_url": bg_path,
            "posters": poster_paths,
            "backdrops": [],
            "poster_backdrops": [],
            "count": len(poster_paths),
        }
        config = {
            "engine": layout,
            "title": title,
            "subtitle": subtitle,
            "font_title": "OPPOSans-B.ttf",
            "font_subtitle": "OPPOSans-B.ttf",
            "title_size": title_size,
            "subtitle_size": subtitle_size,
            "enable_animation": False,
        }

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
            )
            result["uploadStatus"] = resp["status"]
            result["uploaded"] = resp["status"] in (200, 204)
            if result["uploaded"]:
                ctx.mt.log(level="info", message="封面已上传到 Emby")
            else:
                ctx.mt.log(level="warning", message=f"上传返回 HTTP {resp['status']}")

        # 4. 保存预览
        if len(image_bytes) <= 8 * 1024 * 1024:
            ctx.mt.storage.set(key="preview", value={"b64": b64, "size": len(image_bytes)})

        return result

    # ------------------------------------------------------------------ #
    @query("preview")
    def preview(self, input: dict, ctx: Context):
        data = ctx.mt.storage.get(key="preview")
        if not data.get("found"):
            return {}
        return data.get("value") or {}

    # ------------------------------------------------------------------ #
    def get_page(self, ctx: Context):
        return ui.page(
            ui.Card(
                ui.Text(text="选择 Emby 服务器、媒体库与条目，标题会自动取条目名。"),
                ui.Select(label="Emby 服务器", bind="state.serverId",
                          options=ui.ref("query.servers.data")),
                ui.Button(label="加载媒体库", onClick=[{
                    "type": "invoke", "actionId": "load-libraries",
                    "input": {"serverId": ui.ref("state.serverId")},
                }]),
                ui.Select(label="媒体库", bind="state.libraryId",
                          options=ui.ref("query.libraries.data")),
                ui.Button(label="加载条目", onClick=[{
                    "type": "invoke", "actionId": "load-items",
                    "input": {
                        "serverId": ui.ref("state.serverId"),
                        "libraryId": ui.ref("state.libraryId"),
                    },
                }]),
                ui.Select(label="目标条目", bind="state.targetId",
                          options=ui.ref("query.items.data")),
            ),
            ui.Card(
                ui.Text(text="素材与样式：用文件选择器直接选素材图片（可多选）。"),
                ui.FilePicker(),
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
                ui.Input(label="标题字号", bind="state.titleSize", type="number"),
                ui.Input(label="副标题字号", bind="state.subtitleSize", type="number"),
                ui.Button(label="生成并上传封面", onClick=[{
                    "type": "invoke", "actionId": "generate-cover",
                    "input": {
                        "serverId": ui.ref("state.serverId"),
                        "targetId": ui.ref("state.targetId"),
                        "layout": ui.ref("state.layout"),
                        "titleSize": ui.ref("state.titleSize"),
                        "subtitleSize": ui.ref("state.subtitleSize"),
                        "upload": True,
                    },
                }]),
            ),
            ui.RunHistory(),
            ui.RunLog(),
            queries=[
                {"id": "servers", "name": "servers"},
                {"id": "libraries", "name": "libraries"},
                {"id": "items", "name": "items"},
            ],
            state={
                "serverId": None,
                "libraryId": None,
                "targetId": None,
                "layout": "经典九宫格",
                "titleSize": 160,
                "subtitleSize": 80,
            },
        )
