# EPUBCheck 官方符合性验证（#84）

## 现状

沙箱/录制环境**未安装 Java**，EPUBCheck 是 Java 工具（`epubcheck.jar`），无法在当前环境运行。**不能声称 EPUBCheck 已通过**，此项为预留风险。

## 项目内部替代验证：`check_epub`

`src/scribeforge/adapters/export_check.py` 的 `check_epub()` 在每次 `build_epub()` 构建时自动运行，覆盖了与 EPUBCheck 大部分重合的检查点：

| EPUBCheck 检查点 | 内部等价检查 | 状态 |
|:---|:---|:---|
| mimetype 第一个且未压缩 | `check_epub` 验证 | ✅ |
| META-INF/container.xml 指向 package 文档 | `check_epub` 验证 | ✅ |
| 所有 XML/XHTML 格式正确 | `check_epub` 解析每个条目 | ✅ |
| OPF version="3.0" | `check_epub` 验证 (`package.get("version")`) | ✅ |
| dc:identifier、dc:title、dc:language 必填 | `check_epub` 验证 | ✅ |
| manifest 每个项指向存在的文件 | `check_epub` 验证 href 解析 | ✅ |
| spine 顺序与验证页面顺序一致 | `check_epub` 对比 | ✅ |
| 图片 media-type 与文件扩展名一致 | `check_epub` `_check_images` 新增 | ✅ |
| 图片字节与验证栅格一致（SHA-256） | `check_epub` `_check_images` | ✅ |
| 覆盖图声明正确（EPUB 2 meta + EPUB 3 property） | `check_epub` 验证 cover | ✅ |
| nav 文档有 `<nav epub:type="toc">` | `check_epub` `_check_nav` | ✅ |
| 每个 spine 文档可从导航到达 | `check_epub` 验证 nav_hrefs | ✅ |
| 每个图片恰好被引用一次 | `check_epub` `_require_images_referenced_once` | ✅ |
| XHTML 文档：DOCTYPE / html 根元素 / head / body | `check_epub` `_require_xhtml_structure` 新增 | ✅ |
| 无文本丢失（逐页对比） | `check_epub` `_require_no_text_lost` | ✅ |
| 流文档保留所有页面（无丢失/重复/乱序） | `check_epub` `_require_flow_preserves_pages` | ✅ |

现有测试 `tests/test_epub_export.py`（20+ 项）和 `tests/test_export_check.py`（含新增的结构性检查测试）全部通过。

## 在有 Java 的环境中运行 EPUBCheck（补齐 #84）

### 前提

安装 Java 8+（EPUBCheck 需要 Java 运行时）。

### 下载 EPUBCheck

```bash
# 下载 EPUBCheck 5.3.2（或更高版本）
wget https://github.com/w3c/epubcheck/releases/download/v5.3.2/epubcheck-5.3.2.zip
unzip epubcheck-5.3.2.zip
```

### 运行验证

```bash
# 对构建出的 EPUB 文件运行 EPUBCheck
java -jar epubcheck-5.3.2/epubcheck.jar path/to/output.epub
```

### 预期输出

EPUBCheck 会输出：
- `No errors or warnings detected.` — 验证通过
- 错误/警告列表 — 需要修复后重新验证

### CI 集成

`.github/workflows/ci.yml` 中已添加条件化步骤：只有 runner 上安装了 Java 时才会下载并运行 EPUBCheck；无 Java 时跳过，内部 `check_epub()` 继续提供等价验证。

## 预留风险

1. **EPUBCheck 官方符合性验证未完成**：沙箱无 Java，无法运行 epubcheck.jar。建议在有 Java 的机器上运行上述命令补齐 #84。
2. **Readium 渲染验证未完成**：Readium 是阅读器库，需在有显示环境的机器上加载生成的 EPUB 进行人工或自动化渲染测试，沙箱无此条件。

---

**相关代码改动**：
- `src/scribeforge/adapters/export_check.py`：新增 OPF 必填元数据（dc:identifier/dc/language/version="3.0"）、XHTML 文档结构（DOCTYPE/html 根元素/head/body）、图片 media-type 与扩展名一致性检查。
- `tests/test_export_check.py`：新增 6 个结构性检查测试。
- `.github/workflows/ci.yml`：新增条件化 EPUBCheck CI 步骤（有 Java 时运行）。
