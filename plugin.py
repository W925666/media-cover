"""MediaTidy 封面工厂插件。

复用 FrameBase 的 Pillow 封面渲染核心，走 MediaTidy 宿主能力：
- emby.request    查询媒体库/条目、上传封面（base64）
- files.* / ctx    读取本地授权目录中的素材图片
- storage          保存预览与配置草稿
- tasks            后台生成与续接

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


class Plugin(BasePlugin):
    """封面工厂插件实例。"""

    # 常驻实例字段（进程重启会丢失，跨重启状态放 storage）
    def on_init(self, ctx: Context) -> None:
        self._engine: PosterEngine | None = None
        ctx.mt.log(level="info", message="封面工厂插件已初始化")

    # ------------------------------------------------------------------ #
    # 引擎懒加载
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
    # 查询：Emby 服务器（只读，可放 page/query）
    # ------------------------------------------------------------------ #
    @query("servers")
    def servers(self, input: dict, ctx: Context):
        return ctx.mt.emby.servers.list()

    # ------------------------------------------------------------------ #
    # 动作：列出媒体库
    # ------------------------------------------------------------------ #
    @action("list-libraries")
    def list_libraries(self, input: dict, ctx: Context):
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
        return {"libraries": data}

    # ------------------------------------------------------------------ #
    # 动作：搜索条目（供用户选择生成封面目标）
    # ------------------------------------------------------------------ #
    @action("search-items")
    def search_items(self, input: dict, ctx: Context):
        server_id = input.get("serverId")
        library_id = input.get("libraryId")
        term = input.get("term") or ""
        if not server_id:
            raise ValueError("请先选择 Emby 服务器")
        query: dict[str, Any] = {
            "Recursive": True,
            "IncludeItemTypes": "Movie,Series,BoxSet",
            "Fields": "ProviderIds,ProductionYear,ImageTags",
            "Limit": 50,
        }
        if library_id:
            query["ParentId"] = library_id
        if term:
            query["SearchTerm"] = term
        result = ctx.mt.emby.request(
            server_id=server_id, path="/Items", query=query
        )
        if result["status"] != 200:
            raise ValueError(f"Emby 返回 HTTP {result['status']}")
        items = (result["data"] or {}).get("Items", [])
        return {
            "items": [
                {
                    "id": it.get("Id"),
                    "name": it.get("Name"),
                    "type": it.get("Type"),
                    "year": it.get("ProductionYear"),
                }
                for it in items
            ]
        }

    # ------------------------------------------------------------------ #
    # 动作：生成封面（预览或正式上传统一走这里，由 input.upload 区分）
    # ------------------------------------------------------------------ #
    @action("generate-cover")
    def generate_cover(self, input: dict, ctx: Context):
        server_id = input.get("serverId")
        target_id = input.get("targetId")
        title = input.get("title") or ""
        subtitle = input.get("subtitle") or ""
        layout = input.get("layout") or "经典九宫格"
        font_title = input.get("fontTitle") or "OPPOSans-B.ttf"
        font_subtitle = input.get("fontSubtitle") or "OPPOSans-B.ttf"
        title_size = int(input.get("titleSize") or 160)
        subtitle_size = int(input.get("subtitleSize") or 80)
        upload = bool(input.get("upload"))

        # 素材：本地图片文件引用（由 FilePicker 选中，落在 ctx.files）
        image_files = [f for f in ctx.files if not f.get("isDir")]
        poster_paths: list[str] = []
        bg_path: str | None = None
        for f in image_files:
            local = ctx.download(f["ref"])
            poster_paths.append(str(local))
        if poster_paths:
            bg_path = poster_paths[0]

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
            "font_title": font_title,
            "font_subtitle": font_subtitle,
            "title_size": title_size,
            "subtitle_size": subtitle_size,
            "enable_animation": False,
        }

        ctx.mt.log(level="info", message=f"开始生成封面：layout={layout}, title={title}")
        engine = self.engine()
        b64 = engine.draw(config, assets)
        image_bytes = base64.b64decode(b64)

        # 落盘到工作目录
        out = Path(ctx.work_dir) / "cover.jpg"
        out.write_bytes(image_bytes)
        ctx.mt.log(level="info", message=f"封面已生成，大小 {len(image_bytes)} 字节")

        result: dict[str, Any] = {"size": len(image_bytes)}

        # 上传到 Emby 目标（尽力而为，失败不影响落盘）
        if upload and server_id and target_id:
            ctx.mt.log(level="info", message=f"正在上传封面到 Emby 条目 {target_id}")
            resp = ctx.mt.emby.request(
                server_id=server_id,
                method="POST",
                path=f"/Items/{target_id}/Images/Primary",
                json=b64,
            )
            result["uploadStatus"] = resp["status"]
            result["uploaded"] = resp["status"] in (200, 204)
            if not result["uploaded"]:
                ctx.mt.log(
                    level="warning",
                    message=f"Emby 上传返回 HTTP {resp['status']}，封面已落盘可手动设置",
                )
            else:
                ctx.mt.log(level="info", message="封面已上传到 Emby")

        # 保存预览供页面展示（限制在 8 MiB 内）
        if len(image_bytes) <= 8 * 1024 * 1024:
            ctx.mt.storage.set(key="preview", value={"b64": b64, "size": len(image_bytes)})

        return result

    # ------------------------------------------------------------------ #
    # 查询：最新预览元信息
    # ------------------------------------------------------------------ #
    @query("preview")
    def preview(self, input: dict, ctx: Context):
        data = ctx.mt.storage.get(key="preview")
        if not data.get("found"):
            return {}
        return data.get("value") or {}

    # ------------------------------------------------------------------ #
    # 页面
    # ------------------------------------------------------------------ #
    def get_page(self, ctx: Context):
        return ui.page(
            ui.Card(
                ui.Text(text="选择 Emby 服务器，然后生成封面。"),
                ui.Select(
                    label="Emby 服务器",
                    bind="state.serverId",
                    options=ui.ref("query.servers.data"),
                ),
            ),
            ui.Card(
                ui.Text(text="标题与样式"),
                ui.Input(label="主标题", bind="state.title"),
                ui.Input(label="副标题", bind="state.subtitle"),
                ui.Select(
                    label="布局模板",
                    bind="state.layout",
                    options=[
                        {"label": "经典九宫格", "value": "经典九宫格"},
                        {"label": "经典堆叠", "value": "经典堆叠"},
                        {"label": "经典平铺", "value": "经典平铺"},
                        {"label": "极简", "value": "极简"},
                        {"label": "海报墙", "value": "海报墙"},
                        {"label": "扇形展开", "value": "扇形展开"},
                        {"label": "斜线分割", "value": "斜线分割"},
                        {"label": "自由设计", "value": "自由设计"},
                        {"label": "旋转", "value": "旋转"},
                    ],
                ),
                ui.Input(label="标题字号", bind="state.titleSize", type="number"),
                ui.Input(label="副标题字号", bind="state.subtitleSize", type="number"),
                ui.FilePicker(),
                ui.Button(
                    label="生成封面",
                    onClick=[{
                        "type": "invoke",
                        "actionId": "generate-cover",
                        "input": {
                            "serverId": ui.ref("state.serverId"),
                            "title": ui.ref("state.title"),
                            "subtitle": ui.ref("state.subtitle"),
                            "layout": ui.ref("state.layout"),
                            "titleSize": ui.ref("state.titleSize"),
                            "subtitleSize": ui.ref("state.subtitleSize"),
                            "upload": False,
                        },
                    }],
                ),
            ),
            ui.RunHistory(),
            ui.RunLog(),
            queries=[{"id": "servers", "name": "servers"}],
            state={
                "serverId": None,
                "title": "",
                "subtitle": "",
                "layout": "经典九宫格",
                "titleSize": 160,
                "subtitleSize": 80,
            },
        )
