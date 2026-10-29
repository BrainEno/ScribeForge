# Benchmark：古籍 / 旧版书能力评估的支撑材料

本目录存放**可复现**的测量工具与结果，配合
[`docs/ancient-book-support.md`](../../docs/ancient-book-support.md) 阅读。

## 内容

| 文件 | 作用 |
| --- | --- |
| `pdf_characteristics.py` | 统计 PDF 页面尺寸、亮度、暗像素比、单页 PNG 体积，用于估算全书资产体积与扫描质量 |
| `scale_harness.py` | 两段独立测量：①真实 PDF 光栅化导入 ②真实编排/组装/导出链路在合成引擎下的规模表现 |
| `measurements.json` | 2026-10-28 在 `benchmark/V..pdf` 上的实测结果 |

## 运行

```bash
# 页面特征统计（前 50 页 @300DPI）
python benchmark/analysis/pdf_characteristics.py benchmark/V..pdf

# 规模测量
python benchmark/analysis/scale_harness.py \
  --pdf benchmark/V..pdf --import-pages 40 --pipeline-pages 562 --json
```

## 重要声明

`scale_harness.py` 中的 `SyntheticEngine` **不是 OCR 引擎**。
它输出的文本是形如 `P0003L07` 的合成标记，只用于测量编排、
SQLite 持久化、决策层、组装与导出在书籍规模下的时间与内存。
它不产生任何可采信的转写文本，也**不参与准确率评分**。

真实准确率必须由 `scribeforge benchmark` 用真实引擎产出：

```bash
scribeforge benchmark --manifest benchmark/corpus/v/manifest.json \
  --out benchmark/benchmark_report.md --include-pipeline
```

在该命令产出结果之前，`benchmark_report.md` 的数字必须保持 pending。
不要把本目录的时长/体积数据误读为识别质量数据。
