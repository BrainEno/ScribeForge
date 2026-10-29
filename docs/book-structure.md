# 章节 / 印刷页码 / 页面角色模型（issue #87）

## 1. 问题

旧书扫描件的"页"有两个互不相同的概念：

- **物理页序号** `page_index`：PDF 索引，所有 OCR/风险/决策证据行都引用它；
- **印刷页码**：读者在边栏看到的那个数字。

把两者混为一谈，输出就会坏掉。本书（`benchmark/V..pdf`，562 页）的具体情况：

- 全书有**三段互不相同的编号**（前言区、正文、后部），编号会重新从 1 开始；
- 存在**无编号页**（空白页 2、3、18、57，以及插图页）；
- 章名与页码不在同一行，页眉里还有书名缩写 `V.`——它恰好是一个合法的罗马数字 5。

旧实现直接按物理序导出，结果是 EPUB 导航里 562 条平铺的 `Page 1`…`Page 562`：
这根本不是目录，读者无法用它定位章节。

## 2. 数据模型

```
PageStructure(page_index, role, label, ink_ratio)
PageLabel(printed_label, scheme, sequence, sequence_ordinal)
Chapter(ordinal, level, title, start_page_index, printed_page, source)
BookStructure(pages, chapters, toc_entries, unplaced_toc_titles)
```

- `PageRole`：cover / front_matter / copyright / table_of_contents / body / plate /
  blank / back_matter / unknown。`is_textual` 用于区分"读者会按顺序读的文字页"。
- `NumberingScheme`：none / arabic / roman_lower / roman_upper / cjk / unknown。
- `sequence_ordinal = 编号段号 × 1_000_000 + 段内序号`：同一个"5"出现在两段编号里
  也不会混淆（`docs/book-structure.md` 之所以需要它，正是因为本书有三段编号）。

- `Chapter.source`：`toc`（书自己的目录页）/ `heading`（页面排版）/ `manual`。
  两者证据强度不同，导出与报告必须能区分。

持久化（schema v6，v10 补充）：

| 表 | 内容 |
| --- | --- |
| `page_structure` | 每页的 role、印刷标签、方案、序号、段内序号、**ink_ratio（v10）** |
| `chapters` | 章节树（ordinal/level/title/start_page_index/printed_page/**source（v10）**） |
| `ocr_blocks` | **新增**：#86 的块证据此前只存在于域层，没有落库 |

> 真实缺陷记录：`ocr_blocks`、`engine_runs.direction`、`ocr_lines.confidence` 在 #86 里
> 只改了域模型与 worker 输出，`record_ocr`/`load_ocr_run` 未持久化。结果是结构检测
> 从库里读回的 OCR 结果 `blocks` 恒为空——"页眉页脚块"这条最可靠的页码线索从未生效。
> 本次在 v6 一并补上（v6 用 `ALTER TABLE` 加列，不改 CHECK，无需重建表）。

## 3. 检测规则（保守优先）

**页码**（`detect_page_label`）：

1. 只看页边带（顶部/底部 10%）。正文里数字极多（"1923"、"第十二章"），
   把其中一个当成页码会污染其后所有页的编号。
2. 优先使用引擎声明的 `header`/`footer` 块（#86 的 `BlockType.is_page_furniture`）；
   没有块证据时退回几何判断。
3. 解析严格：阿拉伯数字、规范罗马数字、中文数字。`译林出版社`、`1923年`、`12abc`
   一律返回"无编号"——**宁缺勿错**，错页码会连带毁掉所有交叉引用。

**编号序列**（`assign_sequences`）：

- 数字倒退 → 认为编号重新开始；
- 一次跳跃超过 3 → 认为是误识别，丢弃该点；
- **只出现一次的序列整体丢弃**：本书页眉里的 `V.` 就是单点误报的典型；
- 因此真实的前置罗马页码（多页连续）保留，孤立的 `V.` 被丢弃。

**章节**（`detect_chapters`）：

- 需要两个独立信号：引擎标为 `title` 块，或文本匹配章/节/序等模式；
- 且该行字号大于本页正文（`≥1.15×`），或已被引擎声明为 title 块；
- 级别由字号比推断：`≥1.6×` 为 1 级，`≥1.3×` 为 2 级，否则 3 级；
- 正文中提及"第一章"不会被当成标题（字号不满足）。

> 真实缺陷记录：正文高度估计最初把所有行（含标题行）纳入中位数。两行页面上
> `median([标题0.05, 正文0.02]) = 0.05`，于是每个标题的"字号比"都是 1.0，**任何章节
> 都检测不出来**。修正为排除已声明 title 块后再取中位数。

**页面角色**（`classify_page_roles`，整本书一起判断）：

只看引擎报告的行、页码序列和**像素墨迹度量**，不看文件名或版式先验。规则按顺序：

1. 没有任何文字：墨迹 `< PLATE_INK_RATIO` → `blank`，否则 → `plate`
   （插页/图版；也可能是一整页文字没被识别出来）。**扫描的第一页**若是这种图版
   → `cover`：引擎从封面图画上读不出文字，墨迹无法把它和插图页区分开，位置是
   第二个信号——装订成册的书，第一页就是封面；
2. 出现 ≥2 个版权页特征（`ISBN`、`图书在版编目`、`版权`、`责任编辑`、`开本`…）且无页码 → `copyright`；
3. 页面像目录（首三行有 `目录`/`Contents` 标题，或 ≥3 行且过半行都解析成"标题…页码"）→ `table_of_contents`；
4. 第一张有文字的页，无页码、无标题行，且（文字 ≤40 字 或 墨迹 ≥50%）→ `cover`；
   本 PDF 的彩色封面实测墨迹 94%，所以"整页都是颜色"本身就是封面的证据；
5. 其余页按**最长编号序列**（=正文）划分：序列之前 → `front_matter`，
   序列之内 → `body`，序列之后 → `back_matter`；
6. 没有编号序列（无法区分前后）→ `unknown`。未知页按正文导出，这是安全的失败方向。

**墨迹度量**（`adapters/page_ink.measure_page_ink`）：

页面缩到最长边 512 px、转灰度、统计亮度 < 200 的像素占比。它不是图像质量指标，
只用来回答"这页上到底有没有东西"。阈值由 `benchmark/V..pdf` 的真实页面标定
（300 dpi，512 px 归一化）：

| 真实页 | 墨迹 | 说明 |
| --- | ---: | --- |
| 第 3 页 | **0.39%** | 空白分隔页 |
| 第 19 页 | **2.45%** | 文字很少的正文页 |
| 第 562 页 | 3.40% | 末页 |
| 第 4 页（出版说明） | 3.99% | 稀疏正文 |
| 第 5/17/18 页 | 6.3–6.8% | 正文 |
| 第 281 页 | 12.6% | 正文 |
| 第 1 页（彩色封面） | **94%** | 整页是颜色，不是纸 |

度量成本：实测 2481×3728 的 300 dpi 页面 **约 0.14 s/页**（Python/PIL，缩放到 512 px），
562 页约 80 秒，相对 OCR 耗时可忽略。

由此得到两个阈值：

- `BLANK_INK_RATIO = 1%`：低于此值，页面上没有任何可读内容，**度量本身**即可把它
  确认为空白页（`page_confirmations.actor = evidence`）。它落在实测的 0.39% 与
  2.45% 之间，两侧都有余量。
- `PLATE_INK_RATIO = 2%`：高于此值而引擎没有读出文字 → `plate`，**永不自动确认**：
  插页、整页识别失败、只有一行小字的近空白页都在这一档，需要人来说它是什么。

**目录页 → 章节树**（`parse_toc_entry` / `map_toc_chapters`）：

目录页的每一行（`第一章 万有引力之虹 …… 13`）解析成 `TocEntry(title, printed_label, kind)`。
但要锚定到物理页还需要**第二个独立信号**：

1. 印刷页码把候选缩小到"同一编号方案 + 同一序号"的页（本书有三段编号，`1` 会命中三页）；
2. 该页自己的标题行必须与目录条目标题对得上（去空格、去标点后互相包含）。

两条都满足才锚定，否则该条目进入 `unplaced_toc_titles` 如实上报，章节树退回逐页
标题检测。**不猜**：锚错一章会让后面所有章节的页码引用都错。

## 4. 导出行为

- EPUB 导航由章节树生成，按 level 嵌套（`<ol>` 栈式生成，含 XHTML 结构校验）；
- 章节树为空时回退到旧的逐页列表，保证向后兼容；
- **一章一个 XHTML 文档**（`doc-000001.xhtml`…，见 `domain/flow.py`）：
  章节不再是目录里的一条链接，而是真正的文档边界，跨页段落因此能合成一句；
  每个被覆盖的扫描页在文档内留 `<span epub:type="pagebreak" id="page-N"
  title="印刷页码"/>`，`--layout page` 可退回逐页对照的旧形状；
- 章节树为空时退回逐页文档（`page-000001.xhtml`…）；
- 每页的印刷页码写进 pagebreak marker 的 `title`（`--layout page` 时写进
  该文档的 `<title>`），而不是物理序号；
- 章节锚定页若未通过校验则被剔除，避免导航指向不存在的文件；
- 章节来源（`toc` / `heading`）写入 `chapters.source`，导出报告与 `structure --json` 都会给出；
- 没有文字但有墨迹的页（`plate`）在确认前挡住导出，拒绝信息里会带上**实测墨迹**：
  `page 7: measured ink 6.93% — there is content here that was not read`，
  让操作者一眼区分"漏识别"和"真的空白"；
- 确认之后，这些页**作为图片写进 EPUB**：`cover` → 封面（`properties="cover-image"`
  且 `meta name="cover"`），`plate`/人工 `--kind non_text` → 正文里的 `<figure>`，
  落在它自己的 pagebreak 之后。整页文字永远不镜像（正文走重排文本），
  确认过的空白页也不写图。图片字节按 SHA-256 校验，导出后仍与页图证据逐字节相同；
  细节见 [Export](./export.md)。

## 5. 命令

```bash
# 处理完成后自动派生（失败不致命，回退到物理页码）
scribeforge process book.pdf

# 对已处理的 store 重新派生（例如换了引擎或调整了启发式）
scribeforge structure --store book.scribeforge --engine mineru
scribeforge structure --store book.scribeforge --json
```

`process` 的一行摘要现在长这样：

```
structure: pages 562, labelled 498, chapters 12 from toc (contents lists 14, 12 anchored); blank 9, plate 3
```

`structure --json` 额外给出 `roles`、`blank_pages`、`plate_pages`、`unmeasured_pages`、
`confirmed_textless`、`toc_entries`、`toc_mapped`、`toc_unmapped`、`chapter_source`。

## 6. 已知边界

- 页码位数极小的罗马数字与单字母页眉仍可能冲突；单点检测已被序列规则过滤，
  但整段页眉都是罗马数字时仍可能误判。
- 章节标题的提取依赖引擎是否上报 title 块与字号；对不上报块级别的引擎（纯 PP-OCR），
  仅凭"字号大于正文 + 模式匹配"判断，召回率有限。
- 目录页解析要求"标题……页码"在同一行。目录条目折行（标题一行、页码一行）时会解析失败，
  该页只会得到 `table_of_contents` 角色而不会贡献章节。
- 目录条目里页码是中文数字（`二一`）时会被解析成数值，这类写法本书没有出现，未经真实语料验证。
- 章节来源以目录优先，但目录本身如果漏列章节（合订本常见），漏掉的章节不会从标题检测补回来。
- 角色不参与复核队列的取舍：`body`/`front_matter` 都照常进入队列，`plate` 页在确认前会
  挡住导出（不会静默丢弃插图页）。
- "扫描第一页的图版就是封面"是一个**位置先验**：若这本扫描没有拍到封面（首图其实是
  卷首插图）或第一页正面就是一张插图，它会被当成封面。这一点无法从单页证据排除，
  所以写在这里而不是藏进代码。
- DOCX 导出**不含**页图（只有 EPUB 会写图片），命令行会为此打印一条 warning。
- 墨迹度量依赖页图可读：`restore` 之后若页图路径失效，`unmeasured_pages` 会如实列出，
  这类页既不会被自动确认为空白，也不会被误判成 plate。
