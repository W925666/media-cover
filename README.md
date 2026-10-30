# MediaTidy 封面工厂插件

复用 [FrameBase](https://github.com/980513Myb/framebase) 的 Pillow 封面渲染核心，
以 MediaTidy Python SDK v2 插件形式运行：从本地授权目录中的素材图片生成
Emby 封面，并可通过 Emby API 上传到目标条目。

## 功能

- **9 种布局模板**：经典九宫格、经典堆叠、经典平铺、极简、海报墙、扇形展开、
  斜线分割、自由设计、旋转（全部来自 FrameBase `defaults/layouts`）
- **内置字体**：OPPOSans-B、优设标题黑、仓耳舒圆体、方正行楷、Showcard Gothic、
  Elephant Italic（约 22 MiB，打包后 ZIP 约 13.8 MiB，低于宿主 40 MiB 上限）
- **标题/副标题、字号可配置**，主标题自动描边，副标题带装饰色块
- **本地素材**：通过文件选择器选取授权目录中的图片作为海报/背景
- **Emby 集成**：查询服务器、媒体库、条目；base64 上传封面到指定条目
- **预览**：生成结果保存到插件 KV，页面可刷新查看

## 目录结构

```
plugin.py          入口（Plugin 类：actions / queries / get_page）
plugin.json        清单（权限 emby.api / files.read / storage）
engine/            PosterEngine（移植自 FrameBase core/engine.py，
                   已去掉 requests/urllib3，素材仅支持本地路径）
layouts/*.py       布局模块（render(ctx, bg, config, assets, fonts)）
fonts/             内置字体
```

## 开发与打包

需要 Python 3.12 与 uv 0.6.7（SKILL 固定版本）：

```bash
uv sync --python 3.12
uv pip install --python .venv/bin/python "/path/to/mediatidy-plugin-development/references/sdk[dev]"
uv run --no-sync python -m mt_plugin check
uv run --no-sync python -m mt_plugin pack
```

输出：`dist/cover.factory-<版本>.zip`，通过 MediaTidy「插件中心 → 上传 ZIP」安装。

> 本机验证备注：若 SDK 以 `uv pip install` 装入 site-packages，mypy 会因
> `MYPYPATH` 指向 site-packages 而拒绝执行；此时用
> `PYTHONPATH=<解压的 Skill>/references/sdk .venv/bin/python -m mt_plugin pack`
> 让 pack 从源码位置加载即可。本仓库已用该方式通过 check 与 pack。

## 安装后需要的授权

| 授权 | 用途 |
| --- | --- |
| 调用 Emby API + 勾选服务器 | 查询媒体库/条目、上传封面 |
| 文件读取 + 素材目录 | 读取本地海报/背景素材 |
| 存储 | 保存最近一次封面预览 |

## 已验证 / 未验证

**已验证（开发机）**
- `mt_plugin check`：清单/装饰器一致、schema、mypy 类型、依赖锁
- `mt_plugin pack`：ZIP 13.8 MiB，21 个文件
- `PosterEngine.draw()` 真实渲染：9 个布局全部加载，经典九宫格
  1920×1080 封面输出正常（含中文字体、描边、装饰块）

**未验证（需真实 MediaTidy 宿主）**
- 宿主安装、依赖准备、页面渲染与授权流程
- `emby.request` 上传 base64 封面：Emby 官方接口要求 base64 body，
  但宿主对 `json` 字段的序列化方式文档未明确。插件已做兜底——
  上传失败时封面仍会生成并记录日志，可手动在 Emby 中设置
