# ScribeForge 全仓库代码审查与工程质量审计

## 1. 执行摘要与边界

- 审查时间：2026-11-03 10:25–10:40 UTC；分支 `arena/a6af4ca9-scribeforge`；审查基线及当时最新 `origin/main` 均为 **`4c8586300b26dab6bba1d0c077e5259e456ec5cc`**（`git fetch origin --prune` 后确认）。克隆为浅历史；PR 历史通过 GitHub 查询，不将本地单条 log 当作完整历史。
- 项目是**本地优先、证据分层的书籍 OCR 编排器**，不是自研 OCR 模型。当前产品目标不是账号或云同步；用户没有指定专项业务，因此本报告选择最关键的“导入 → 双 OCR → 复核/修复 → 可信导出”做跨层追踪。
- **总体判断：**分层方向和大量确定性测试有价值；但自动修复有两个已复现的证据可信性缺陷（[#122](https://github.com/BrainEno/ScribeForge/issues/122)、[#123](https://github.com/BrainEno/ScribeForge/issues/123)），预处理帧与 repair 的绑定也存在源码确认的缺陷（[#124](https://github.com/BrainEno/ScribeForge/issues/124)）。因此不能仅凭绿色测试把所有自动接受的文本视为经独立证据充分核验。EPUBCheck CI 并非强制门禁（[#125](https://github.com/BrainEno/ScribeForge/issues/125)）。
- 本次创建 **4 个** GitHub Issues；原有开放 Issue **21 个**，分类见第 7 节。没有关闭 Issue、修改业务代码、数据库或合并 PR。本次静态审查覆盖全仓库的目录、契约、工作流与核心链路，并对高风险分支深入核查；不是对每一行都做了形式化证明。
- 本地没有 Flutter/Dart SDK、展示环境、OCR 模型/GPU 和完整模型下载网络；没有实测真实 OCR 质量、端到端吞吐、视觉布局、辅助技术、真实 VLM 服务、发行安装包或官方 EPUBCheck。**不将伪引擎/Oracle 的结果外推为真实模型准确率。**

## 2. 规模、技术栈与结构

### 统计方法

对基线 `git ls-files -z` 读取已跟踪文件，按目录分类并统计字节及文本文件的换行符数（不是 AST 有效代码行；含注释与空行；二进制不计行；无末尾换行的最后一行不额外计数）。没有识别到跟踪的 `*.g.dart`、`*.freezed.dart` 或 `*.generated.py`；被 `.gitignore` 排除的 Flutter 平台生成目录未计，不能推断其它文件绝无生成内容。

| 类别 | 路径规则 | 文件 | 行 | 字节 |
|---|---|---:|---:|---:|
| 生产 | `src/`、`clients/flutter_desktop/lib/` | 110 | 28,203 | 1,025,280 |
| 测试 | `tests/`、`clients/flutter_desktop/test/` | 93 | 22,255 | 751,682 |
| 工程脚本 | `benchmark/analysis/`、`.github/scripts/`、Flutter `tool/` | 10 | 1,836 | 58,443 |
| 其他：文档、配置、数据与二进制 | 其余 | 61 | 4,867 | 25,249,203 |
| 合计 | 跟踪文件 | **274** | **57,161** | **27,084,608** |

规模热点：`src/scribeforge/cli.py` 2,497 行、`storage/sqlite.py` 2,452 行、`unified_workspace.dart` 1,851 行；这是维护性信号而非单凭长度认定 Bug。

### 架构与数据流

```text
Flutter workspace ──argv / JSON/stdin──> Python CLI <── Tk reviewer / CLI operator
       │                                       │
       └── A/B candidate evaluation sidecar    ├── runtime manager: 独立环境/模型/探针
                                               │
PDF / PNG / JPG ─> 页图 + SHA256 ─> preprocess 派生帧
                  └─> SQLite pages/jobs     └─> OCR worker A/B (子进程 JSON v1/v2)
                                                        │
                   SQLite OCR/blocks/lines/tokens <─────┘
                     └─> 对齐/风险/候选 ─> 可选 VLM crop ─> repair/人工决定
                                         └─> decisions (证据引用与风险)
                                                       ├─> verified assembly ─> EPUB/DOCX
                                                       └─> raw candidate EPUB（不可当可信导出）
                     audit / backup / verify-backup / restore
```

Python 3.10+，项目依赖 Pillow、pypdfium2、NumPy；开发依赖 pytest/pytest-cov、Ruff、mypy；Flutter/Dart 使用 archive、crypto、file_picker、pdfrx、xml。OCR 模型隔离在 worker/私有环境；VLM 使用 OpenAI 兼容 HTTP 接口。`domain/` 是模型中立规则，`pipeline/` 编排，`adapters/` 实现输入输出和外部服务，`storage/sqlite.py` 持久化，`runtime/` 管理模型环境；桌面 UI 通过 CLI 调用。未发现源码证据足以断言存在循环依赖；未进行全依赖图静态证明。

### 接口与持久化清单

- CLI 由 `cli.py:296-663` 注册：`process`, `setup`, `runtime-status`, `vlm-check`, `status`, `structure`, `relink-source`, `audit`, `rehash-pages`, `backup`, `verify-backup`, `restore`, `repair`, `review`, `resolve`, `confirm`, `ui`, `export`, `export-candidates`, `benchmark`；参数以各子命令 `--help` 为准。部分桌面接口输出带 `schema_version` 的 JSON；可取消处理通过 stdin JSON 控制消息。CLI 非 HTTP 服务。
- OCR worker：`<command> --image <path> --page-index <index>`；stdout 单个 UTF-8 JSON，stderr 日志；`schema_version` 1/2、引擎名、页号、归一化坐标、行、可选 token/块/方向；适配器校验身份、版本，详见 `docs/ocr-worker-contract.md`。token 未提供与空数组有不同语义。
- VLM：显式启用，OpenAI 兼容 `/chat/completions`，裁剪图 base64 与结构化文字回复；远程需明确授权且 HTTPS，禁代理和重定向（`adapters/openai_compatible_vlm.py:28-85,142-197`）。这不是登录/会话系统；API key 来自指定环境变量而非桌面设置中的明文值。
- SQLite `PRAGMA user_version=12`，通过 `initialize` 升级；核心表为 `projects`, `pages`, `engine_runs`, `ocr_lines`, `ocr_tokens`, `ocr_blocks`, `verification_runs`, `alignments`, `alignment_conflicts`, `risks`, `review_candidates`, `vlm_reviews`, `decisions`, `jobs`, `page_preprocess`, `page_structure`, `chapters`, `page_confirmations`。证据及人工决策追加、作业状态可变；`exports` 在 `docs/data-model.md` 明确为规划而非已实现。备份 manifest 的 schema 版本与数据库 `user_version` 不是同一概念。
- 架构债务：CLI、SQLite 和工作台集中承担大量职责；更细的边界和契约测试有益，但不能以“统一所有导出器”为由混淆候选与可信导出（原 [#72](https://github.com/BrainEno/ScribeForge/issues/72)）。

## 3. 专项调用链与边界

1. Flutter `_pickPdf`/`_openProject` 选择书及 store；`_refreshStatus` 校验路径及 SHA-256 并阻止源文件不匹配时继续处理（`unified_workspace.dart:195-287,408-497`）；状态轮询用 request version 丢弃部分过期结果。
2. `_runProcess` 通过 `cli_executor.dart` / `cli_runner.dart:260-283` 以参数数组启动 CLI，stdout/stderr 流式收集；取消通过 versioned stdin 控制发送，不拼 shell 命令。CLI `_process` 先建库和确认 source，再 `import_source`，后调用 `process_book`。
3. import 按页写临时文件并替换，存页摘要和渲染参数；project 级 import job 记录失败（`pipeline/importing.py:57-108`）。`process_page` 验原始页图、可选预处理，再依次执行双 OCR、对齐和风险/候选持久化、可选 VLM、裁决（`pipeline/orchestrator.py:82-205`）；`_run_stage` 成功后跳过、失败可重试，`process_book` 单页失败不阻止其余页。作业锁用于竞争及崩溃接管；不能推断所有网络文件系统都支持一致锁语义。
4. `adjudicate_verification` 对正常机器决策调用接受策略，保留风险与证据；review JSON 提供候选/来源和坐标，Flutter `resolve` 向 decisions 追加人工决定。候选 A/B 的评价存 sidecar，语义独立于可信裁决。`assemble_verified_content` 在页未验证、pair 未决或空页未确认时阻止导出，并基于最新决定组装；识别的页眉页脚默认排除但可显式包含。图像材料须关注 #124 中的帧和校验缺口。
5. 修复轮次可对未决 pair 重读并自动写决定，但存在 #122/#123/#124。特别注意：单引擎不同裁剪可能提供新信息，不等于独立模型投票；已保存证据不代表接受策略一定被执行。VLM 可在经明确授权时发送 crop，风险包括网络故障/超时和远程数据披露；不能把自报置信度当独立正确性证明。
6. 导出由 `assembly.py` → `adapters/epub.py`/`docx.py`，内部结构/文本自检；候选 EPUB 经 `candidate_epub.py` 单独产生，不应进入可信导出。原始页图、预处理图、复核 crop 和接受文字是不同层，不可混为同一证据。

## 4. 新发现及问题索引

| 优先级 | 分类 / 验证状态 | 跟踪项与核心证据 | 可执行验收摘要 |
|---|---|---|---|
| **P0** | 真实 Bug；临时库 + FakeEngine 已复现 | [#122 修复轮次绕过接受闸门](https://github.com/BrainEno/ScribeForge/issues/122)：`rounds.py:463-486` 直接写 decision，未调用 `machine_blockers` 也未写风险快照；U+FFFD 在普通裁决被拦、repair 却自动接受 | 所有机器路径共享政策；异常字符、低信心和空间错配保持待审；风险、证据正确持久化并有导出/恢复回归 |
| **P1** | 真实 Bug；临时库 + FakeEngine 已复现 | [#123 同一模型重复计为独立票](https://github.com/BrainEno/ScribeForge/issues/123)：`rounds.py:309-317,430-438` 与 `consensus.py:131-144`；整页 MinerU=鸦、Paddle=雅，重读 MinerU=鸦、Paddle=蛙，仍自动接受鸦，支持者只有同一模型的两次读取 | 按来源而非标签计独立票；两来源真正一致才允许政策通过；保存全部尝试 |
| **P1** | 源码证实的错误路径；真实图像影响范围待测 | [#124 预处理帧/裁剪与摘要](https://github.com/BrainEno/ScribeForge/issues/124)：`preprocessing.py:8-14,97-107`、`orchestrator.py:119-132`、`rounds.py:404-422`；框在预处理帧，repair 却裁原图且未验摘要；存在的派生图复用未验 output SHA | OCR 证据绑定帧与摘要，正确变换/选择输入帧；篡改拒绝；几何变换、重启和参数变化测试 |
| **P2** | CI 配置事实；实际某次 jar 执行状态未知 | [#125 EPUBCheck 非有效门禁](https://github.com/BrainEno/ScribeForge/issues/125)：`.github/workflows/ci.yml:31-49` `continue-on-error`、下载失败跳过、找不到 EPUB 则跳过、`java ... || true` | 显式生成 fixture 并执行官方工具；故意无效样本证明失败不可吞；明确标识跳过 |

复现实录（本地临时目录，**非真实模型**）：使用 `runpy.run_path('tests/test_rounds.py')` 取得 `FakeEngine`, `_disputed_page`, `RepairReader`, `run_repair_round`。#123 以指定两个重读调用后返回 `resolved=True`、`selected_text='鸦'`、refs 为原 MinerU 与其 region run。#122 建临时 `SQLiteStore`、白色 PNG 页，两个 FakeEngine 均返回 `�`；`process_page` 返回 `unresolved_pairs=(0,)`，两个 repair FakeEngine 返回同字后 `run_repair_round` 写入 `automatic` 决策且 `risk_score=None`。完整可执行条件和改动范围见各 Issue。#124 是从不同帧的数据流得到的源码结论，并非本次已实测每种 deskew 参数都造成明显错裁。

## 5. 多维度评估

- **健壮性/一致性：**按页落盘、失败重试、恢复及 worker 的期限/取消/输出上限有明确实现和测试。另一方面 #122–124 会影响证据与决策；此外 `preprocess_page` 按文件存在复用，改变预处理选项是否安全需要独立回归。迁移使用多处 `executescript`；不能仅因为 `_connection` 调用 rollback 就声称所有 DDL 原子回滚，建议在 #63 中用故障注入验证。
- **安全/隐私：**本地应用无账号授权体系，不应凭空审计多租户。VLM 默认关闭；本地无代理、禁止重定向、远程 HTTPS 与显式 opt-in 是正面措施。仍需处理 [#71](https://github.com/BrainEno/ScribeForge/issues/71) 的运行时/模型依赖固定和校验；本次未作渗透测试或完整供应链审计。`benchmark/corpus/v/manifest.json` 写明 2008 年中译本来源；仓库对样本的再分发权限未由本次审查验证，应由维护者核验，不据此断言侵权。
- **架构：**worker 隔离、领域中立、原证据/决策分层、候选/可信导出分开都合理。决策逻辑却分散在正常裁决与修复轮次，是两类真实问题的关键。未做复杂度/循环依赖工具扫描；大文件尺寸仅提示拆分契机。
- **UI/UX：**工作台已有导入、状态、复核、裁决、导出、备份及键盘快捷键；不是旧 #78 所描述的仅 A/B 评价。review 图片使用固定高度 150、载入失败仍允许文字复核（`unified_workspace.dart:1388-1408,1515-1517`），真实高分辨率文字的可读性值得用户测试；没有实机验证，不能断言不可用。文本有中文主界面及部分英文设置文案；未见完整多语言系统，响应式、屏幕阅读器、缩放及 WCAG 尚未实测。
- **性能：**`process_book` 串行分页，两个 OCR 串行（`orchestrator.py:134-159,245-261`）；大书延迟是合理风险，不代表本次测出了处理耗时或并行一定更快。引擎启动、GPU 显存、SQLite 锁和内存须先基准再选调度方案（#70）。状态与复核 JSON 收集在内存中；大书 UI 大小及刷新成本未测。
- **测试：**Python 快速套件覆盖率超过 90%，但覆盖率不是正确性证明，且 `pyproject.toml` 明确排除两套 Tk widget 文件；真实 GPU/model 标记默认排除。独立修复轮次尚缺 #122–124 的关键跨层回归。Flutter CI 以 fakes 为主，不能证明真实 CLI/OCR + 原生 Windows 打包流程。
- **CI/CD：**main Python CI、3.14 Ubuntu/Windows 已成功；Flutter workflow 仅 PR / 手动触发而非 main push，且 `dart format lib test` 改写而非 `--set-exit-if-changed` 检查。未发现针对桌面发行包的构建/发布门禁；#125 的绿色步骤不证明 EPUBCheck 实际通过。依赖范围没有完整锁定，需考虑可复现性与供应链（#71）。

## 6. 验证结果与 CI 证据

环境：Linux，Python 3.11.2；命令从仓库根目录执行。venv 位于 `/tmp/scribeforge-audit-venv`，构建输出位于 `/tmp/scribeforge-audit-build`，不进入仓库。

| 实际命令 | 退出码 | 结果/边界 |
|---|---:|---|
| `python -m pip install -e '.[dev]'` | 1 | 系统环境受 PEP 668 管理，未强行覆盖 |
| `python -m venv /tmp/scribeforge-audit-venv && /tmp/scribeforge-audit-venv/bin/pip install -e '.[dev]' build` | 0 | 隔离安装成功；依赖未锁定，版本可能随时间变化 |
| `/tmp/scribeforge-audit-venv/bin/ruff check .` | 0 | 全通过 |
| `/tmp/scribeforge-audit-venv/bin/mypy src` | 0 | 84 个源文件无错误 |
| `/tmp/scribeforge-audit-venv/bin/pytest -m 'not benchmark and not gpu'` | 0 | 958 passed、14 deselected，53.41 秒；覆盖率 **91.48%**，门槛 90% |
| `/tmp/scribeforge-audit-venv/bin/pytest -m benchmark --no-cov` | 0 | 13 passed、959 deselected，37.17 秒；测试/夹具通过，不等于真实 OCR 推理 |
| `/tmp/scribeforge-audit-venv/bin/ruff format --check .` | 1 | Ruff 0.17.3 报 113 files would be reformatted、85 already formatted；非当前 CI 门禁 |
| `/tmp/scribeforge-audit-venv/bin/python -m build --outdir /tmp/scribeforge-audit-build` | 0 | sdist 和 wheel 构建成功，不是桌面安装包 |
| `/tmp/scribeforge-audit-venv/bin/pip check` | 0 | 无损坏的依赖声明 |
| `/tmp/scribeforge-audit-venv/bin/scribeforge --help` | 0 | CLI 帮助可运行 |
| 两段临时 FakeEngine 复现实验（第 4 节） | 0 | 复现 #122、#123，未写仓库数据 |

`flutter` / `dart` 不在 PATH，故未本地运行 `flutter pub get/analyze/test/build`。未执行 GPU smoke（需专用 runtime），没有声称运行真实 OCR。基线 main [CI 运行 41206278437](https://github.com/BrainEno/ScribeForge/actions/runs/41206278437) 的 core 和 Python 3.14.4 Ubuntu/Windows job 均 success；[PR #121 Flutter 运行 41197168214](https://github.com/BrainEno/ScribeForge/actions/runs/41197168214) success，head 为 `6d22db5f28fb85befaf16e687afc99cb856bdb50`，不是 merge SHA；该 PR 在基线合并。`gh run view ... --log` 下载日志时对 `results-receiver.actions.githubusercontent.com` 返回 EOF，所以无法核验这次 CI 中 EPUBCheck 到底是否运行。CI 列出的步骤成功不能克服 workflow 的吞错逻辑。

## 7. 既有开放 Issues 的逐项清理建议

以下为创建新 Issue **之前** GitHub 返回的 21 项；结论是建议，不操作关闭。以各条原始验收标准为准，证据路径均指本报告基线。

### 建议关闭（1 项）

- [#77 OCR 子进程期限与取消](https://github.com/BrainEno/ScribeForge/issues/77)：其“故意挂起能停止、错误可见、已持久化状态可恢复”的主要验收有实现和回归。`adapters/subprocess_ocr.py:73-93,220-308` 实现 deadline、输出限制与进程树清理；`tests/test_orchestrator.py:346,383`、`tests/test_cli.py:628` 覆盖超时/取消/重试，均包含在本次通过的快测中；[PR #117](https://github.com/BrainEno/ScribeForge/pull/117) 已合并，基线 CI 有 Windows 3.14 测试。**建议维护者复核目标机器真模型/进程树行为后关闭**；不声称实测真实 GPU 挂死。

### 部分完成、保留并补充（7 项）

- [#90 多引擎技术路线](https://github.com/BrainEno/ScribeForge/issues/90)：`workers/paddle_vl_worker.py`、`workers/surya_worker.py`、`runtime/planner.py` 已有路径，`domain/consensus.py` 有规则；真实三引擎古籍对比、覆盖许可与显存实测未证实。旧的 PP-OCRv6 主引擎设想也须更新，当前契约文档明确 PP-OCRv4。
- [#88 派生规范化层](https://github.com/BrainEno/ScribeForge/issues/88)：`domain/normalize.py` 已存在，用于异体字比较（`docs/glyph-variants.md`），旧“无任何 normalization 模块”不成立；并不等于支持 OpenCC 简体导出、可审计转换记录及导出层元数据。
- [#86 worker 契约](https://github.com/BrainEno/ScribeForge/issues/86)：`docs/ocr-worker-contract.md` 已有 v2/block/line/token 及兼容；[PR #119](https://github.com/BrainEno/ScribeForge/pull/119)、[#120](https://github.com/BrainEno/ScribeForge/pull/120) 引入原生词框与 opt-in smoke。真实 PP-OCRv4 非空 token 尚未由本次执行；MinerU 无原生 token、行框/块框及裁剪尺寸验收仍需核对。旧“全无块及低置信度永不触发”已过时。
- [#84 结构保真](https://github.com/BrainEno/ScribeForge/issues/84)：`domain/content.py`、`domain/paragraphs.py`、`adapters/epub.py` 已有段落/章节、图像路径；仍缺含脚注/插图样书的结构保留率、官方 EPUBCheck 和多阅读器结果。#125 跟踪具体 CI 缺陷。
- [#82 页图摘要](https://github.com/BrainEno/ScribeForge/issues/82)：`pipeline/importing.py:90-103` 写摘要，`project_audit.py:192-219` 检验，`tests/test_project_audit.py:92` 覆盖替换；v11 迁移/rehash 可兼容旧项目。但 `rounds.py:420-422` 的独立重读没有验证该原图，故“阻止作为原始证据使用”尚不能无条件关闭；残余根因由 #124 覆盖。
- [#79 复杂版面对齐](https://github.com/BrainEno/ScribeForge/issues/79)：`domain/alignment.py:127-168` 已有方向与分栏相关逻辑，`tests/test_alignment_groundtruth.py` 提供夹具；旧“只 y 距离”已过时，仍需一对多/多对一及复杂版面样本上的匹配/顺序误差评估，不把局部通过等同满足完整验收。
- [#78 Flutter 逐行复核](https://github.com/BrainEno/ScribeForge/issues/78)：`unified_workspace.dart:651-699,1416-1530` 已有候选、人工决定及键盘操作，README 区分候选评价；尚未证明真实 UI→CLI→SQLite→重启→导出的完整实机验收，坐标帧问题另见 #124。建议按剩余验收更新描述后复核关闭。

### 仍有真实缺口/风险，保留（7 项）

- [#81 基准代表性](https://github.com/BrainEno/ScribeForge/issues/81)：`benchmark/corpus/v/manifest.json` 六页、`guji/manifest.json` 三页，都是单次校对；缺 30–50 页分层、A/B/C/D 真实模型与人力成本比较。不是“当前仍只有四页”。
- [#80 指标口径](https://github.com/BrainEno/ScribeForge/issues/80)：`benchmark/metrics.py` 的 `word_error_rate` 用 `split()`、`line_error_counts` 精确整行 LCS；中文 WER 和真实漏行不能混为一谈。WER 大于 1 本身可能因插入项，并非必然实现错误。
- [#73 Python 格式门禁](https://github.com/BrainEno/ScribeForge/issues/73)：当前 CI 只执行 Ruff lint；本次 format check 113 个待格式化，结果依赖工具版本。低优先级维护债，不是业务 Bug。
- [#71 依赖和模型供应链](https://github.com/BrainEno/ScribeForge/issues/71)：`runtime/planner.py` 有部分版本 pin，`runtime/tools.py` 有下载与安装；未见覆盖全部权重/依赖的锁定哈希清单。需按发行风险及模型许可核查，不声称已发现投毒。
- [#70 串行处理](https://github.com/BrainEno/ScribeForge/issues/70)：`orchestrator.py:134-159,245-261` 依次运行，属于性能改进机会而非性能事故；老 Issue 基于小样本的小时推算不能作为本次实测。
- [#69 用户处理报告/可观测性](https://github.com/BrainEno/ScribeForge/issues/69)：CLI status 与 jobs 可查，`rounds.progress` 有复核汇总；未见其所要求的完整每页/引擎耗时、风险聚合报告及脱敏日志。功能改进而非必然故障。
- [#67 授权与发布](https://github.com/BrainEno/ScribeForge/issues/67)：仓库根目录无 LICENSE、`pyproject.toml` 无 license 字段；`gh api repos/BrainEno/ScribeForge/tags` 返回 `[]`，`gh release list` 无输出。许可选择需权利人决定。

### 过时、重复或需要重新定义（6 项）

- [#80](https://github.com/BrainEno/ScribeForge/issues/80) 与 [#65](https://github.com/BrainEno/ScribeForge/issues/65) 都讨论中文 WER；保留 #80（也覆盖漏行），**建议关闭 #65 为重复**，并保留语言口径建议。#65 对大于 1 的表述需修正。
- [#62](https://github.com/BrainEno/ScribeForge/issues/62) 与 [#81](https://github.com/BrainEno/ScribeForge/issues/81) 都要求扩大基准；建议关闭 #62 为重复并将其多语言/版式覆盖需求合并进 #81。#62 的“100–150 页”与“中文 150–200 页”本身矛盾，需重新确定目标。
- [#68](https://github.com/BrainEno/ScribeForge/issues/68) 属 #81 的多语言分层子集；建议合并验收进 #81 后关闭，保留日语竖排/英文双栏的明确覆盖目标。README 的中英日是初始目标，并非已验证结论。
- [#63](https://github.com/BrainEno/ScribeForge/issues/63)：原“无版本化迁移”已被 `storage/sqlite.py:168,276-317` 的 user_version 1→12 和 `tests/test_job_persistence.py:282-385` 证伪；但旧条还要求逐历史版本迁移、失败回滚、备份 manifest 数据库 schema 等，不能声称全部满足。**建议重定义**为迁移原子性、历史库和跨版本恢复验证，而不是关闭全部剩余工作。
- [#66](https://github.com/BrainEno/ScribeForge/issues/66)：旧覆盖数字过时，应以本次为依据重新定义：`runtime/tools.py` 62%、`runtime/probe.py` 71%、`project_backup.py` 83%；为失败路径设计注入测试比机械提高数字更重要。与 #71 中下载失败测试部分重叠。
- [#72](https://github.com/BrainEno/ScribeForge/issues/72)：保留为低优先架构讨论或重定义为共享容器格式契约测试。`candidate_epub.py` 与 `epub.py` 确有两条实现及 `export_common.py`，但要求统一为同 builder 是方案，不是已证实 Bug；两层不同信任语义必须保留。

上述 1 + 7 + 7 + 6 = 21，#65/#62/#68 的关闭是**去重建议**，不是声称所有相应产品能力已完成。未对 Issue 或 PR 做状态写入。

## 8. 推荐开发顺序及依赖

1. **立即先处理 #122**：统一所有机器决策闸门与风险快照；对已有 repair 决策提供审计定位与重新人工复核方案，不静默覆盖证据。可与 #123 并行设计，但上线自动修复前两者均应闭合。
2. **#123 独立来源身份模型**：区分模型/引擎与重试，定义多引擎共识；补缺失证据、VLM、恢复测试。依赖 #122 的统一接受边界。
3. **#124 帧和摘要绑定**：确保 repair、VLM、人审看到与 OCR 相关的正确像素；再扩大真实古籍与多轮模型评估。对旧项目提供兼容与可解释的阻断/恢复。
4. **#125 CI 验证可观测性** 与 #63 迁移故障注入、#71 供应链安全并行；然后进行真实 Windows 桌面流程、官方 EPUBCheck/多阅读器与带许可的跨语言样本测试（#81/#84/#86）。
5. 在证据可信与真实基准建立后再优化 #70 并发/吞吐，测量 pages/min、RAM/VRAM 和失败率；不要用未经验证的并行度作为默认。

## 9. 可审计工作记录与环境

- 工具：Arena.ai Agent Mode；可使用终端、文件系统读写、`git`、已认证 `gh` 与 GitHub API。实际读取 main、PR、issues、Actions、tags、releases，实际创建 4 issues；未请求用户凭据。其他可用工具包括文件读写/展示、后台进程、网页与图像相关工具；本次以终端/GitHub 为主。模型提供商、具体模型 ID、Agent 版本、是否启用推理模式及配置均**未知/无法确认**。
- 代表性命令：`git status`, `git fetch origin --prune`, `git rev-parse HEAD origin/main`, `git ls-files -z`, `gh issue list --state open --limit 300 --json ...`, `gh pr list --state all --limit 40 --json ...`, `gh pr view 121`, `gh pr checks 121`, `gh run list`, `gh run view`, `gh api repos/BrainEno/ScribeForge/tags`, `gh release list`, `rg`, `nl`, `sed`；测试/构建完整命令和退出码见第 6 节。
- 核心阅读：`README.md`, `AGENTS.md`, `pyproject.toml`, 两个 workflow、`docs/architecture.md`, `docs/data-model.md`, `docs/ocr-worker-contract.md`, `docs/epubcheck-validation.md`；Flutter `unified_workspace.dart`, `cli_runner.dart`, `cli_executor.dart`, `settings_store.dart`；Python `cli.py`, `pipeline/importing.py`, `orchestrator.py`, `preprocessing.py`, `adjudication.py`, `rounds.py`, `assembly.py`, `domain/consensus.py`, `acceptance.py`, `alignment.py`, `furniture.py`, `storage/sqlite.py`, `adapters/subprocess_ocr.py`, `openai_compatible_vlm.py`, `project_audit.py`, `project_backup.py` 与对应测试/语料 manifest。并非逐行覆盖每个模块。
- 源码 + 临时 FakeEngine/SQLite 动态验证：#122、#123；源码核验但未真实模型动态验证：#124、#125。现有快测和 benchmark marker 测试通过仅证明所覆盖场景。实际 PR/Actions 信息来自 GitHub 查询，而非猜测。
- 限制与更正：起初系统 Python 缺 pytest 且 PEP 668 拒绝安装，改用隔离 venv 后成功；远程 CI 原始日志下载 EOF，改以 job 元数据和 workflow 源码判断，不宣称验证器实际运行；原 #63“无迁移”、#78“Flutter 没有逐行决定”、#86“没有 v2 块”、#82“无摘要”均被当前源码纠正，但剩余验收没有自动判为完成。
- 三个最重要判断及证据：①机器接受不是所有路径都受门禁保护（#122 的 U+FFFD 复现）；②重复标签不等于独立来源（#123 的 2:1:1 复现）；③处理帧、坐标、摘要需作为一个契约（`preprocessing.py:8-14` 对照 `rounds.py:404-422`）。这些判断分别来自动态实验、动态实验、跨文件源码核对；第三项真实模型影响尚未量化。
