# 基准语料规范（含竖排 / 繁体语料要求）

- 更新日期：2026-10-30
- 适用范围：`scribeforge benchmark`、`benchmark/corpus/*`、R4/R5 验收

> 本文只描述**语料怎么算合格**。现有语料的实际内容见
> [`benchmark/corpus/v/manifest.json`](../benchmark/corpus/v/manifest.json)；
> 能力评估见 [`ancient-book-support.md`](./ancient-book-support.md)。

---

## 1. 一条合格语料页的门槛

| 要求 | 判定方式 | 为什么 |
| --- | --- | --- |
| **真实页面** | 来自真实扫描件/出版物，能指到具体 PDF 物理页 | 合成或重排页面测的是渲染器，不是 OCR。任何"把横排页转 90°"当作竖排语料的做法都被明确排除：那既是伪造页面，也无法体现竖排的字形连写与行间关系 |
| **权限清晰** | 公有领域或已获授权；在 manifest 里记录来源 | 语料会随仓库分发 |
| **人工转写** | 逐行、保留原始标点与异体字 | CER 的分母就是它 |
| **可复现** | 图像 + 转写 + manifest 一并入库，绝对路径不允许 | 无法复现的数字不是证据 |
| **页面内容健康** | 有正文，不是纯图版/纯空白 | 空页面会把 CER 稀释成好看的数字 |

## 2. manifest 字段

```json
{
  "schema_version": 1,
  "ground_truth_review_status": "unreviewed",
  "ground_truth_note": "Describe how the reference transcriptions were produced and checked.",
  "cases": [
    {
      "id": "gj-001",
      "image": "pages/gj-001.png",
      "ground_truth": "truth/gj-001.txt",
      "language": "zh-Hant",
      "tags": ["body", "vertical", "double-column-notes", "song-edition"],
      "direction": "vertical-rl"
    }
  ]
}
```

- `direction` 是**可选**字段，取值 `horizontal-lr` / `horizontal-rl` /
  `vertical-rl` / `vertical-lr`。它只用于报告分组（横排/竖排分开看 CER），
  不参与管线判定——管线仍然用几何检测方向。写错只会让报告分组错，不会污染运行。
- 缺省 `direction` 时，报告把该页归入 `horizontal-lr`：现有 6 页确实都是横排。
- `tags` 自由填写，用于在报告里定位某一类页面（如 `footnote`、`latin-inline`）。
- `ground_truth_review_status` 是语料级的显式转写校订状态：`unreviewed`、
  `single-pass` 或 `double-proofread`。只有确实完成两人独立转写并互相核对，
  才能声明 `double-proofread`；缺省表示 `not declared`，不会根据说明文字或标签推断。
- `ground_truth_note` 记录转写方法、校订范围与限制；基准报告会显示这些状态和说明
  （说明中的空白会规范成单行），让 CER 不会脱离参照文本的证据质量。此状态是数据集
  维护者的声明，并非 ScribeForge 对文本正确性的认证；`unreviewed` 和未声明状态也可用于
  开发回归，但不能被描述成金标准。

## 3. 竖排 / 繁体语料的最低规模（R4）

| 项 | 要求 |
| --- | --- |
| 页数 | ≥ 20 页 |
| 方向 | 全部 `vertical-rl`（古籍主流）；若收 `vertical-lr`，须单独标注 |
| 字形 | 繁体为主，至少 1 页含异体字（用于验证 `glyph-variants` 只影响比较、不改写导出） |
| 版面元素 | 覆盖页眉、页脚/版心、双行夹注、居中诗行、单字成行、跨页段落 |
| 校订 | 两人独立转写并互相核对；未复核的行须在 manifest 里标 `tags: ["single-pass"]` |
| 体积 | 单页 PNG 控制在 1 MB 以内（灰度、约 200 DPI），整包 < 30 MB |

达到 R4 后，`scribeforge benchmark --manifest benchmark/corpus/ancient/manifest.json
--out benchmark/ancient_report.md --include-pipeline` 就是 R5 要求的数字来源。

## 4. 现有语料

### 4.1 `corpus/v`（6 页，现代简体横排）

《V.》叶华年译本（译林 2008）的 6 个真实页面，含章首页、居中诗行、脚注、
拉丁文夹用。**全部为横排简体**，用于横排回归。

已知不足：

1. 无法验证竖排、繁体、异体字；
2. 参照文本单次转写，未经二次校订；
3. 图像已入库但源 PDF 受版权保护。

这 6 页同时是**段落规则**的黄金用例：把真值按本书版心（28 字）重新折行，
再要求规则还原出 36 / 36 段（161 行 → 36 段，零丢字）。见
[段落与结构保真](./paragraphs.md) 与 `tests/test_paragraphs.py`。

### 4.2 `corpus/guji`（3 页，繁体竖排刻本）

《欽定四庫全書簡明目錄》卷首聖諭（清乾隆武英殿刻本，公有领域），取自
`open-guji/open-guji-cv` 的测试夹具（该仓库 Apache-2.0）。半葉八行二十一字、
四周雙邊，含版心書名、單字成行（奉）與二字成行（聖諭）——正是横排逻辑会配错
的那种版面。

首批转写为单人完成，未经二次校订；R4 要求 ≥20 页且双人校订，**尚未达标**。

在它之上跑出的实测（识别误差由 oracle 强制为 0，只测管线自身）：

| 变体 | 页数 | 平均 CER | 缺失/多余行 | 已接受/未解决配对 |
| --- | ---: | ---: | ---: | ---: |
| `oracle`（引擎层） | 3 | 0.0000 | 0 / 0 | — |
| `oracle-ceiling`（全链路） | 3 | 0.0000 | 0 / 0 | 24 / 0 |

同时有三条回归测试守护：真实页 CER 为 0；把转写顺序反转必须使 CER > 0.2；
去掉方向声明后仅凭几何仍判为 `vertical-rl`。

## 5. 从真实扫描件切出语料

```bash
python benchmark/analysis/build_corpus.py \
  --pdf /path/to/public-domain-scan.pdf \
  --out benchmark/corpus/ancient --name gj \
  --pages 3,7,11 --direction vertical-rl \
  --language zh-Hant --dpi 200 \
  --source "《四部叢刊》影印本，公有领域"
```

脚本只做机械部分：按物理页号渲染灰度 PNG、写 manifest、生成**空**转写文件。
它不会生成任何文字——空转写会被 `validate_corpus()` 拒绝，所以语料在人工填好
之前无法参与评分（否则一页空真值会拿满分）。

## 6. 加语料时的自检命令

```bash
python - <<'PY'
from pathlib import Path
from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import load_manifest

for name in ("v", "ancient"):
    root = Path("benchmark/corpus") / name
    manifest = load_manifest(root / "manifest.json")
    problems = validate_corpus(manifest, root)
    print(name, "OK" if not problems else problems)
PY
```

`validate_corpus()` 会拒绝：绝对路径、越界路径、缺失或空文件、空转写、
以及没有任何 case 的 manifest。
