# ScribeForge 新手使用指南（Windows 优先）

这份指南面向第一次使用 Git、Python、Flutter 和命令行的用户。ScribeForge 会在本机对扫描书籍进行 OCR，保留每页原始读取与复核证据，并允许你明确记录人工决定。**未经复核的 OCR 候选不等于最终定稿。**

## 1. 先了解你要做的事情

常见工作流程如下：

1. 准备扫描版 PDF 或页面图像。
2. 安装 ScribeForge Python CLI，并完成一次私有 OCR 运行时配置。
3. 在 Flutter 桌面工作台导入书籍，运行可恢复的双引擎处理。
4. 按页查看 OCR / VLM 候选与图像证据，明确记录人工决定。
5. 从已接受的内容导出 EPUB 3 或 DOCX。

Flutter 工作台负责驱动导入、处理、复核和导出；Python CLI 负责 OCR、证据存储与输出。Windows 是当前优先验证的桌面环境。

## 2. 安装基础软件

请安装：

- **Git**：用于下载仓库。
- **Python 3.11（参考版本）或 Python 3.14.4（核心/CLI 已由 CI 验证）**：用于 ScribeForge 后端。
- **Flutter 3.47.7 稳定版工具链**：用于运行桌面端。
- Windows 11 用户建议启用 **Developer Mode（开发者模式）**，因为 PDF 组件的原生构建可能需要创建符号链接。

安装后打开 PowerShell，运行以下命令确认工具可用：

```powershell
git --version
py -3.14 --version
# 若使用参考版本 Python 3.11：py -3.11 --version
flutter --version
flutter doctor
```

这里的 CI 验证版本是 Python 3.14.4；`py -3.14` 会选择本机已安装的 Python 3.14。若没有该版本，可安装 Python 3.11 并将下文命令里的 `-3.14` 换成 `-3.11`，同时在安装程序中启用 Python Launcher。运行 `flutter doctor` 时，优先处理 Windows Desktop 构建工具的报错。ScribeForge 核心/CLI 使用当前虚拟环境；OCR 引擎由 `scribeforge setup` 安装到独立的 Python 3.12 运行环境，详见[模型运行环境](./model-environments.md)。

## 3. 下载代码并安装 Python CLI

在 PowerShell 中执行：

```powershell
git clone https://github.com/BrainEno/ScribeForge.git
cd ScribeForge

py -3.14 -m venv .venv # Python 3.11 也可：将 -3.14 换成 -3.11
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

安装完成后，确认命令能够运行：

```powershell
scribeforge --help
```

如果提示找不到命令，先确认当前 PowerShell 前面是否显示 (.venv)。也可以直接运行仓库虚拟环境中的可执行文件：

```powershell
.\.venv\Scripts\scribeforge.exe --help
```

请在**仓库根目录**执行 Python 安装命令，不要在 clients\flutter_desktop 中执行。

## 4. 首次配置 OCR 运行环境

执行：

```powershell
scribeforge setup
```

首次设置会下载或安装应用管理的运行时、OCR 依赖和模型，过程可能较长，需要网络连接与足够磁盘空间。运行时默认放在当前用户的应用数据目录，不会把模型环境写进每本书的项目目录。直接运行 CLI 命令会打开旧版 Tk 配置窗口；在 Flutter 工作台中请前往 **设置 → 运行时配置**，使用原生向导查看持续进度并按后端修复。

若命令不在 PATH 中，可以使用：

```powershell
.\.venv\Scripts\scribeforge.exe setup
```

安装中断时，查看窗口中的失败步骤与错误信息后重试。不要直接删除项目数据库或源文件来修复运行环境。

## 5. 对一本书运行 OCR

假设源文件是 D:\Books\My Book.pdf：

```powershell
scribeforge process "D:\Books\My Book.pdf"
```

默认会在同一目录创建 My Book.scribeforge，其中保存 SQLite 项目数据库、页面图像与处理证据。若希望显式指定项目目录：

```powershell
scribeforge process "D:\Books\My Book.pdf" --store "D:\Books\My Book.scribeforge"
```

处理进度和最终统计会显示在终端。每次 OCR 页面调用默认有 600 秒期限，可用 `--worker-timeout SECONDS` 调整；worker 输出与日志有界保存。超时会终止 worker 及其子进程，将对应阶段标记为失败并允许重试。Flutter 工作台处理期间可点击 **取消处理**；取消会停止 OCR 进程树，已导入页图、已完成的 OCR 证据和人工决策均保留。退出码的含义：

- 0：全部页面处理成功。
- 1：至少部分页面失败；任务可重试。
- 2：遇到致命配置或输入问题，例如源文件不存在、格式不支持、运行环境未准备好。
- 130：用户取消处理；未完成阶段可重试。

该任务支持按页面恢复。部分页面失败或用户取消后，可再次运行同一条命令；修复了缺失的运行环境后也可以重跑，不需要先删除整个项目目录。

查看处理状态：

```powershell
scribeforge status --store "D:\Books\My Book.scribeforge"
```

## 5.1 检查项目是否完整

在重新运行 OCR、归档或迁移项目之前，可以执行只读审计：

```powershell
scribeforge audit --store "D:\Books\My Book.scribeforge" --json
```

审计会检查 SQLite 完整性、外键引用、源文件是否存在及其 SHA-256 是否与记录一致，以及每一页导入图像是否仍然存在。源 PDF 不在原路径或哈希不匹配时会给出警告；数据库损坏、外键错误或页面图像缺失会报告错误。审计不会修复数据库、重写路径或删除证据。

退出码：0 表示未发现问题；1 表示审计完成但发现需要处理的事项；2 表示无法读取项目数据库。输出 JSON 适合保存为诊断记录。

## 5.2 备份与恢复项目

建议在 OCR 完成、没有运行中的任务时创建备份：

```powershell
scribeforge backup --store "D:\Books\My Book.scribeforge" --out "D:\Backups\My Book.zip"
```

备份会包含项目数据库、项目目录中的页面图像和其他文件，并将原始 PDF 一并打包，但前提是 PDF 存在且 SHA-256 与项目记录一致。若源文件已移动，先用 `scribeforge relink-source` 重新关联；若页面图像缺失、数据库有完整性错误或仍有任务正在运行，备份会拒绝创建。已有 ZIP 默认不会覆盖，只有明确提供 `--overwrite` 才会替换。

先验证备份：

```powershell
scribeforge verify-backup --backup "D:\Backups\My Book.zip"
```

恢复到一个**尚不存在**的新目录：

```powershell
scribeforge restore --backup "D:\Backups\My Book.zip" --to "D:\Books\My Book-restored.scribeforge"
```

恢复前会重新验证清单、每个文件的 SHA-256、SQLite 完整性和外键。恢复不会覆盖现有目录；数据库中的页面图像路径和原始 PDF 路径会更新到恢复位置，OCR 与校对证据仍然保留。恢复后再运行 `scribeforge audit --store "D:\Books\My Book-restored.scribeforge" --json` 检查项目状态。

## 6. 生成两份候选 EPUB

OCR 完成后执行：

```powershell
scribeforge export-candidates --store "D:\Books\My Book.scribeforge" --out-dir "D:\Books\My Book-comparison-candidates"
```

成功后通常会得到：

- candidate-mineru.epub
- candidate-paddleocr.epub
- candidate-manifest.json

Manifest 会记录源文件哈希、引擎版本、每页的 OCR 运行 ID 和候选文件哈希。若某个引擎缺少页面结果，导出会报错，而不是静默生成一本缺页的候选书。

这些 EPUB 是**未校对的原始候选**，只用于比较。最终已校对的 EPUB/DOCX 应通过 scribeforge export 导出；该命令会阻止尚有未解决校对项的项目被误当作完整成品。

## 6.1 处理被拦下的行与空白页

导出被拒绝时，命令会逐条说明原因，例如：

```
error: cannot export unresolved content:
  1 unresolved pair(s):
    page 0 pair 0: unrepresentable_text (U+FFFD)
    inspect with 'scribeforge review'
    decide with 'scribeforge resolve --page N --pair M --text ...'
```

这表示两个引擎虽然“读得一样”，但读出来的内容机器不能签字（替换字符、控制字符、
置信度过低、或证据质量风险过高）。处理方式：

```powershell
scribeforge review --store "D:\Books\My Book.scribeforge"
scribeforge resolve --store "D:\Books\My Book.scribeforge" --page 0 --pair 0 --text "鸦"
```

如果整页都没有可识别文字（空白页、图版），除非测量证明页面上什么都没有，否则必须显式确认：

```powershell
scribeforge confirm --store "D:\Books\My Book.scribeforge" --page 5 --kind blank --reason "章节之间的空白页"
scribeforge confirm --store "D:\Books\My Book.scribeforge" --page 12 --kind non_text --reason "无说明文字的图版"
```

导出被拒时会给出每页的实测墨迹，用它判断"漏识别"还是"真空白"：

```
  2 page(s) with no text at all, not yet confirmed as textless:
    page 7: measured ink 6.93% — there is content here that was not read
    page 12: measured ink 0.03% — nothing on the page
```

墨迹低于 1% 的页面会被结构检测自动确认为空白（记录为 `evidence`），不需要人工逐页点击；
6.93% 这种说明页面上有大量内容却没被读出来，正确做法是重跑该页而不是确认空白。

同时可以查看这本书的结构（页面角色、印刷页码、章节树）：

```powershell
scribeforge structure --store "D:\Books\My Book.scribeforge" --json
```

`process` 结束后也会打印一行摘要，例如
`structure: pages 562, labelled 498, chapters 12 from toc (contents lists 14, 12 anchored); blank 9, plate 3`。
章节来自目录页（`toc`）比来自单页排版（`heading`）更可靠；无法锚定的目录条目会如实列出，
不会被猜着放进目录。

确认记录会追加保存（谁、何时、为什么），导出时也会写进报告。若某页其实是识别失败
而非空白，不要使用 confirm，而应重跑该页。

导出后可以查看这本书有多少内容由机器自动接受：

```powershell
scribeforge export --store "D:\Books\My Book.scribeforge" --out "D:\Books\My Book.epub" --json
scribeforge export --store "D:\Books\My Book.scribeforge" --out "D:\Books\My Book.epub" --strict-acceptance
```

`--json` 报告包含 `machine_accepted`、`human_accepted`、`max_machine_content_risk`、
`requires_human_review` 和抽查清单 `spot_check`。`--strict-acceptance` 会在仍存在
需要抽查的机器接受行时拒绝导出，适合要求人工逐行确认的交付场景。详见
[自动接受闸门](./acceptance-gate.md)。

### 导出的 EPUB 是"一本书"，不是"一叠扫描页"

有章节树时，导出的 EPUB 默认**一章一个 XHTML 文档**（`--layout auto` 自动如此），
被翻页切断的段落会重新接成一段；每个扫描页在正文里留一个
`<span epub:type="pagebreak" id="page-N" title="印刷页码"/>` 标记，读者仍然可以按
印刷页码跳转。命令行会顺带报告这次合并了多少段：

```powershell
scribeforge export --store "D:\Books\My Book.scribeforge" --out "D:\Books\My Book.epub"
# layout: chapter — 12 document(s), 1180 paragraph(s)
# paragraphs continued across a page turn: 37
```

想拿导出文件与原扫描逐页对照时，改用逐页形状：

```powershell
scribeforge export --store "D:\Books\My Book.scribeforge" --out "D:\Books\My Book-scans.epub" --layout page
```

`--layout` 只影响 EPUB；DOCX 仍然一张扫描页一次分页，适合与原书并排核对。
合并判据（上一页末行未收句且不短行、下一页首行不缩进）不是万无一失，
遇到排版古怪的页面仍可能多接或少接一段；见 [导出](./export.md)。

> 目前合并判据只在夹具和两页真实扫描上验证过，**没有在整本书上量过合并率**。
> 若你在一本真实书上发现段落被接错，请以 `--layout page` 的逐页版本为准。

### 封面和插图会一起进 EPUB

重排只能搬运文字，**画出来的东西**要单独处理。封面和插图页没有文字，导出默认会带上它们：

- 扫描的**第一页如果是一整页图画**，就会被判定为封面，EPUB 里同时写入
  `properties="cover-image"` 与 `<meta name="cover">`，书架和阅读器都能显示；
- 其余"没有文字但有墨迹"的页会被判成 `plate`，**在确认之前挡住导出**：

```powershell
scribeforge confirm --store "D:\Books\My Book.scribeforge" --page 7 --kind non_text \
  --reason "整页插图，没有图注可读"
```

确认之后，这一页会以 `<figure><img .../></figure>` 出现在它自己的页码位置之后，
`alt` 只会写"封面"或"第 117 页的插图"（不编造图注）。导出报告会写明：

```
page images: 4 embedded (cover 1, plate 3)
```

图片写进 EPUB 时是**原始页图逐字节复制**，SHA-256 会随书记录并在导出后复核：
页图被替换或损坏，导出会拒绝而不是悄悄换一张图。确认过的**空白页不会**写图。

> DOCX 导出目前只写文字，不含页图；命令行会打印一条 warning。另外，
> "第一页的图就是封面"是位置先验：如果这本扫描没有拍到封面，第一张插图会被当成封面。

## 7. 用 Flutter 桌面工作台完成全流程

在仓库根目录执行：

```powershell
cd clients\flutter_desktop
.\tool\bootstrap_desktop.ps1
flutter run -d windows
```

首次启动会生成 Windows 桌面 runner 并拉取 Flutter 依赖。macOS/Linux 可分别运行 `sh tool/bootstrap_desktop.sh`，再使用相应平台的 `flutter run` 命令。桌面端负责组织工作流；Python CLI 与本机运行时仍需按第 3、4 节安装。目前尚未发布可直接安装的 Windows 预编译包；打包方向与发布验收条件见 [Windows 桌面分发方案](windows-desktop-distribution.md)。

### 7.1 导入或打开项目

在 **书籍** 页面：

- 点击 **导入书籍**，选择 PDF 或单页图像。默认项目目录与源文件同目录，名称为 `<文件名>.scribeforge`。
- 点击 **打开项目文件夹**，选择已有的 `.scribeforge` 目录，可继续之前的处理、复核或导出。当前桌面工作流要求一个目录对应一本书；多项目目录会被明确拒绝。

选择源文件后，工作台会检查项目数据库、源文件路径和 SHA-256。已有项目的源文件被移动时，状态卡会说明原因；使用 **重新关联源文件** 选择移动后的原文件。只有 SHA-256 与原记录完全相同才会更新路径，处理证据不会被改写。内容不同的文件必须使用新的项目目录。

### 7.2 首次运行检查与处理

第一次使用前，进入 **设置**，查看运行时状态；若尚未安装 OCR 运行时，点击 **运行时配置 (setup)**。原生向导会分别显示 MinerU 与 PaddleOCR 的状态、当前安装阶段、步骤计数和失败步骤；全部安装失败或仅单个后端出错时，可从对应后端卡片重试/单独修复。安装完成或失败后，关闭向导会刷新状态；也可随时点击 **刷新运行时状态**。若 Python CLI 不在 PATH 中，可在 **命令行工具** 中填写完整可执行文件路径，例如 `D:\ScribeForge\.venv\Scripts\scribeforge.exe`。

进入 **处理** 并点击 **开始处理**。工作台调用 `scribeforge process --cancelable`，同时每秒读取 `scribeforge status --json`，显示已完成双引擎 OCR 的页数、当前运行的页面与阶段、任务计数及失败详情。OCR 页面调用默认期限为 600 秒；点击 **取消处理** 会向 CLI 发送取消消息并终止 worker 及其后代进程。失败或中断的阶段可重试，已保存的页图、OCR 证据和人工决策不会被覆盖。CLI 退出码 1 表示部分页面失败，2 表示源文件、输入或运行时错误，130 表示用户取消。

### 7.3 查看证据并记录人工复核

进入 **复核**，每条队列项会展示风险原因、OCR 引擎一/二的原始文本、可用的 VLM 候选与不确定标记，以及候选区域裁切图（没有裁切图时显示页面图像）。默认过滤已识别的页眉、页脚和连续页码；需要检查原书页边行时，打开 **显示页眉 / 页脚 / 页码**，界面会展示家具分类原因和两侧 OCR 原文。点击 **填入 OCR 1**、**填入 OCR 2** 或 **填入 VLM 候选** 只会把文字放入编辑框，不会自动接受；请核对图像和候选后，明确点击 **记录人工决策**。

这会调用 `scribeforge resolve`，把人工选择追加到决策证据链。OCR 原始结果不会被覆盖，VLM 标记为“不确定”也不会被当作结论。也可以在候选卡片内使用 **Alt+1/2/3** 填入 OCR 1/OCR 2/VLM 候选，使用 **Ctrl+Enter** 明确记录当前输入。页码与配对序号在界面上从 1 开始显示，CLI/JSON 中的 `page_index` 和 `pair_index` 是从 0 开始。

### 7.4 导出 EPUB 或 DOCX

进入 **导出**，选择 EPUB 3 或 DOCX，选择输出文件夹（文件名会跟随书名和格式生成），再填写书名、语言和可选作者。可选开启 **保留页眉、页脚与页码**，将默认剔除的已识别页边行纳入本次导出；家具分歧若仍未裁决，可信导出仍会被阻止。任何模式下，未裁决内容都会阻止导出；开启 **严格接受校验** 还会拒绝需要抽查的机器接受配对，关闭时则沿用 CLI 的默认接受策略。候选 EPUB 与已接受的正式导出不同：不要把未经复核的 OCR 候选误当成定稿。

### 7.5 Flutter 与 Python CLI 的 JSON 契约

Flutter 通过本机子进程调用 Python CLI，不启动网络服务，也不让浏览器或 UI 直接访问 localhost。当前契约为：

- `scribeforge status --store <目录> --json`：`schema_version: 1`；每个项目返回页数、双引擎成功数、任务计数、复核数量、失败详情，以及 `active_jobs`（运行中的 `stage`、`page_index`、`attempts`）。工作台在处理期间轮询它以显示页级进度。
- `scribeforge review --store <目录> --json`：`schema_version: 1`；返回每个未决配对的页码、配对索引、风险分数/原因、可空的 `furniture_reason`、两套 OCR 文本、VLM 文本/不确定标记、页面图像与裁切图路径、区域坐标和字符冲突。传入 `--include-page-furniture` 可把家具分歧加入队列并附上分类理由。
- `scribeforge resolve --store <目录> --project-id <ID> --page <页码> --pair <配对> --text <人工文本>`：明确指定项目并追加人工决策；`--project-id` 对旧式单项目调用仍可省略。

新增字段以加法方式扩展 schema v1；不认识的 `schema_version` 应被客户端拒绝，不能把未知 JSON 猜成复核结果。`process` 命令本身仍是同步命令，实时进度来自已持久化的 SQLite job 状态，而不是对 CLI 人类可读输出做脆弱的逐行解析。

## 8. 常见问题与开发者自检

### `scribeforge` 不是可识别的命令

确认 `.venv` 已激活且在仓库根目录执行过 `python -m pip install -e ".[dev]"`。也可以在设置里填写 `.venv\Scripts\scribeforge.exe` 的完整路径。

### 首次 OCR 配置失败

检查网络、磁盘空间和运行时报告中的失败步骤；修复后重试 **运行时配置 (setup)**。OCR 运行时按后端隔离安装。不要删除项目数据库或源文件来修复运行环境。

### 源文件路径或 SHA-256 不匹配

工作台会阻止把不同文件处理进已有项目。若只是移动了源文件，从项目状态卡使用 **重新关联源文件**；只有字节内容完全相同才会通过。若文件内容有改动，请为它创建新的项目目录。

### Flutter 报告找不到 Windows runner

在 `clients\flutter_desktop` 目录运行 `tool\bootstrap_desktop.ps1`，完成后再运行 `flutter run -d windows`。

### PDF 原生依赖在 Windows 上报符号链接错误

启用 Windows Developer Mode，再重新执行 bootstrap 和构建。不要把生成的 `windows` runner 文件夹提交到 Git。

### 开发者自检

在仓库根目录：

```powershell
python -m pytest
```

在 Flutter 目录：

```powershell
dart format lib test
flutter analyze
flutter test
```
