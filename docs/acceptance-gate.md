# 自动接受闸门（Acceptance Gate）

> 解决的问题：**两个引擎意见一致，不等于这段文字可以被机器签字放行。**
> 对应 issue：#75（高风险行被一致规则放行）、#74（双引擎空结果被当作空白页）。

ScribeForge 的导出只读取“已接受的决策层”。在引入本闸门之前，一条配对只要满足
“两个引擎读出来一样”，就会被写成 `actor = automatic` 的决策，然后从复核队列消失、
进入成品。实践中有三类文本会从这条路径漏过去：

1. 两个引擎都读到 `U+FFFD`（“这个字形我读不出来”），一致地一致；
2. 两个引擎都以 0.1 的置信度读同一行 —— 两个猜测互相印证并不构成证据；
3. 两个引擎把两行**并不相邻**的文字当成同一行（空间错配）。

第 1、2 类根本不可读，第 3 类可能压根不是同一行。闸门的作用就是把这三类挡在
“机器签字”之外，交给视觉模型、重读轮次或人来处理。

## 两个不同的风险数字

`PairRisk` 现在有两个分数：

| 字段 | 含义 | 闸门是否读取 |
| --- | --- | --- |
| `score` | 全部风险信号之和（含结构性分歧） | **否** |
| `content_score` | 只累计证据质量类信号：低置信度 0.25、Unicode 异常 0.35、空间错配 0.15 | **是** |
| `min_confidence` | 两个引擎中较低的那一个置信度；都没报时为 `None` | **是** |

这个拆分是必需的，不是洁癖。结构性分歧（`TEXT_DISAGREEMENT` 0.45）意味着“两个引擎
读得不一样”，而**这正是视觉模型被请来处理的情况**：VLM 解决之后，分歧已经是一个
有结论的问题。如果闸门读 `score`，那么每一个被 VLM 解决的配对都会因为残留的 0.45 被
拦下，VLM 功能等于被静默废掉（`tests/test_adjudication.py` 里有两个测试专门盯这一点）。

## 策略

```python
AcceptancePolicy(
    max_content_risk=0.34,   # content_score 高于此值 → 机器不得接受
    min_confidence=0.30,     # 最差读数的置信度低于此值 → 机器不得接受
    blocking_reasons={UNICODE_ANOMALY, SPATIAL_MISMATCH},
    spot_check_risk=0.25,    # 已接受但达到此值 → 值得抽查
)
```

`machine_blockers(text=…, risk=…, policy=…)` 返回有序的原因码，顺序固定，便于断言与
复现：

- 文本缺陷：`blank_text`、`unrepresentable_text`
- 风险原因：`unicode_anomaly`、`spatial_mismatch`（来自 `blocking_reasons`，按
  `RiskReason` 的声明顺序输出，不随写入顺序变化）
- 分数与置信度：`high_content_risk`、`low_confidence`

`unrepresentable_text` 覆盖 `U+FFFD` 以及 XML 1.0 无法承载的控制字符（`\t`、`\n`、
`\r` 除外）。`unrepresentable_characters()` 会给出具体的 `U+000B` 这类名字，因为
“这一行被拦了”不可操作，“这一行含有 U+000B”才可操作。

## 闸门管到哪里

`adjudicate_pair` 的三条自动路径 —— `engines_agree`、`variant_agreement`、
`vlm_confident` —— 全部经过同一个 `_gated()`。被拦下的配对返回
`blockers`/`blocked=True`，不写决策，留在队列里。

**闸门不会拦人。** `scribeforge resolve` 记录的人工决策不做闸门判断：人可以接受任何
文字，只是那条决策会署名为 `human`，并在导出的接受度汇总里体现出来。

## 队列必须与闸门一致

闸门上线后必须先修一个连带缺陷：复核队列过去只列 `review_candidates`。候选阈值
（0.35）高于低置信度配对的风险分（0.25），于是导出被闸门挡住，却没有对应的裁切图。
现在默认候选阈值为 **0.25**，确保低置信度也能生成复核 crop；`review_queue` 另外仍然
**按对齐配对全量列举未决项**，排序 `(-score, page, pair)`，只跳过页面家具行和
“双引擎都返回空行”的配对。

同理，导出报错不再只说“有 1 个未决配对”，而是逐条列出原因：

```
error: cannot export unresolved content:
  1 unresolved pair(s):
    page 0 pair 0: unrepresentable_text (U+FFFD)
    inspect with 'scribeforge review'
    decide with 'scribeforge resolve --page N --pair M --text ...'
```

## 报告口径：`open_blocked`

`progress()` 除了 `open_pairs` / `open_high_risk`，还给出 `open_blocked` —— 未决配对
中**闸门拒绝让机器接受**的数量。它与 `open_high_risk`（按总分阈值）不是同一个集合：
一条 `+0.35` 的 Unicode 异常行可能没到 0.6 的“高风险”，却已经越过 0.34 的接受上限。
驱动动作也不一样：高风险行需要一次裁决，`open_blocked` 行需要**换一次输入再读**
（`scribeforge repair` 的放大裁剪重读、第三引擎或 VLM）。

## 空白页必须显式确认（issue #74）

一页零配对**不是**空白的证据：两个 worker 崩溃、栅格解码失败、扫描缺页、真的一页
空白，在 OCR 表里长得一模一样。旧行为是直接组装出一个空页导出，等于让“漏识别”
冒充“空白页”。

现在：

1. 零对齐配对的页面若没有确认记录，`assemble_verified_content` 抛
   `UnresolvedContentError(unconfirmed_pages=…)`；
2. 操作者用 `scribeforge confirm --page N --kind blank|non_text --reason "…"` 明确记录；
3. `page_confirmations` 是**追加式**表（`kind`、`actor`、`reason`、`ink_ratio`、
   时间戳），所以“某年某月某人说这页是空白”的历史不会被后来的读数改写。

另外，若某一配对里**两个引擎都返回空行**，它既不是内容也不阻塞页面：它被记为
`AdjudicationResult.empty` / `VerifiedBook.empty_pairs`，对外可见但不写决策 ——
给空字符串写决策会造出空段落，并把整页漏识别伪装成空白页。

## 导出时的接受度汇总

`VerifiedBook.acceptance` 是 `AcceptanceSummary`：页数、配对总数、机器接受数、人工
接受数、机器接受部分的最大内容风险、以及需要抽查的配对列表
（`SuspectAcceptance`，含 `actor`、`content_risk`、`reasons`、可选 `note`）。

- 默认导出照常进行，但会打印警告：`N accepted pair(s) need a spot check`；
- `--strict-acceptance` 把这条警告升级为拒绝（退出码 2）；
- `--json` 输出完整机器可读报告：`machine_accepted` / `human_accepted` /
  `machine_acceptance` / `max_machine_content_risk` / `requires_human_review` /
  `spot_check[]`。

这样“已验证”不再是一个二值状态：一本书可以完全由机器接受（风险低），也可以是
人工接受了全部争议行，操作者能在交付前看到到底是哪一种。

## 数据模型变化（schema v9）

- `decisions.risk_score`、`decisions.risk_reasons_json`：决策写入时风险是多少。
  闸门策略以后可能调整，历史决策必须保留它当时被判定时的依据。
- `risks.min_confidence`、`risks.content_score`：闸门读取的两个字段必须落库，否则
  重新加载后闸门形同虚设（实现过程中确实先踩了这个坑：风险只存在内存里，重读后
  `blocked=()`）。
- `page_confirmations`：追加式空白页/非文本页确认。

v8 → v9 为纯增量迁移，不删除任何既有项目、OCR、验证或决策证据。

## 为什么不做“首轮只接受最低风险的一半”

曾考虑让首轮裁决只接受风险最低的一半配对，逐轮降低门槛。最终没有实现：闸门是
**逐配对、顺序无关**的确定性判定，按风险排序不会改变任何一对的结论，只会让“哪些
配对现在可以导出”依赖执行顺序 —— 反而更难推理。真正需要“先低风险”的地方是
VLM/重读的**预算分配**，那是 `review_queue` 排序已经解决的问题。

## 测试锚点

- `tests/test_acceptance.py`：`U+FFFD`、控制字符、合法空白、空文本、
  `content_risk`、低置信度、`spot_check`、策略校验、原因码顺序。
- `tests/test_adjudication.py`：`#75` 回归 —— 同文 `U+FFFD` / 0.1 置信度必须留在队列
  且不产生决策；结构性分歧单独出现时不得阻塞（否则 VLM 失效）；双空行不写决策。
- `tests/test_content_assembly.py`：`#74` 回归 —— 未确认的零配对页面阻塞导出，确认后
  导出为空页；双空行配对记为 `empty_pairs`。
- `tests/test_page_storage.py`：v8 → v9 迁移保留项目、补齐列与新表。
- `tests/test_cli.py`：`confirm` 子命令、拒绝文案、`--strict-acceptance`、`--json`。
