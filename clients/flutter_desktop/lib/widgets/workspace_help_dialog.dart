import 'package:flutter/material.dart';

/// 全局「使用帮助」对话框：集中展示标准工作流、复核快捷键与重要规则，
/// 让新手在任何页面都能一键找到操作指引（#189）。
class WorkspaceHelpDialog extends StatelessWidget {
  const WorkspaceHelpDialog({super.key});

  static const TextStyle _heading = TextStyle(
    fontWeight: FontWeight.w600,
    fontSize: 13,
  );

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('使用帮助'),
      content: const SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text('标准流程', style: _heading),
            SizedBox(height: 4),
            Text('导入书籍 → 开始处理 → 复核决策 →（可选候选比较）→ 导出 EPUB/DOCX。'),
            SizedBox(height: 10),
            Text('复核快捷键', style: _heading),
            SizedBox(height: 4),
            Text(
              'Alt+←/→ 上一项/下一项；Ctrl+J 聚焦决策输入；Ctrl+G 聚焦跳页；'
              'Alt+1/2/3 填入候选；Ctrl+Enter 记录人工决策。',
            ),
            SizedBox(height: 10),
            Text('重要规则', style: _heading),
            SizedBox(height: 4),
            Text(
              '填入候选只是填空，不会记录决策；只有「记录人工决策」（Ctrl+Enter）'
              '才会写入证据链。处理可随时取消，进度与人工决策都会保留，可继续或重试。',
            ),
            SizedBox(height: 10),
            Text('首次使用', style: _heading),
            SizedBox(height: 4),
            Text(
              '在「设置」里一键初始化 OCR 环境；如需视觉模型（VLM）复核，'
              '也在「设置」里配置接口地址与模型。',
            ),
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('知道了'),
        ),
      ],
    );
  }
}

/// 打开「使用帮助」对话框。
Future<void> showWorkspaceHelpDialog(BuildContext context) {
  return showDialog<void>(
    context: context,
    builder: (context) => const WorkspaceHelpDialog(),
  );
}
