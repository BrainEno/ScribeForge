# Data Model

SQLite is the source of truth for project state. Original source files remain immutable.

Core entities:
- `projects`: book identity, source URI/hash, status, timestamps
- `pages`: project/page index, source image reference, dimensions, preprocessing metadata
- `engine_runs`: engine/version/config, page, status, timings
- `blocks`, `lines`, `tokens`: hierarchical OCR geometry and text evidence
- `verification_runs`: immutable comparisons between two OCR engine runs
- `alignments`: relationships between evidence produced by independent engines
- `alignment_conflicts`: character-range disagreements within an aligned pair
- `risks`: reason codes and score for an aligned pair
- `review_candidates`: risky evidence regions queued for VLM/human review
- `vlm_reviews`: append-only literal VLM readings linked to one persisted review candidate, including model identity and crop SHA-256
- `decisions`: accepted text, actor (automatic/VLM/human), timestamp, rationale/evidence refs, and the risk score/reasons that were in force when it was accepted
- `page_confirmations`: append-only statements that a page has no text (blank leaf or plate), with actor, reason, optional ink ratio
- `jobs`: stage, page scope, state, attempts, error, timing
- `exports`: format, verified revision, path/hash

Coordinates use a normalized page coordinate system in addition to engine-native geometry.

A decision never deletes engine evidence. Corrections create/replace the decision layer while source OCR remains immutable. A VLM reading is evidence, not automatically an accepted decision.

Operational job rows are intentionally mutable because they describe execution state rather than source evidence. Retrying a job updates its state, attempt count, error, and timestamps; it does not overwrite OCR, verification, VLM, or decision evidence.

## Implemented persistence slices

Schema version 1 persists `projects`, `pages`, `engine_runs`, `ocr_lines`, `ocr_tokens`, and append-only `decisions` with foreign-key protection. Recording another OCR pass creates a new engine run rather than updating old OCR rows. Decision history stores explicit evidence references and does not mutate source OCR text.

Schema version 2 adds append-only `verification_runs`, `alignments`, `alignment_conflicts`, `risks`, and `review_candidates`. Each verification run references the exact two immutable OCR engine runs that produced it. Recomputing alignment/risk creates a new verification run instead of overwriting the previous comparison. Version 1 databases migrate forward without deleting existing projects or OCR evidence.

Schema version 3 adds append-only `vlm_reviews`. Each row references an existing review candidate, records model/version, literal text, uncertainty, crop path, and the SHA-256 of the exact crop bytes supplied to the model. Re-running a VLM adds another evidence row instead of overwriting prior model output. Version 2 databases migrate forward without deleting verification evidence.

Schema version 4 adds resumable `jobs`. Jobs are uniquely scoped by project, stage, and optional page. `import` is project-scoped; OCR, verification, and VLM review stages are page-scoped. A job records `pending`, `running`, `failed`, or `succeeded` state, attempt count, last failure, and start/finish timestamps. `ensure_job` is idempotent for a given scope. Pending, failed, and interrupted-running jobs remain discoverable for resume; succeeded jobs are not restarted accidentally. Version 3 databases migrate forward without deleting existing evidence.

Job execution uses a nonblocking project-scoped OS writer lock, stored outside the project assets and keyed by the resolved SQLite path plus project ID. Only the store that acquired it may complete or fail its active job. A competing process gets a retryable ownership error without incrementing attempts or changing evidence. The OS releases the lock on process termination; the next process can then reclaim a persisted `running` job, incrementing (not resetting) its attempt history. This needs no schema migration and works with local filesystem locks; do not assume advisory locks are coordinated by every network filesystem.

Schema version 5 rebuilds `jobs` to widen the stage constraint, version 6 adds
`page_structure`, `chapters`, `ocr_blocks` and the line-level confidence/direction
columns, and version 7 records the direction an alignment was computed in.

Schema version 8 adds `engine_runs.scope`. `NULL` means the run read the whole
page; `pair:<n>` means it read one aligned pair from a region crop during a repair
round. Region runs are evidence in their own right (they can support an accepted
decision) but are deliberately invisible to `latest_engine_run`, so a one-line
re-read can never masquerade as the newest reading of a page.

Schema version 9 adds what the [acceptance gate](./acceptance-gate.md) reads and
what it must be able to explain later:

- `risks.min_confidence` — the lower of the two readers' confidences (`NULL` when
  neither reported one), and `risks.content_score` — the part of the risk score that
  comes from evidence quality rather than structural disagreement. Persisting both is
  not optional: a gate that recomputes from an in-memory structure reads
  "no risk" after a reload and silently stops blocking.
- `decisions.risk_score` / `decisions.risk_reasons_json` — the risk a decision was
  accepted under. Policies change; a decision must keep the basis it was judged on.
- `page_confirmations` — append-only confirmation that a page carries no text
  (`kind` in `blank`/`non_text`, `actor` in `human`/`evidence`, free-text `reason`,
  optional `ink_ratio`). Two engines returning nothing is also what a crash looks
  like, so this fact needs a named source.

Version 8 databases migrate forward to 9 without deleting any project, OCR,
verification, or decision evidence.

Schema version 10 stores the evidence behind a page's role and the provenance of a
chapter title:

- `page_structure.ink_ratio` — measured share of non-background pixels. Textless
  pages carry no text whether they are blank leaves or plates, and only the raster
  says which (see [Book structure](./book-structure.md)).
- `chapters.source` — `toc` (the book's own contents page, anchored to a physical
  page through the printed label) or `heading` (one page's typography), or `manual`.
  The two are not equally strong evidence, and an export that mixes them says which
  is which.

Version 9 databases migrate forward to 10 without deleting any evidence.

`exports` remains planned and will be added through a later versioned migration rather than a destructive database reset.

**Schema v11 records the page raster's identity.** `pages` gains four nullable
columns — `page_asset_sha256`, `page_asset_byte_size`,
`page_asset_parameters_json`, `page_asset_version` — populated at import time from
the rendered page's bytes and render parameters (DPI, renderer, source format).
They are nullable so projects imported before v11 keep opening; a NULL digest means
"evidence from before v11" and is reported by the audit, not treated as an error.
`scribeforge rehash-pages` fills the gap for those pages without overwriting an
existing digest, so a changed page can never launder itself through a rehash.

**Schema v12 preserves token availability separately from token rows.**
`ocr_lines.tokens_reported` records whether the worker supplied a token array:
`0` means unavailable/unknown (`tokens` omitted or `null`), while `1` means the
worker reported the token list, including an explicitly empty list. `ocr_tokens`
continues to hold each token's own text, confidence (nullable), and geometry. The
v11-to-v12 migration marks lines with existing token rows as reported; old lines
without rows remain unknown because v11 could not distinguish missing tokens from
an explicit empty array. This is a conservative migration: it preserves evidence
without manufacturing certainty.

The import pipeline writes these page-asset columns for every page it materializes,
the orchestrator verifies the source raster against them before any engine reads it,
the audit reports missing / mismatched / unreadable page assets, and `scribeforge
export` re-checks the digest before a cover or plate is embedded. The bytes and
their SHA-256 also travel on `VerifiedImage` (in memory only), and the export
check re-derives the digest from the archive entry it wrote: the image a reader
sees is provably the image that was imported, measured, and audited.

One consequence of the chapter layout is worth stating here because it changes an
old assumption: **a document in the exported EPUB is no longer a page.** One
XHTML file now holds a whole chapter, so text can no longer be attributed to a
single page by looking at which file it is in. Nothing in the database changes —
`VerifiedPage.lines` and `page_structure` stay per page — and the exported document
keeps a `pagebreak` marker per page, so the mapping back to evidence survives.
Anything that wants to infer "page N = file N" must use `--layout page`, where that
identity holds by construction. The raw `export-candidates` path is unaffected: it
is a separate writer (`scribeforge.adapters.candidate_epub`) that still emits one
spine item per source page, which is what the Flutter comparator aligns on. See
[Export](./export.md).

## Decision resolution

The decision layer needs no migration: it writes schema-v1 `decisions` rows through `scribeforge.pipeline.adjudication`. `adjudicate_pair` applies one deterministic rule — independent engine agreement is accepted as `automatic`, a confident non-blank VLM reading is accepted as `vlm` **only when it corroborates one of the engines**, and every other pair stays unresolved for human review. A model that disagrees with both engines is a third claim, not a second vote (issue #76).

Each recorded decision cites the verification run, both OCR engine runs, and the VLM reading when one was used.

Unresolved pairs can be re-read instead of only queued: `scribeforge.pipeline.rounds`
adds region-scoped engine runs (`engine_runs.scope = "pair:<n>"`) and accepts a text
when the readings reach consensus — at least two independent readers, strictly more
than any rival group. Those decisions cite the fresh runs and use the same
`automatic` / `vlm` actors, so the decision layer stays one append-only table.

Re-running adjudication is idempotent because a pair that already has a decision is left untouched; source OCR is never rewritten.

A human resolves a pair by appending a `decisions` row with `actor = human` (`record_human_decision`, exposed as `scribeforge resolve`). It cites the same evidence and, being newest, becomes the pair's latest decision while earlier rows are preserved, so a human can correct an `automatic` or `vlm` acceptance without mutating any engine, verification, or VLM evidence row.

`adjudicate_pair` additionally passes every automatic path through the
[acceptance gate](./acceptance-gate.md): agreement no longer suffices when the
accepted text is unrepresentable (`U+FFFD`, control characters), when the worst
reader admits low confidence, or when the pair's content risk exceeds the policy
ceiling. Blocked pairs are recorded as unresolved and stay in `review_queue`, which
lists **every undecided pair** rather than only score-thresholded candidates — a
queue built from candidates could report "0 queued" while export stayed blocked.

Export reads only this decision layer: `assemble_verified_content` takes the newest decision per pair and blocks when any page or pair is unresolved, so the reader output is always derived from accepted, cited evidence rather than raw OCR. It also blocks a page that produced zero aligned pairs until `page_confirmations` records why (`scribeforge confirm`), because "no pairs" is equally the signature of a failed read.

**Schema v13: OCR 坐标帧。** 新的 `ocr_input_frames` 用 run id 绑定整页 OCR
使用的帧类型（原始/预处理）、SHA-256 和宽高。两个 run 的帧不一致或任何一个
没有绑定时，不能进行基于其坐标的自动裁切；v12 及更早项目的历史 run 在迁移后
保持未知，不按是否存在预处理记录猜测。旧证据与人工决策保留不变；需要自动重读的
历史项目应将源文件重新导入新项目，或从有明确帧证据的备份恢复，不能通过重新哈希
将不确定的历史帧变成已知帧。当前 region-scope OCR 的结果不参与页帧匹配，页帧
记录针对整页识别。

重新处理已成功页面时要求所选择的帧与原 run 相同；改变预处理开关或参数不会把
不同帧的两个引擎结果合并。图像缺失、变更或帧尺寸不符会拒绝自动重读。备份恢复
会重定位已归档的 `preprocessed/` 路径；创建或验证备份时，记录的预处理图必须
能唯一映射到归档中的 `preprocessed/` 文件，否则拒绝备份或验证。

**Schema v14: 裁剪输入证据。** 新的 `crop_input_evidence` 与区域 OCR run 或 VLM
review 在同一数据库事务中写入，记录 verification、pair、页帧摘要、裁剪摘要和路径、
页帧像素矩形与放大倍率。裁剪尺寸为矩形宽高乘倍率，坐标位于绑定的整页帧。
模型调用前后验证裁剪，变化或缺失时拒绝保存该次输出。每个模型调用使用独立文件，
路径冲突时分配新名称而不覆盖旧文件；失败或中断留下的文件也不被重用。备份恢复会
重定位项目内 `crops/` 路径。`review --json` 仅展示绑定且摘要匹配的 VLM 裁剪。

v13 及更早已有的 VLM 和区域 OCR 记录不会被猜测补填几何；恢复自动处理时若需使用
未知 VLM 裁剪证据会拒绝，保留旧记录供人工审计，并提示重新导入。低层
`materialize_review_crops` 默认创建不覆盖的独立文件；显式指定 `immutable=False`
仍可使用已弃用的旧替换模式，只能用于一次性暂存目录，不能用于证据目录。该旧模式
会发出弃用警告并拒绝覆盖输入图本身；生产 OCR/VLM 流水线始终使用安全模式。模型调用前后校验不是
针对有权限同时替换又还原文件的恶意进程的完整防护。

`scribeforge audit` 只读核验已绑定的整页 OCR 帧和区域模型裁剪的摘要、尺寸、
页帧与验证关联。已知证据缺失或不一致是错误并阻止备份；旧记录缺少绑定以警告
呈现，不做反推或补写，也不自动阻止备份；历史 VLM 已记录的裁剪摘要仍会核对，
但摘要匹配并不证明该未知几何或实际模型输入。审计不能证明模型没有在两次哈希之间
读取被替换后又还原的文件，或证明历史的未知帧。

**模型输入副本。** 整页 OCR 以及带 `crop_input_evidence` 的区域 OCR/VLM
生产调用收到每次独立的临时字节副本，而不是源帧或已保存裁剪路径。调用前后
验证副本及证据文件；正常退出或 Python 异常时清理副本，检测到变更不记录本次
结果。证据仍引用持久帧和裁剪，不引用临时路径。该措施不是同权限恶意程序的
沙箱：不能阻止其自行访问其他路径，也不能检测调用中替换后恢复；强杀进程可能
留下临时文件。没有 crop provenance 的旧式 `review_pending_candidates` 直接调用
仍保留兼容行为，不能视为已隔离。

基准 Oracle 在已登记路径外读到隔离副本时，按登记时的文件 SHA-256 匹配；
同内容若对应不同文本或方向则拒绝歧义，不按文件名或登记顺序猜测。已登记路径
仍按原有路径语义处理。

**备份快照复核。** 创建备份先审计活动项目，再制作 SQLite 快照并归档；发布归档前
还会将实际归档临时恢复，按快照中的摘要、尺寸和关联重新审计。归档必须包含每个
已记录的原始页、预处理图、区域 OCR 裁剪及 VLM 裁剪（包括旧 VLM 已知裁剪）；
无法唯一映射时拒绝备份或验证。临时恢复发现错误、或警告与活动项目审计不一致时
拒绝发布，既有备份不会被替换。为进行这次校验，临时目录需要足够空间容纳一次
解压后的项目；异常时清理，强制终止进程可能遗留临时文件。

`verify-backup` 除检查归档结构、文件摘要、SQLite 和引用映射外，也会在临时目录
恢复并审计实际证据，返回重新计算的警告而不是信任归档清单中的警告。警告中
`archive:/` 表示归档内路径，不是本地路径；验证后临时文件会清理。验证同样需要
足够的临时磁盘空间，强制终止进程可能遗留临时文件。哈希和审计无法证明归档来源
真实、未知的历史几何，或阻止同权限恶意进程在检查之间修改又恢复文件。
