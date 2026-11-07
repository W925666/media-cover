# MediaTidy 封面工厂

基于 [FrameBase](https://github.com/980513Myb/framebase) 原版 Pillow 渲染核心制作的 MediaTidy Python SDK v2 插件。
插件可直接从 Emby 媒体库取海报与背景图，生成 1920×1080 静态封面或动态 GIF，并自动上传为媒体库封面。

![扇形展开封面预览](docs/images/fan-layout.jpg)

## 封面预览

<table>
  <tr>
    <td width="50%"><img src="docs/images/fan-layout.jpg" alt="扇形展开"><br><b>扇形展开</b></td>
    <td width="50%"><img src="docs/images/rotate-layout.jpg" alt="旋转"><br><b>旋转</b></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/grid-layout.jpg" alt="经典九宫格"><br><b>经典九宫格</b></td>
    <td width="50%"><img src="docs/images/honor-layout.jpg" alt="荣耀视界"><br><b>荣耀视界</b></td>
  </tr>
</table>

更多原版预览图保留在 [`templates/`](templates/) 目录。

## 功能

- **17 种 FrameBase 布局**
  - 静态：经典九宫格、经典堆叠、经典平铺、极简、海报墙、扇形展开、斜线分割、自由设计、旋转、荣耀视界、聚焦C佬
  - 动态：经典九宫格、经典堆叠、经典平铺、扇形展开、旋转、聚焦
- **原版布局效果**：按布局自动加载 FrameBase `defaults/templates` 原版预设，保留坐标、字号、比例、阴影、圆角、模糊和旋转参数。
- **34 种内置字体**：保留 FrameBase 上游字体文件名和页面选项，字体经过体积优化以适配 MediaTidy 插件源。
- **三种取图方式**：
  - `随机取图`：从目标媒体库最多 300 个候选作品中重新洗牌，每次生成重新抽取。
  - `最新入库`：按 Emby `DateCreated` 倒序选取最近加入媒体库的作品。
  - `最新发行`：按 Emby `PremiereDate` 倒序选取发行日期最新的作品。
- **自动标题**：主标题留空时使用媒体库名称；副标题留空时根据媒体库名称和类型生成英文标签，例如 `国漫 → CHINESE ANIMATION`。
- **自定义内容优先**：用户填写的主标题、副标题、字体、角标样式、角标大小和海报数量会覆盖自动值。
- **自动上传**：生成结束后直接上传为所选 Emby 媒体库的 Primary 封面。
- **低内存动态 GIF**：逐帧渲染、立即调色板量化并释放 RGBA 帧，适配资源受限的 MediaTidy Worker。
- **一键批量生成**：支持为全部媒体库依次生成并上传封面。

## 安装

### 从 GitHub 插件源安装

在 MediaTidy 中打开：

```text
插件中心 → 从仓库安装
```

仓库地址填写：

```text
https://github.com/W925666/media-cover
```

分支使用：

```text
main
```

安装完成后配置并启用插件。

### 更新

仓库发布新版本后，在 MediaTidy 插件详情页点击“从仓库更新”，然后刷新插件页面。

## MediaTidy 配置

### 插件配置

| 配置 | 说明 |
| --- | --- |
| Emby 地址 | MediaTidy 容器可以访问的 Emby 地址，例如 `http://192.168.1.10:8096` |
| Emby API Key | Emby 后台创建的 API Key，用于直接下载海报和上传封面 |

### 插件授权

| 授权 | 用途 |
| --- | --- |
| `emby.api` | 查询服务器、媒体库、作品信息和上传封面 |
| Emby 服务器授权 | 指定插件允许操作的 Emby 实例 |
| `storage` | 缓存媒体库列表、最近预览和插件状态 |
| `files.read` | 可选；使用本地授权目录素材时读取图片 |

## 使用方法

1. 打开“封面工厂”插件页面。
2. 选择 Emby 服务器和媒体库。
3. 选择取图模式：`随机取图`、`最新入库`或`最新发行`。
4. 选择封面布局。
5. 设置海报数量，默认 `9`；建议使用奇数。
6. 主标题和副标题可留空，让插件自动填写。
7. 选择字体、徽章样式和徽章大小。
8. 点击“🚀 一键生成封面”。
9. 运行完成后，封面会自动上传到对应媒体库。

动态布局通常需要 15–40 秒，具体时间取决于 Worker 性能、海报下载速度和帧数。

## 取图逻辑

### 随机取图

插件从目标媒体库读取最多 300 个 Movie、Series 或 BoxSet 候选项目，使用系统随机源洗牌，然后按“作品名 + 年份”去重并选取指定数量的海报。每次运行都会重新洗牌。

### 最新入库

插件向 Emby 请求：

```text
SortBy=DateCreated
SortOrder=Descending
```

因此优先使用最近加入媒体库的作品，而不是按名称或项目 ID 排序。

### 最新发行

插件向 Emby 请求：

```text
SortBy=PremiereDate
SortOrder=Descending
```

因此优先使用首映或发行日期最新的作品。这个模式与“最新入库”不同：旧电影今天刚加入媒体库时，在“最新入库”中靠前，但在“最新发行”中仍按其原始发行日期排序。

## 原版 FrameBase 效果

本插件内部保留 FrameBase 的：

- `defaults/layouts/*.py` 布局实现
- `defaults/templates/*.json` 默认预设
- 布局依赖的光效、暗角、背景和麦穗素材
- 原始字体文件名与字体选择项

生成时会先加载对应布局的原版预设，再应用媒体库标题、用户输入、海报数量、字体和角标设置。因此默认构图尽量与 FrameBase 原版预览保持一致，同时适配 MediaTidy 的 Emby 取图与上传流程。

## 目录结构

```text
plugin.py          MediaTidy 插件入口、Emby 取图、生成和上传逻辑
plugin.json        插件清单、权限、动作与输入 Schema
engine/            FrameBase PosterEngine 与低内存动态 GIF 实现
layouts/           FrameBase 原版布局及布局资源
fonts/             内置字体
/templates         FrameBase 原版预设 JSON 与预览图
docs/images/       GitHub README 展示图片
tests/             渲染与引擎测试
```

## 本地测试

要求 Python 3.12 和 Pillow 11.1.0：

```bash
python3 tests/test_engine.py
```

测试内容包括：

- 所有布局可加载并渲染
- 未知布局安全回退
- 图片缓存像素预算与源图缩放
- 从任意工作目录读取默认资源

## 当前版本

```text
0.3.1
```

已在真实 MediaTidy + Emby 环境验证：

- 插件页面正常加载
- 随机取图连续运行会选择不同素材
- 最新入库和最新发行模式可正常生成
- 静态封面生成成功
- 动态 GIF 在受限 Worker 中生成成功
- 封面上传返回 Emby HTTP 204

## 致谢

- [FrameBase](https://github.com/980513Myb/framebase)：布局、预设、字体与 Pillow 渲染核心。
- [MediaTidy](https://github.com/murongyun8/MediaTidy)：插件宿主、Python SDK v2 与 Emby 能力接口。
