# EPUBCheck 官方符合性验证（#125）

## CI 门禁

`.github/workflows/ci.yml` 的 `core` job 安装 Temurin Java 17，下载固定的
[EPUBCheck 5.4.0 官方发布包](https://github.com/w3c/epubcheck/releases/tag/v5.4.0)，
核对发布资产 SHA-256 后运行 `.github/scripts/epubcheck_gate.py`。下载、摘要校验、
解压、Java 启动或检查失败均使 job 失败，不会跳过或吞掉错误。

门禁通过项目 `build_epub()` 生成固定时间、固定内容的样本，先要求 EPUBCheck
接受正常样本，再将同一个样本的 `mimetype` 条目移除，要求 EPUBCheck 返回非零并
报告官方 `ERROR(...)` 或 `FATAL(...)` 消息。第二次失败是**预期的负向对照**；
若无效样本通过、或失败只是 Java/文件错误而非 EPUBCheck 诊断，门禁本身失败。
日志记录生成、两次检查的输出和退出码。仅能据此声称**该样本**通过指定版本的
官方检查，不能外推所有导出文件或所有阅读器的兼容性。

## 手动执行

安装项目和 Java 17，将固定版本发布包下载解压后：

```sh
python .github/scripts/epubcheck_gate.py epubcheck-5.4.0/epubcheck.jar
# 检查自己的文件：
java -jar epubcheck-5.4.0/epubcheck.jar path/to/output.epub
```

`tests/test_epubcheck_gate.py` 使用本地假校验器测试门禁的成功、失败、负向对照与
下载配置，不代替官方 Java 校验器。受执行环境网络限制，本地无法从 GitHub release
资产主机获取官方 5.4.0 包；曾用 npm 第三方包中版本自报 5.3.0 的 Java 工具执行
正反样本，两者按预期返回 0 和 1。5.4.0 的实际执行状态以对应 CI 日志为准。

## 内部检查与未覆盖范围

`check_epub()` 是导出时运行的内部结构及证据一致性检查，覆盖包结构、导航、图片、
文本和页面顺序等项目约束；它不是 EPUBCheck 的实现，也不能证明官方规范符合性。
这两道检查互补。Readium/其他阅读器的渲染、交互和辅助技术验收仍未由本门禁完成，
也不能据此声称 #84 的结构保真验收完成。
