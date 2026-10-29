# 技术路线（issue #90）

本文是 `docs/ancient-book-support.md` 的执行版：评估文档回答"够不够用、缺什么"，
本文回答"先做什么、换哪个引擎、怎么验收"。撰写于 2026 年 10 月，基于对
`benchmark/V..pdf` 的真实测量与 #85–#89 的实施结果。

## 1. 结论先行

1. **不更换双引擎分歧 → 风险 → 复核 → 决策的骨架。** 这是本仓库唯一别人不提供的
   东西；任何 OCR 模型都只是它的输入。
2. **换主引擎，不改架构**：主识别用 **PaddleOCR-VL（0.9B, Apache-2.0）**，
   副识别保留 **PP-OCRv6（CPU 可跑）**，形成"快引擎 × 强引擎"的分歧组合，
   比现在"两个中等引擎"信息量高。
3. **版面/阅读顺序引入独立第二意见：Surya**（只作结构证据，不作文本）。
   它的价值在"页面结构"而非"字形"，正对 #86 之后的剩余缺口。
4. **竖排仍未解决**，这是当前唯一阻碍"宣称支持古籍"的硬门槛（见 §5）。
5. **VLM 只做复核，永不做主识别**：它会静默改写原文，与证据链模型冲突。

## 2. 本轮已完成（#85–#89）

| Issue | 内容 | 交付物 | 验证方式 |
| --- | --- | --- | --- |
| #85 | 预处理层（deskew / deshadow / contrast / 按需 denoise、binarize、crop） | `domain/preprocess.py`、`adapters/image_preprocess.py`、`pipeline/preprocessing.py`、schema v5 | `benchmark/analysis/preprocess_benchmark.py`：真实页面派生 8 种退化，spine-shadow 191.5→228.8 分离度、faded 0.0→228.6、skew 残余 0.00° |
| #86 | worker 契约 v2（块类型、行级几何、置信度、方向） | `workers/protocol.py`、`domain/ocr.py`、MinerU/Paddle worker | 契约测试 + worker 载荷测试 |
| #87 | 章节 / 印刷页码 / 页面角色 | `domain/structure.py`、`pipeline/structure.py`、schema v6、EPUB 章节化导航 | 结构测试 55 项 + 导出测试（嵌套 nav、印刷页码标题） |
| #88 | 繁简/异体规范化（只影响比较） | `domain/normalize.py` | 折叠/风险/裁定三层测试 |
| #89 | 页眉页脚剔除（块 + 跨页重复 + 孤立页码） | `domain/furniture.py` | 误删防护测试 + 装配集成测试 |

两个在实施中被实测发现、此前只在纸面上的假设：

- **"预处理总是有益"是错的**：中值滤波在 300 DPI 真实页面上使墨/纸分离度从
  225.8 降到 222.6。默认关闭，只有整页判定为脏/阴影/褪色时才动像素
  （见 `docs/preprocessing.md` §3.1）。
- **"块证据已经在用"也是错的**：#86 只改了域模型与 worker 输出，
  `ocr_blocks` 从未落库，结构检测读回时 blocks 恒为空（见 `docs/book-structure.md` §2）。

## 3. 引擎选型（免费、开源、可本地部署）

### 3.1 推荐组合

| 角色 | 引擎 | 许可 | 体量/显存 | 为什么是它 |
| --- | --- | --- | --- | --- |
| 主识别（准） | **PaddleOCR-VL 0.9B** | Apache-2.0 | 0.9B，约 2 GB 显存 | 官方明确覆盖繁体、竖排、手写、旧文档；与现有 Paddle 生态同源，`paddle_worker` 改动最小 |
| 副识别（快） | **PP-OCRv6 + PP-StructureV3** | Apache-2.0 | 数十 MB 级，CPU 可跑 | 极低成本第一意见；PP-Structure 提供版面标签（#86 已能消费） |
| 版面/阅读顺序证据 | **Surya**（仅结构） | 代码 Apache-2.0，**权重 OpenRAIL-M** | 650M | 版面、阅读顺序、表格的第二意见；不参与文本判定 |
| 竖排第三意见 | **CnOCR** 或 **Indra OCR** | Apache-2.0 / 见仓库 | CPU | 两者都明确支持竖排，成本低，适合交叉验证 |
| 古籍分栏 | **Kraken**（评估） | Apache-2.0 | 小，CPU | 专为历史文献分栏设计，可自训字形 |
| 对照基线 | **Tesseract `chi_tra_vert`** | Apache-2.0 | 极小 | 只在回归测试中作基线，不作生产引擎 |

### 3.2 明确不采用

| 方案 | 原因 |
| --- | --- |
| 9B 级 VLM 端到端出全书 Markdown（Chandra / olmOCR / DeepSeek-OCR） | 不可溯源、会幻觉，与"证据级、绝不静默改写"的铁律冲突；显存门槛也排除普通用户 |
| 云端 OCR API | 违背 local-first |
| 自研/自训 OCR 模型 | 成本远超收益，开源方案已足够 |
| TongGuOCR（2026-08 学术工作，中文历史文献） | 论文报告 M5HisDoc AR 93.76%、NED 10.43→6.15，很值得跟踪；但权重/许可以及稳定性未核实，**不投产** |
| OpenCC 全量繁简转换 | 作为**派生层**可选引入（#88 已留出接口），但绝不能覆盖原文字段 |

**许可注意**：Surya/Marker 的**权重**是 OpenRAIL-M（不是 Apache-2.0），
若将来把模型文件随发行版分发，必须单独标注许可与使用限制；MinerU 为 Apache-2.0
但附带归属与收益条款。此项必须在打包前复核，不能只写在文档里。

### 3.3 接入方式：契约 v2 已经准备好

PaddleOCR-VL 与 Surya 都不需要新的适配器类型——`docs/ocr-worker-contract.md`
的 v2 文档已能表达它们的输出：

```json
{
  "schema_version": 2,
  "engine": "paddleocr-vl",
  "engine_version": "0.9b",
  "page_index": 12,
  "direction": "vertical-rl",
  "lines": [{"text": "...", "box": [0.72, 0.11, 0.03, 0.62], "confidence": 0.94, "tokens": []}],
  "blocks": [{"type": "text", "box": [0.70, 0.10, 0.08, 0.66], "line_start": 0, "line_end": 24}]
}
```

- Surya 作为**结构引擎**接入时只填 `blocks` 与 `direction`、`lines` 可为空——
  下游 `text_line_indexes()` 会把"无块证据"当作全部保留，因此结构引擎不会误删内容。
- 新增引擎只需：runtime planner 增加一条模型条目 + 一个 worker 脚本 + 基准条目，
  不需要改动 pipeline。

### 3.4 已落地的接入骨架（2026-10-29）

| 项 | 文件 | 说明 |
| --- | --- | --- |
| PaddleOCR-VL worker | `workers/paddle_vl_worker.py` | 调用 `PaddleOCRVL().predict()`，把 `parsing_res_list` 映射为 v2 行/块；**行序保留模型自己的区域顺序**（那就是它的阅读顺序） |
| Surya 结构 worker | `workers/surya_worker.py` | 只出 `blocks`（按 `position` 排序），`lines` 恒为空；不声明方向 |
| 运行时计划 | `runtime/planner.py` | `paddleocr-vl`、`surya` 两条 `RuntimeBackendPlan`，均标 `optional=True` |
| 基准注册 | `cli.py` `_BENCHMARK_BACKENDS` | `--engine paddleocr-vl` / `--engine surya` 可直接进基准 |

三条与"诚实性"有关的设计约束：

1. **可选后端不进默认安装。** 两者都是数 GB 的模型栈，`install` 会跳过 `optional`
   的后端，需要时用 `runtime repair --backend surya` 单独装。
2. **不声明方向 ≠ 声明横排。** `result_document(direction=None)` 直接**省略**该字段；
   MinerU/PP-OCR 没有方向结论时也不写。写一个常数 `horizontal-lr` 会被下游当作
   引擎断言，从而压制几何方向检测——那正是竖排书逐页配错的成因。
3. **未验证的部分写在文档里，不写进 README 能力声明。** 两个 worker 的上游 API
   与 JSON 字段名来自文档而非实跑；解析器对字段缺失/改名做降级（宁可"无块证据"
   也不崩页），但 R1/R2 的"真实权重跑通"仍是未完成的验收动作。

## 4. 实施阶段与验收门槛

| 阶段 | 内容 | 验收标准（可测量） | 状态 |
| --- | --- | --- | --- |
| **R1** | PaddleOCR-VL worker 接入（`--image/--page-index` 契约不变） | 6 页基准上双引擎均产出 v2 文档；`normalize` 后分歧率低于 PP-OCRv6 × MinerU 组合 | ⚠️ **代码就绪，未跑真实权重** |
| **R2** | Surya 结构 worker（只出 blocks/direction） | 基准页 block 数量与非文本区（图表）排除结果人工抽查通过 | ⚠️ **代码就绪，未跑真实权重** |
| **R3** | 竖排对齐（#79 + P0-2） | 竖排夹具页行序正确率 ≥ 95%；现有横排测试零回归 |
| **R4** | 竖排/繁体基准语料（公有领域古籍扫描） | 语料 ≥ 20 页，含页眉页脚、双行夹注、竖排正文；人工转写双人校订 |
| **R5** | 端到端 CER 基准 | 报告 `benchmark_report.md`：每引擎/每组合的 CER，横排与竖排分开 |
| **R6** | 效率（页级并发、PDF 句柄复用、结果缓存） | 562 页端到端时间下降 ≥ 40% |

**门槛声明**：R3 与 R4 未完成前，README 不得宣称"支持古籍"。当前状态是
**架构与旧版横排书可用，竖排古籍不可宣称**。

## 5. 竖排仍然是最硬的缺口

竖排不是"精度下降"，是语义错误：`alignment._center_y` 用 Y 轴中心距离配对，
而竖排文本的行是**列**。即使引擎正确输出竖排文本，配对与阅读顺序依然会错。

现有基础（#86 已完成）：

- `PageDirection`（`horizontal-lr/rl`、`vertical-rl/lr`）已在域模型、契约与落库中存在；
- `PageOCRResult.text_line_indexes()` 已能按块类型排除页眉页脚与图表；
- `block_of_line()` 提供了"这一行属于哪个块"的依据。

仍缺（需新 issue，建议 P0）：

1. **方向检测**：目前只有引擎敢报 `direction` 时才有值，没有独立的页面方向判定
   （投影/行列方差比是最小可行方案，与 #85 的算法同源）。
2. **对齐主轴**：`align_pages` 需按方向选择主轴（竖排按 X 轴、从右到左排序），
   并保留 `max_vertical_distance` 的语义一致性。
3. **阅读顺序层**：应独立于对齐存在（#79）。
4. **导出拼接**：竖排换行不应插入行内空格/段落合并规则不同。

## 6. 度量诚实性

- 基准语料目前 6 页，全部为现代简体横排印刷品，**无法验证竖排/繁体/异体字**；
  `benchmark/corpus/v/manifest.json` 的 `ground_truth_note` 已声明参照文本为
  单遍转写、非双人校订。
- 本仓库至今**没有真实 OCR 准确率数字**。在 R5 之前，任何"识别率"表述都必须
  标注为未测量。已有的是**可复现的像素级测量**（预处理分离度、歪斜残差），
  它们不能替代 CER。

## 7. 与其他文档的关系

| 文档 | 内容 |
| --- | --- |
| `docs/ancient-book-support.md` | 能力评估、缺口分析、调研清单（本路线的问题来源） |
| `docs/preprocessing.md` | #85 的实现与实测依据 |
| `docs/ocr-worker-contract.md` | 引擎接入契约（v1 兼容，v2 当前） |
| `docs/book-structure.md` | #87 章节/页码模型 |
| `docs/glyph-variants.md` | #88 规范化层 |
| `docs/page-furniture.md` | #89 页眉页脚剔除 |
| `docs/development-plan.md` | 阶段计划（Phase 5 对应本路线） |
