import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/superseded_review_drafts.dart';

class SupersededDraftsDialog extends StatefulWidget {
  const SupersededDraftsDialog({super.key, required this.archive});

  final SupersededReviewDrafts archive;

  @override
  State<SupersededDraftsDialog> createState() => _SupersededDraftsDialogState();
}

class _SupersededDraftsDialogState extends State<SupersededDraftsDialog> {
  String? _message;

  Future<void> _copy(SupersededReviewDraft draft) async {
    var message = '已复制旧稿。请对照当前证据重新核对。';
    try {
      await Clipboard.setData(ClipboardData(text: draft.text));
    } on PlatformException {
      message = '剪贴板不可用，可以手动选中文字。';
    }
    if (mounted) setState(() => _message = message);
  }

  @override
  Widget build(BuildContext context) {
    final drafts = widget.archive.newestFirst;
    return AlertDialog(
      scrollable: true,
      title: const Text('旧版本复核草稿'),
      content: SizedBox(
        width: 600,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Text('这些文字基于不同的核验证据，未写入决策。只能查看或复制，不会自动填入新证据。'),
            const Text('仅保存在当前会话，最多保留最近 30 条。切换项目或退出会清除。'),
            if (_message case final message?)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(message),
              ),
            if (drafts.isEmpty) const Text('没有保留的旧草稿。'),
            for (final draft in drafts)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(draft.location),
                      Text('原证据版本：${draft.token ?? '未提供'}'),
                      const Divider(),
                      SelectableText(draft.text),
                      Wrap(
                        spacing: 8,
                        children: <Widget>[
                          TextButton.icon(
                            onPressed: () => _copy(draft),
                            icon: const Icon(Icons.copy),
                            label: const Text('复制旧稿'),
                          ),
                          TextButton(
                            onPressed: () => setState(() {
                              widget.archive.remove(draft);
                              _message = '已移除此旧稿。';
                            }),
                            child: const Text('移除此旧稿'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('关闭'),
        ),
      ],
    );
  }
}
