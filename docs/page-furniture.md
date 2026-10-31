# 页眉页脚剔除（issue #89）

## 1. 问题

旧书每一页都可能有不属于正文的行：书名缩写/部名页眉、出版社行和印刷页码。若把它们当正文，会污染重排导出，也会因两个引擎对页眉读法不同而制造大量无用复核项。

## 2. 检测规则与保守边界

`domain/furniture.py::detect_page_furniture` 在全书 OCR 结果上计算判定。判定理由随结果返回，不会改写或删除原始 OCR 行。

| 理由 | 判定依据 |
| --- | --- |
| `engine_block` | v2 worker 将该行明确标为 `header`/`footer` 块；单行页面仍保守保留 |
| `repeated_in_margin` | 位于页边带，并在全书或同一奇偶页组中重复至少 3 页 |
| `page_number` | 位于页边带，且至少两个观测页码与物理页索引保持同一递增关系 |

页码连续性按 `printed sequence - physical page_index` 分组。因此稀疏样本也可以验证：例如 `page_index` 19、29、39 上的页码 3、13、23 属于同一序列；单个页边数字、与物理页跨度不匹配的跳号或重复但不递增的数字不会仅因“像页码”就被删除。页码重新从 1 开始会形成新的序列组。

页边带为页面上下各 8%（竖排文本使用左右边带）。只出现一页的普通边缘行不会仅凭位置被判定为页眉；正文中间的数字行不作为页码。一个无递增关系的罗马数字标题（例如书名 `V.`）仍可凭跨页重复证据作为 running head 识别；阿拉伯/中文数字若连续性不成立则保留为普通文本。

跨页重复按折叠后的文本比较（#88），因此不同 OCR 引擎对异体字或简繁字的轻微差异不会漏掉重复页眉。对于左右页交替页眉，奇偶页分别累计重复次数。

## 3. 应用、证据与显式保留

| 环节 | 默认行为 |
| --- | --- |
| `assemble_verified_content` | 页眉页脚对应的 pair 不导出，也不要求决策；`VerifiedBook.dropped_furniture` 保留页码、文本和理由 |
| `review_queue` / `open_pairs` | 已判定的页眉页脚不进入默认人工队列或自动重读队列 |
| `review_detail` / `scribeforge review --json` | 显示两侧 OCR 原文、几何及 `furniture_reason`，以便复核判定证据 |
| 导出报告 | 汇总被排除行数及理由；删除是可审计的，不是静默的 |

若要查看或保留原书页眉页脚，可在相关命令显式指定 `--include-page-furniture`。桌面版“复核”页也提供“显示页眉 / 页脚 / 页码”筛选项；“导出”页提供对应的保留选项：

```sh
scribeforge review --store BOOK.scribeforge --include-page-furniture
scribeforge repair --store BOOK.scribeforge --include-page-furniture
scribeforge export --store BOOK.scribeforge --out BOOK.epub --include-page-furniture
```

该开关只改变该次 review/repair/export 对 furniture 的处理，不会改变 OCR 源证据。保留模式下，未裁决的分歧仍需人工决策；它不会绕过可信导出的阻塞条件。默认不带该开关时，行为与现有过滤策略相同。

## 4. 回归范围

六页 V. 风格合成 OCR 夹具覆盖重复 `V.` 页眉、连续页码、默认导出剔除、显式保留，以及两个引擎对页眉意见不同时的复核队列差异：该夹具默认排 6 个正文分歧，开启家具后为 12 个分歧；导出默认剔除 12 行家具，开启保留后输出 18 行（含 6 行正文）。这些是确定性夹具效果，不是实扫 benchmark 的 OCR 指标；不连续/重复页码、正文孤立数字和引擎行索引不一致也有否决用例，防止误删正文。真实引擎输出仍需单独验证。
