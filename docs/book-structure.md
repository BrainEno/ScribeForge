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
PageStructure(page_index, role, label)
PageLabel(printed_label, scheme, sequence, sequence_ordinal)
Chapter(ordinal, level, title, start_page_index, printed_page)
BookStructure(pages, chapters)
```

- `PageRole`：cover / front_matter / copyright / table_of_contents / body / plate /
  blank / back_matter / unknown。`is_textual` 用于区分"读者会按顺序读的文字页"。
- `NumberingScheme`：none / arabic / roman_lower / roman_upper / cjk / unknown。
- `sequence_ordinal = 编号段号 × 1_000_000 + 段内序号`：同一个"5"出现在两段编号里
  也不会混淆（`docs/book-structure.md` 之所以需要它，正是因为本书有三段编号）。

持久化（schema v6）：

| 表 | 内容 |
| --- | --- |
| `page_structure` | 每页的 role、印刷标签、方案、序号、段内序号 |
| `chapters` | 章节树（ordinal/level/title/start_page_index/printed_page） |
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

## 4. 导出行为

- EPUB 导航由章节树生成，按 level 嵌套（`<ol>` 栈式生成，含 XHTML 结构校验）；
- 章节树为空时回退到旧的逐页列表，保证向后兼容；
- 每页 `<title>` 使用印刷页码（`Page 3`），而不是物理序号；
- 章节锚定页若未通过校验则被剔除，避免导航指向不存在的文件。

## 5. 命令

```bash
# 处理完成后自动派生（失败不致命，回退到物理页码）
scribeforge process book.pdf

# 对已处理的 store 重新派生（例如换了引擎或调整了启发式）
scribeforge structure --store book.scribeforge --engine mineru
scribeforge structure --store book.scribeforge --json
```

## 6. 已知边界

- 页码位数极小的罗马数字与单字母页眉仍可能冲突；单点检测已被序列规则过滤，
  但整段页眉都是罗马数字时仍可能误判。
- 章节标题的提取依赖引擎是否上报 title 块与字号；对不上报块级别的引擎（纯 PP-OCR），
  仅凭"字号大于正文 + 模式匹配"判断，召回率有限。
- 尚未实现目录页解析（`table_of_contents` 角色已定义但未从目录页文本回填章节）。
