# ScribeForge 新手使用指南（Windows 优先）

这份指南面向第一次使用 Git、Python、Flutter 和命令行的用户。ScribeForge 会把扫描书籍保存在本机进行 OCR、保留每页结果与证据，并允许你比较两个 OCR 模型的输出。**OCR 候选 EPUB 尚未校对，不等于最终定稿。**

## 1. 先了解你要做的事情

常见工作流程如下：

1. 准备一份扫描版 PDF。
2. 安装 ScribeForge 的 Python 程序与私有 OCR 运行环境。
3. 运行两套 OCR，让程序将结果保存到项目目录。
4. 生成 MinerU 与 PaddleOCR 两个候选 EPUB。
5. 启动 Flutter 桌面端，同时查看原始 PDF、候选 A、候选 B。
6. 按页比较、标记偏好、写下问题，保存人工评估。

桌面比较器可以独立打开任意 PDF 和 EPUB；若要让它直接启动 OCR，则需要先安装 Python CLI。Windows 是当前优先验证的桌面环境。

## 2. 安装基础软件

请安装：

- **Git**：用于下载仓库。
- **Python 3.11 或更新的兼容版本**：用于 ScribeForge 后端。
- **Flutter 3.47.7 稳定版工具链**：用于运行桌面端。
- Windows 11 用户建议启用 **Developer Mode（开发者模式）**，因为 PDF 组件的原生构建可能需要创建符号链接。

安装后打开 PowerShell，运行以下命令确认工具可用：

```powershell
git --version
py -3.11 --version
flutter --version
flutter doctor
```

如果 py -3.11 不存在，请先安装 Python 3.11，并在安装程序中启用 Python Launcher。运行 flutter doctor 时，优先处理 Windows Desktop 构建工具的报错。

## 3. 下载代码并安装 Python CLI

在 PowerShell 中执行：

```powershell
git clone https://github.com/BrainEno/ScribeForge.git
cd ScribeForge

py -3.11 -m venv .venv
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

首次设置会下载或安装应用管理的运行时、OCR 依赖和模型，过程可能较长，需要网络连接与足够磁盘空间。运行时默认放在当前用户的应用数据目录，不会把模型环境写进每本书的项目目录。

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

处理进度和最终统计会显示在终端。退出码的含义：

- 0：全部页面处理成功。
- 1：至少部分页面失败；任务可重试。
- 2：遇到致命配置或输入问题，例如源文件不存在、格式不支持、运行环境未准备好。

该任务支持按页面恢复。部分页面失败时，可再次运行同一条命令；修复了缺失的运行环境后也可以重跑，不需要先删除整个项目目录。

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

## 7. 启动 Flutter 桌面比较器

仍在仓库根目录时，执行：

```powershell
cd clients\flutter_desktop
.\tool\bootstrap_desktop.ps1
flutter run -d windows
```

首次启动会生成 Windows 桌面 runner 并拉取 Flutter 依赖。日后直接在 clients\flutter_desktop 中运行 flutter run -d windows 即可。

### 在界面里完成比较

1. 点击 **Open source PDF**，选择原始扫描 PDF；如果已有 `.scribeforge` 项目，也可以先点击 **Open project** 选择项目目录。
2. 打开已有项目时，应用会读取项目记录并尝试打开其记录的源 PDF，然后核对源文件路径与 SHA-256。若源文件找不到、路径不同或内容已变化，状态条会显示原因，并禁用 OCR 重试与候选导出，避免旧记录被套用到另一份文件。
3. 点击 **Open Candidate A EPUB** 和 **Open Candidate B EPUB** 载入候选。
4. 翻页，观察两份 EPUB 文本的差异标记，并同时核对 PDF 原页。
5. 在 **Human evaluation** 中选择本页更好的版本、平局或稍后复核。
6. 评估完整本书后选择总体偏好，写上缺行、错字、标点或阅读顺序等备注，再点击 **Save evaluation**。

支持 OCR 工作流的版本还可以在界面内点击 **Set up OCR**、**Run OCR**、**Build A/B EPUBs**。第一次使用时，通过右上角设置图标确认 CLI 可执行文件：默认是 scribeforge；若应用找不到它，填写完整路径，例如 D:\ScribeForge\.venv\Scripts\scribeforge.exe。该设置会保存到当前用户的应用配置目录。

选择 PDF 后，页面上方会出现项目状态条：显示总页数、两套 OCR 都已成功的页数、任务成功/待处理/运行中/失败数量，以及待人工复核的数量。点击刷新图标可重新读取状态；出现失败任务时，可点击 **Retry failed pages** 重试。该操作会复用 CLI 的可恢复处理流程，不会删除已有页面证据。项目状态只有在源 PDF 的路径与 SHA-256 均匹配时才允许继续处理。如果只是移动了 PDF：先点击 **Open project** 打开原来的 `.scribeforge` 目录，再点击 **Open source PDF** 选择移动后的文件；确认路径或哈希不匹配后，点击 **Relink source PDF**。程序会重新计算 SHA-256，只有与项目中记录的哈希完全相同才更新源路径；该操作不修改哈希、OCR 运行记录、页面和人工校对证据。若文件内容不同，重新关联会失败，请为修改后的 PDF 使用新的项目目录。

也可以从 PowerShell 执行相同的安全操作：

```powershell
scribeforge relink-source --store "D:\Books\My Book.scribeforge" --source "E:\Archive\My Book.pdf"
```

重新关联后，正常的 `scribeforge process` 仍会检查源路径和内容身份，不会因手动指定一个不同文件而复用旧证据。

当候选 EPUB 由 ScribeForge 生成时，程序会自动按源页编号对齐。其他电子书若只含章节而没有源页编号，页面对应关系只能近似判断，界面会提示这一点。当前版本提取 EPUB 的 XHTML 文本进行逐行比较，并不保证还原每个 EPUB 的 CSS、字体和分页布局。

### 从桌面端导出已校对成品

打开与当前源 PDF 路径和 SHA-256 相匹配的项目后，点击 **Export verified book**。选择 EPUB 3 或 Word（DOCX），填写书名、语言标签（例如 zh-Hant、en、ja；未知时用 und），作者可不填。修改时间也可留空；若填入 ISO 8601 时间戳，导出可以固定该元数据时间，便于生成可复现的文件。

该操作与 **Build A/B EPUBs** 不同：A/B 文件是未经校对的原始 OCR 候选，仅用于比较；正式导出只使用数据库中已接受的校对决策。只要仍有未验证页面或待处理的冲突，CLI 就会拒绝导出，并在命令日志中显示原因。请先完成人工复核，而不要把候选 EPUB 当作定稿。

## 8. 评估文件放在哪里

人工评估保存在源 PDF 旁边，文件名类似：

```text
My Book.pdf.scribeforge-review.json
```

它会记录 PDF 与两个候选 EPUB 的 SHA-256 指纹、逐页判断、整书偏好和备注。如果源 PDF 或候选文件之后发生变化，旧评估不会在没有提示的情况下直接套用到新输入上。

请保留以下文件：

- 原始 PDF。
- 对应的 *.scribeforge 项目目录。
- 候选输出目录。
- *.scribeforge-review.json 人工评估文件。

备份或移动文件时，尽量一起移动相关项目目录和评估文件，并在移动后重新选择实际路径。

## 9. 常见问题

### scribeforge 不是可识别的命令

说明虚拟环境可能未激活，或 CLI 没有安装成功。在仓库根目录重新激活 .venv，或者用 .\.venv\Scripts\scribeforge.exe 的完整路径执行。

### 首次 OCR 配置失败

检查网络、可用磁盘空间、错误日志中的失败步骤。运行环境是按 OCR 后端隔离安装的；其中一个后端失败时，不要假定另一个已完整就绪。修复环境后重新打开 scribeforge setup。

### Flutter 报告找不到 Windows runner

在 clients\flutter_desktop 目录运行 .\tool\bootstrap_desktop.ps1，完成后再运行 flutter run -d windows。

### PDF 原生依赖在 Windows 上报符号链接错误

启用 Windows Developer Mode，再重新执行 bootstrap 和构建。不要把生成的 windows runner 文件夹提交到 Git。

### 界面无法启动 OCR 命令

打开右上角设置，填写完整的 scribeforge.exe 路径。确认 Python 包已在仓库根目录通过 pip install -e ".[dev]" 安装，并确认 CLI 路径对应的环境已安装。

### 候选 EPUB 生成失败或缺页

先运行 scribeforge status --store "<项目目录>" 查看失败页面与任务状态，然后重跑 scribeforge process "<源 PDF>" --store "<项目目录>"。所有页都有所需引擎输出之后，再运行 export-candidates。

## 10. 开发者自检

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

如果你只是想比较已有的 PDF 与 EPUB，并不需要先安装 OCR 模型；安装 Flutter 桌面依赖并启动比较器后，直接手动打开三个文件即可。
