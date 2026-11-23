import 'package:flutter/material.dart';

import '../../models/physical_page_selection.dart';

class ScopedRetryDialog extends StatefulWidget {
  const ScopedRetryDialog({
    super.key,
    required this.initialPages,
    required this.sourcePages,
    required this.remoteVlmEnabled,
  });
  final List<int> initialPages;
  final int? sourcePages;
  final bool remoteVlmEnabled;
  @override
  State<ScopedRetryDialog> createState() => _ScopedRetryDialogState();
}

class _ScopedRetryDialogState extends State<ScopedRetryDialog> {
  late final TextEditingController pages;
  String? error;
  @override
  void initState() {
    super.initState();
    pages = TextEditingController(
      text: widget.initialPages.map((p) => '${p + 1}').join(', '),
    );
  }

  @override
  void dispose() {
    pages.dispose();
    super.dispose();
  }

  void _confirm() {
    try {
      final selection = parsePhysicalPages(
        pages.text,
        sourcePages: widget.sourcePages,
      );
      Navigator.of(context).pop(selection);
    } on FormatException catch (exc) {
      setState(() => error = exc.message);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('选择物理页续跑'),
    content: SizedBox(
      width: 560,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '输入 PDF 从第一页开始计算的物理页码，不是书上印刷页码或 EPUB 阅读器页码。'
              '例如物理第 18、374 页输入 18, 374；请先确认对应关系。',
            ),
            const SizedBox(height: 12),
            TextField(
              controller: pages,
              autofocus: true,
              key: const ValueKey('scoped-retry-pages'),
              decoration: InputDecoration(
                labelText: '物理页码（逗号或空格分隔）',
                errorText: error,
                helperText: widget.sourcePages == null
                    ? '原书总页数未知；处理核心仍会检查已导入页。'
                    : '已记录原书 ${widget.sourcePages} 页',
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '仅继续所选页未完成的阶段，已完成阶段与人工决定不会主动重跑；可能产生新的 OCR 或复核证据。'
              '不会重建整书结构或运行整书修复。操作前请备份项目及现有日志，并使用原文件和相同预处理设置。',
            ),
            if (widget.remoteVlmEnabled) ...[
              const SizedBox(height: 8),
              const Text('当前已启用 VLM；处理时可能按配置发送复核裁切图。'),
            ],
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(
        key: const ValueKey('confirm-scoped-retry'),
        onPressed: _confirm,
        child: const Text('确认并续跑'),
      ),
    ],
  );
}
