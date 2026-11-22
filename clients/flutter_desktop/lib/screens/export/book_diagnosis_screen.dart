import 'package:flutter/material.dart';

import '../../services/book_diagnosis_service.dart';
import '../../services/desktop_file_picker.dart';

/// Compare counts and reported source markers; never interprets them as proof.
class BookDiagnosisScreen extends StatefulWidget {
  const BookDiagnosisScreen({
    super.key,
    required this.service,
    required this.filePicker,
    required this.storePath,
    required this.sourcePath,
  });

  final BookDiagnosisService service;
  final DesktopFilePicker filePicker;
  final String storePath;
  final String sourcePath;

  @override
  State<BookDiagnosisScreen> createState() => _BookDiagnosisScreenState();
}

class _BookDiagnosisScreenState extends State<BookDiagnosisScreen> {
  String? _epubPath;
  BookDiagnosis? _result;
  String? _error;
  bool _busy = false;

  Future<void> _chooseAndInspect() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    String? path;
    try {
      path = await widget.filePicker.pickFile(
        dialogTitle: '选择要检查的 EPUB',
        allowedExtensions: const <String>['epub'],
      );
    } on Object catch (error) {
      if (mounted) {
        setState(() {
          _error = '无法选择文件：$error';
          _busy = false;
        });
      }
      return;
    }
    if (!mounted) return;
    if (path == null) {
      setState(() => _busy = false);
      return;
    }
    setState(() {
      _epubPath = path;
      _result = null;
    });
    try {
      final result = await widget.service.diagnose(
        storePath: widget.storePath,
        sourcePath: widget.sourcePath,
        epubPath: path,
      );
      if (mounted) setState(() => _result = result);
    } on Object catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    return Scaffold(
      appBar: AppBar(title: const Text('检查 EPUB 与原书页数')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: <Widget>[
          const Text('检查使用当前项目及其原始文件，不运行 OCR，也不修改项目。'),
          const SizedBox(height: 12),
          SelectableText('原文件：${widget.sourcePath}'),
          if (_epubPath != null) SelectableText('EPUB：$_epubPath'),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              key: const ValueKey('choose-diagnosis-epub'),
              onPressed: _busy ? null : _chooseAndInspect,
              icon: const Icon(Icons.folder_open),
              label: const Text('选择 EPUB 并检查'),
            ),
          ),
          if (_busy) ...[
            const SizedBox(height: 12),
            const LinearProgressIndicator(),
            const Text('正在校验原文件并读取项目和 EPUB…'),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            SelectableText(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ],
          if (result != null) ...[
            const SizedBox(height: 20),
            Text('原书物理页：${result.sourcePages}'),
            Text(
              '项目已导入：${result.importedPages} 页 · 未导入：${result.missingImported} 页',
            ),
            Text('导入状态：${result.importState ?? '未知'}'),
            Text(
              '双引擎 OCR 完成：${result.ocrPages} 页 · 有验证记录：${result.verifiedPages} 页',
            ),
            Text('失败任务：${result.failedJobs}'),
            Text(
              'EPUB 阅读顺序文档：${result.spineDocuments} · 识别的原书页标：${result.pageMarkers}',
            ),
            Text('未识别到页标：${result.missingMarkers} 页'),
            if (result.missingImportedPages.isNotEmpty)
              SelectableText(
                '未导入的物理页（最多 100 页）：${result.missingImportedPages.join('、')}',
              ),
            if (result.missingMarkerPages.isNotEmpty)
              SelectableText(
                '未识别页标的物理页（最多 100 页）：${result.missingMarkerPages.join('、')}',
              ),
            if (result.recordedCountMatches == false)
              const Text('项目记录的原书总页数与本次源文件不一致；请保留证据并恢复备份。'),
            if (result.hasUnrecognizedDocuments)
              const Text('部分 EPUB 文档的页标格式未识别，不能把它当作缺页结论。'),
            const SizedBox(height: 16),
            const Text(
              '阅读器屏幕页数会随字号和窗口变化，不等于 PDF 物理页数。页标数量不能证明文字识别完整或准确；此检查也不能证明 EPUB 属于当前项目。',
            ),
          ],
        ],
      ),
    );
  }
}
