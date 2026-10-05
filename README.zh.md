<p align="center">
  <img src="docs/logo.png" width="128" height="128" alt="MOGRT Converter logo">
</p>

<h1 align="center">MOGRT Converter</h1>

<p align="center"><a href="README.md">English</a> · <a href="README.de.md">Deutsch</a> · <b>简体中文</b></p>

<p align="center">
  无需 Adobe 软件，即可填写并渲染 After Effects 模板（<code>.mogrt</code>）——输出带 Alpha 通道的 ProRes 4444，供 DaVinci Resolve 使用。
</p>

<p align="center">
  <a href="https://github.com/nielsfranke/MOGRT-Converter/releases/latest"><b>下载 macOS 版</b></a> ·
  <a href="#安装">安装</a> ·
  <a href="#使用方法">使用方法</a> ·
  <a href="#支持范围">支持范围</a>
</p>

<p align="center">
  <img src="docs/screenshot.png" width="900" alt="MOGRT Converter with a free Mixkit lower third, client collections, live preview and controls">
</p>

## 这是做什么的

字幕条、标题卡、片尾等素材常常是 After Effects 制作的动态图形模板。在 Premiere Pro 中你可以通过 *基本图形* 面板填写这些模板。但换到 DaVinci Resolve 之后就打不开了。

MOGRT Converter 直接读取模板，并用自带的渲染器还原 After Effects 的行为来渲染。既不需要 After Effects，也不需要 Premiere。你像在 Premiere 中一样填写控件、实时预览，然后渲染出带透明背景的片段，直接放到 Resolve 的时间线上。

- **实时预览**，支持播放、逐帧拖动、透明棋盘格和标题安全框。播放期间应用会在所有 CPU 核心上预渲染，因此即便模板很重，从第二遍循环开始也能流畅播放
- **全部模板控件**：文字、滑块、复选框、颜色、位置、缩放
- **修改时长**的方式与 Premiere 一致：片头和片尾（受保护区域）保持不变，仅调整中间部分
- **运动**属性与 Premiere 一致：位置、缩放、旋转和不透明度，也可以在预览中直接拖动图形
- **整理素材库**：合集（例如每个客户一个）、重命名、排序、从列表移除模板
- **批量渲染**：在应用内以表格形式，或从 Excel/Numbers 导出的 CSV 一次渲染多个变体
- **带 Alpha 通道的 ProRes 4444**（另有 4444 XQ、PNG-in-MOV 和 H.264），包含模板自带的音频
- **Resolve 集成**：一个脚本可将新片段导入媒体池中的 "MOGRTs" 素材夹
- **命令行**，可用于自动化
- **中文、英语和德语**：界面跟随系统语言，也可在左下角切换

## 安装

### macOS

1. 下载 [`MOGRT-Converter-0.6.4-macOS-arm64.dmg`](https://github.com/nielsfranke/MOGRT-Converter/releases/latest)（Apple Silicon）。
2. 打开 DMG，将 **MOGRT Converter** 拖入 *应用程序*。
3. 首次启动：右键点击应用 → **打开**。该应用未经 Apple 公证。

ffmpeg 和 Ghostscript（用于模板中的 EPS 标志）已内置，无需再安装其他东西。

### Windows 和 Linux

从[最新版本](https://github.com/nielsfranke/MOGRT-Converter/releases/latest)下载（x64，由 GitHub Actions 构建，测试程度低于 Mac 版）：

- **Windows：** `…-Setup.exe`（安装程序）或 `…-Windows-x64.zip`（免安装版）。需要 WebView2 运行时，Windows 10/11 通常已预装。
- **Linux：** 解压 `…-Linux-x64.tar.gz` 并运行 `./install.sh`。如需原生窗口，请安装 `sudo apt install gir1.2-webkit2-4.1`；否则界面会在浏览器中打开。

## 使用方法

1. **添加模板**：点击 *+ 文件夹* 或 *+ 文件*，直接拖入窗口，或双击 `.mogrt` 文件。
   **整理**：*+ 合集* 可新建合集，例如为某个需要反复使用的客户建一个。将模板拖到合集上，或通过右键（或 *⋯*）添加。同一菜单还可重命名模板，或将其*从列表中移除*（文件仍保留在磁盘上）。右键点击某一行文件夹可将其移除。可按名称、最近打开或最新文件排序。
2. **填写**：模板的控件会显示在右侧。输入时预览即时更新。空格键播放，方向键逐帧步进。
   *运动*可移动、缩放、旋转和淡入淡出整个图形——类似 Premiere 中片段的运动属性。最快的方法是用鼠标在预览中拖动图形（按住 Shift 键：仅垂直方向），例如为社交媒体把字幕条往上移。
3. **渲染**：选择格式、输出文件夹，并可选择设置新的*时长*（`8`、`8.5`、`00:00:08:12` 或 `200f`），然后点击*渲染*。默认输出到 `~/Movies/MOGRT Renders`。
4. **批量**：*批量 …* 会打开一个表格：每个文件一行，包含全部控件、文件名和时长。行可预览、复制，或通过*导入 CSV* 从 Excel 导入。*保存 CSV 模板* 会写出含全部列的对应表格。
5. **在 Resolve 中**：点击一次*安装 Resolve 集成*。之后 Resolve 的*工作区 → 脚本*下会出现两个条目：
   - **MOGRT Renders importieren** 将新片段导入 "MOGRTs" 素材夹。
   - **MOGRT Converter starten** 启动本应用。

**缺少字体**：如果模板使用了未安装的字体，应用会给出提示并使用替代字体渲染。*从 Google Fonts 下载*可自动获取免费字体（包括可变字体的单个字重）。商业字体请放入*打开字体文件夹*所打开的目录，然后重新打开模板。已内置 Montserrat 和 Source Sans Pro。

### 命令行

命令行的输出为德语。

```bash
mogrt info template.mogrt                                  # list controls and fonts
mogrt fonts template.mogrt --download                      # download missing free fonts from Google Fonts
mogrt still template.mogrt -t 2.5 -o preview.png           # single frame
mogrt render template.mogrt --set "Title=Jane Doe" --set "Subtitle=Head of Communications" -o lower_third
mogrt render template.mogrt -d 8 -o lower_third_8s                   # new duration (intro/outro kept)
mogrt render template.mogrt --offset 0,-300 -o lower_third_up        # 300 px higher (also --position, --motion-scale, --rotation, --opacity)
mogrt batch template.mogrt --template names.csv                       # CSV template with all columns
mogrt batch template.mogrt names.csv -o renders/                      # one file per row
```

**CSV 格式**：列标题为模板的控件名称（参见 `mogrt info`），另可选用 `filename` 和 `duration`。分隔符可用分号、逗号或制表符，编码为 UTF-8 或 Windows 编码。空单元格使用默认值。颜色写作 `#RRGGBB`，位置写作 `960 540`，复选框写作 `yes`/`no`。多行文字如同 Excel 中那样（Alt+Enter）或用 `\n`。

在 Mac 应用中，命令为 `"/Applications/MOGRT Converter.app/Contents/MacOS/MOGRT Converter"`；在 Windows 中为应用目录下的 `mogrt.exe`。
格式（`-f`）：`prores4444`（默认）、`prores4444xq`、`png-mov`、`h264`。

## 支持范围

<details>
<summary>图层、效果、表达式</summary>

- **形状图层**：路径、矩形、椭圆、星形、填充/描边、渐变、修边、圆角、合并路径、偏移、虚线、中继器、收缩和膨胀、锯齿
- **文字**：点文本和段落文本、带范围选择器的动画器（不透明度、位置、缩放、旋转、颜色、字距）
- **合成**：预合成、父子关系、时间重映射、轨道遮罩（Alpha/亮度）、包含模板/轮廓的混合模式、带摄像机的 2.5D 图层、蒙版、调整图层
- **效果**：模糊（高斯、快速/方框、摄像机镜头、定向、CC Vector Blur）、颜色（填充、色调、三色调、曲线、色阶、曝光度、色相/饱和度、亮度和对比度、转换通道、阈值、反转）、遮罩（设置遮罩、遮罩阻塞/简单阻塞、最小/最大）、扭曲（湍流置换、置换图、波形变形、网格变形、边角定位、镜像、偏移、毛边、分散、CC Cylinder、CC Glass、CC Blobbylize、CC HexTile）、生成（渐变填充、四色渐变、分形和湍流杂色、网格、CC Light Sweep、Vegas）、过渡（线性、径向、百叶窗、卡片擦除）、粒子（CC Particle Systems II、CC Particle World、CC Glue Gun）、残影、色调分离时间、发光、投影、浮雕、查找边缘、锐化
- **图层样式**：颜色叠加、渐变叠加、描边、投影、外发光、内阴影、内发光、斜面和浮雕
- **素材**：纯色、图像、视频、Illustrator/PDF、EPS（需 Ghostscript）、音频
- **表达式**通过内置 JavaScript 引擎执行：`sourceRectAtTime`、`wiggle`、`effect()`、`content()`、`thisComp.layer()`、`loopOut`、`posterizeTime`、标记、`linear`/`ease`、向量运算

</details>

**限制：**
- 仅支持在 After Effects 中制作的 MOGRT。尚不支持在 Premiere Pro 中创建的模板。
- 不还原第三方插件（Trapcode、Element 3D 等）和真正的 3D 内容。
- 未知的效果或表达式会被跳过。应用会在控制台打印警告。

## 工作原理

`.mogrt` 是一个 ZIP 归档，内含 After Effects 工程和一份控件描述。转换器用 [py-aep](https://github.com/forticheprod/py-aep) 读取工程，套用控件数值，并用 [Skia](https://skia.org) 渲染每一帧。文字由 HarfBuzz 排版，表达式在 QuickJS 中运行。最后由 ffmpeg 写出视频。

## 开发

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[build,dev]"
.venv/bin/mogrt app                    # interface in the browser (development mode)
.venv/bin/mogrt-converter              # interface in the native window
.venv/bin/python -m pytest tests       # unit tests; also renders every .mogrt in samples/
.venv/bin/mogrt inspect template.mogrt # print layers, keyframes and expressions
```

代码、注释和提交信息使用英文；应用文案以德语撰写，并在 `mogrt_converter/app/static/index.html` 中翻译（`EN` 表为英语，`ZH` 表为中文）。

### 测试语料库

`samples/` 和 `testdata/` 不属于本仓库，因为模板自带各自的许可。请将你自己的 `.mogrt` 文件放入 `samples/`，`tests/test_samples.py` 会逐一渲染。

如需更广泛的测试，可将任意数量的 MOGRT 放入 `testdata/`。`tools/corpus.py` 会全部渲染，收集不支持的效果、失败的表达式和缺失的字体，并与内置的预览视频比对：

```bash
MOGRT_DATA_DIR=testdata/appdata .venv/bin/python tools/corpus.py testdata -o out/corpus   # report: out/corpus/report.md
MOGRT_CORPUS=1 .venv/bin/python -m pytest tests/test_corpus.py
```

Mixkit 上有无需注册即可下载的免费模板，例如（请自行核对许可：可用，但不可再分发）。上方截图展示的也是 Mixkit 模板。

### 构建

```bash
.venv/bin/python packaging/build.py    # builds for the current system (macOS: .app + .dmg)
docker run --rm -v "$PWD":/src -w /src python:3.12-bookworm sh packaging/linux/build-in-docker.sh
```

构建过程会从官方源码编译精简版 Ghostscript（`packaging/ghostscript/build_gs.sh`；在 Windows 上则打包已安装的官方 Ghostscript）。内置的第三方软件及其许可：[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

PyInstaller 只能为当前运行的系统构建。工作流 `.github/workflows/build.yml` 在 GitHub Actions 上构建全部三个平台。你也可以手动触发：*Actions → build → Run workflow*；构建结果会出现在 workflow artifacts 中。

### 发布

1. 在 `pyproject.toml`、`mogrt_converter/__init__.py` 以及两份 README 中更新下载文件名处的版本号，然后提交。
2. 推送标签：`git tag -a v0.7.0 -m "MOGRT Converter 0.7.0" && git push origin v0.7.0`
3. 构建工作流会构建 macOS、Windows 和 Linux，并将文件附到该标签的 GitHub Release 上。如果还没有 Release，则创建一个并自动生成说明。你可以在此之前或之后撰写说明；同名文件会被覆盖。

## 许可证

MOGRT Converter 是基于 [GNU Affero 通用公共许可证 v3.0](LICENSE)（或更高版本）的自由软件。你可以自由使用、修改和分享它。如果你分发修改后的版本，或将其作为网络服务提供，你必须以相同的许可证公开源代码。

内置的第三方软件（Ghostscript、FFmpeg、字体、库文件）及其许可：[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

After Effects、Premiere Pro 和 Motion Graphics Templates 是 Adobe 的商标或格式。本项目与 Adobe 无关。

## 贡献翻译

界面文案以德语为源语言，英语和中文是翻译表。新增语言请参考 `ZH` 表的做法：

1. 在 `mogrt_converter/app/static/index.html` 中新增一张语言表，键为德语原文。
2. 把语言代码加入 `LANGS`，并在 `LANG_LABELS` 中填上该语言的显示名。
3. 在系统语言判定（`LANG`）中加上对应前缀。
4. 保持 `{0}`、`{1}` 等占位符不变。
5. 添加对应语言的 `README.xx.md`，并更新各 README 顶部的语言互链。
