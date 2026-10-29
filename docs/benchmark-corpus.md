# 基准语料规范（含竖排 / 繁体语料要求）

- 更新日期：2026-10-29
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

## 4. 现有语料的已知不足

1. **6 页全是现代简体横排**（《V.》叶华年译本，译林 2008），无法验证竖排、
   繁体、异体字。竖排阈值 `detect_page_direction()` 目前只在合成夹具上验证过。
2. **参照文本单次转写**，未经二次校订；作为回归基线可用，作为金标准不够。
3. **图像已入库但源 PDF 受版权保护**。新增语料必须改用公有领域材料。

## 5. 加语料时的自检命令

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
