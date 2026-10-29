# 图片压缩体积对比 + EPUBCheck 验收评估

基于 `benchmark/V..pdf` 的真实页面（300 DPI，pdfium 渲染）做图片压缩体积对比，并对 EPUBCheck / Readium 验收（#84）作诚实评估。

---

## 1. 图片压缩体积对比

### 测试方法

从 `benchmark/V..pdf`（562 页，Thomas Pynchon《V.》中译本，Yilin Press 2008，2481 × 3728 px @ 300 DPI）提取 5 个有代表性的真实页面：

| 页 | 章节 | 布局特征 |
|----|------|----------|
| 0 | 封面/首页 | 图片密集，1.82 MB 原始 PNG |
| 4 | 出版说明 | 文本密集，无缩进 |
| 13 | 第一章 | 稀疏布局，居中标题、诗行 |
| 19 | 正文 | 对话段落、引号 |
| 29 | 正文 | 省略号、脚记 |

每个页面用 `PdfPageSource`（pypdfium2，300 DPI）渲染为原始 PNG，再用 `reencode_image()`（`src/scribeforge/domain/page_asset.py`）测试不同压缩参数的体积。

### 体积对比表（字节）

| 页 | 原始 PNG | PNG + optimize | 节省 | JPEG q60 | JPEG q80 | JPEG q95 | 节省 (q80) |
|:---|:---|:---|:---|:---|:---|:---|:---|
| 0 | 1,818,151 | 1,695,487 | 122,664 (6.7%) | 188,184 | 335,029 | 922,334 | **1,483,122 (81.6%)** |
| 4 | 777,612 | 761,089 | 16,523 (2.1%) | 384,500 | 496,605 | 752,437 | **281,007 (36.1%)** |
| 13 | 1,280,820 | 1,258,200 | 22,620 (1.8%) | 601,112 | 786,535 | 1,213,482 | **494,285 (38.6%)** |
| 19 | 910,144 | 894,974 | 15,170 (1.7%) | 444,998 | 576,629 | 874,135 | **333,515 (36.6%)** |
| 29 | 786,742 | 777,435 | 9,307 (1.2%) | 400,929 | 516,667 | 778,359 | **270,075 (34.3%)** |

### 结论

1. **PNG optimize 本身收益极小**：文本页仅节省 1.2%–2.1%，封面（图片密集页）节省 6.7%。`PdfPageSource.render_page()` 输出的 PNG 已经足够紧凑，重编码 PNG 不是体积优化的有效手段。

2. **JPEG q80 是最佳平衡点**：文本页节省 34.3%–38.6%，封面节省 81.6%（1.82 MB → 335 KB）。q95 对文本页几乎无收益（778 KB vs 786 KB，仅 1%），q60 虽然更小但文本可读性可能受损。

3. **封面是最大体积源**：第 0 页原始 1.82 MB，JPEG q80 后仅 335 KB，是单页压缩收益最大的页面。

### 建议

- 封面/插图页：建议 JPEG q80（体积降幅 80% 以上，可接受质量损失）。
- 纯文本页：保持原始 PNG（PNG optimize 收益 < 2%，JPEG 有损压缩可能引入文字模糊）。
- `reencode_image()` 默认关闭（CLI `--reencode-format`/`--reencode-quality` 显式启用），保持原始字节作为审计基准。
- 若启用压缩，审计摘要基于重编码后字节，与原始 render 不可直接比对（已在 `importing.py` 文档中说明）。

---

## 2. EPUBCheck / Readium 验收（#84）

### 无法运行 EPUBCheck（诚实说明）

沙箱环境**未安装 Java**（`java: command not found`），EPUBCheck 是 Java 工具（epubcheck.jar），无法在当前环境运行。因此**不能声称 EPUBCheck 通过**，此项为预留风险。

### 项目内部已有替代验证：`check_epub`

`src/scribeforge/adapters/export_check.py` 的 `check_epub()` 在每次 `build_epub()` 构建时自动运行，覆盖了与 EPUBCheck 大部分重合的检查点：

| 检查项 | 状态 | 说明 |
|:---|:---|:---|
| mimetype 为第一个未压缩条目 | ✅ | `check_epub` 验证 |
| META-INF/container.xml 指向 package 文档 | ✅ | `check_epub` 验证 |
| 所有 XML/XHTML 格式正确 | ✅ | `check_epub` 解析每个条目 |
| manifest 每个项指向存在的文件 | ✅ | `check_epub` 验证 href 解析 |
| spine 顺序与验证页面顺序一致 | ✅ | `check_epub` 对比 |
| package 标题与书籍一致 | ✅ | `check_epub` 验证 dc:title |
| 图片字节与验证栅格一致（SHA-256） | ✅ | `check_epub` `_check_images` |
| 覆盖图声明正确（EPUB 2 meta + EPUB 3 property） | ✅ | `check_epub` 验证 cover |
| nav 文档有 `<nav epub:type="toc">` | ✅ | `check_epub` `_check_nav` |
| 每个 spine 文档可从导航到达 | ✅ | `check_epub` 验证 nav_hrefs |
| 每个图片恰好被引用一次 | ✅ | `check_epub` `_require_images_referenced_once` |
| 无文本丢失（逐页对比） | ✅ | `check_epub` `_require_no_text_lost` |
| 流文档保留所有页面（无丢失/重复/乱序） | ✅ | `check_epub` `_require_flow_preserves_pages` |

现有测试 `tests/test_epub_export.py` 已覆盖 mimetype、container、metadata/spine、nav、pagebreak、文本保持、确定性构建等 20+ 项，全部通过。

### 预留风险

- **EPUBCheck 官方符合性验证未完成**：无 Java 环境，无法运行 epubcheck.jar。建议在有 Java 的机器上运行 `java -jar epubcheck.jar <output.epub>` 补齐 #84 验收。
- **Readium 渲染验证未完成**：Readium 是阅读器库，需在有显示环境的机器上加载生成的 EPUB 进行人工或自动化渲染测试，沙箱无此条件。

---

## 3. 本轮已提交的代码改动（#89 已合并）

- PR #103 `#89 页眉页脚检测：竖排页按 x 轴判定页边距`，已合并到 main `773fe0c`。
- `domain/furniture.py` `_in_margin` 感知页面方向：竖排按左右边距（x 轴），横排按上下边距（y 轴）。
- `tests/test_furniture.py` 新增 2 个竖排边距测试，19/19 全绿。

---

**下一步建议**：在有 Java 的环境中运行 EPUBCheck 补齐 #84；或继续按清单处理其他事项。
