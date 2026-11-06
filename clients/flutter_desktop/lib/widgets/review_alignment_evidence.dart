import 'package:flutter/material.dart';

import '../models/review_queue.dart';

class ReviewAlignmentEvidence extends StatelessWidget {
  const ReviewAlignmentEvidence({required this.item, super.key});

  final ReviewQueueItem item;

  String get _statusLabel => switch (item.alignmentStatus) {
    'matched' => '已匹配',
    'unmatched' => '未匹配',
    'ambiguous' => '结构歧义',
    _ => '未知',
  };

  String _indexes(List<int> indexes) =>
      indexes.isEmpty ? '—' : '[${indexes.join(', ')}]';

  String get _groupLabel {
    final primaryCount = item.primaryLineIndexes.length;
    final secondaryCount = item.secondaryLineIndexes.length;
    if (primaryCount == 1 && secondaryCount == 1) {
      return '单行配对：1 → 1';
    }
    if (primaryCount > 0 && secondaryCount > 0) {
      return '多行组合：$primaryCount → $secondaryCount';
    }
    return '行组合：$primaryCount → $secondaryCount';
  }

  @override
  Widget build(BuildContext context) {
    final ambiguity = item.ambiguity;
    final hasIndexes =
        item.primaryLineIndexes.isNotEmpty ||
        item.secondaryLineIndexes.isNotEmpty;
    return Container(
      key: const ValueKey('review-alignment-evidence'),
      width: double.infinity,
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            '对齐状态：$_statusLabel',
            style: TextStyle(
              fontWeight: FontWeight.w600,
              color: ambiguity == null ? null : Colors.orange.shade900,
            ),
          ),
          const SizedBox(height: 3),
          if (hasIndexes) ...<Widget>[
            Text(
              '原始行索引（0-based）：OCR 1 ${_indexes(item.primaryLineIndexes)} '
              '→ OCR 2 ${_indexes(item.secondaryLineIndexes)}',
            ),
            Text(_groupLabel),
          ] else
            const Text('原始行索引未提供'),
          if (ambiguity != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(
              '结构映射不确定：请人工选择候选映射；VLM 或机器共识不能绕过此复核。',
              style: TextStyle(
                color: Colors.orange.shade900,
                fontWeight: FontWeight.w600,
              ),
            ),
            Text(
              '歧义原因：${ambiguity.reason} · 候选分差 '
              '${ambiguity.scoreMargin.toStringAsFixed(3)}',
            ),
            for (var index = 0; index < ambiguity.alternatives.length; index++)
              Builder(
                builder: (context) {
                  final alternative = ambiguity.alternatives[index];
                  return Text(
                    '候选 ${index + 1}：OCR 1 '
                    '${_indexes(alternative.primaryLineIndexes)} → OCR 2 '
                    '${_indexes(alternative.secondaryLineIndexes)} · 分数 '
                    '${alternative.score.toStringAsFixed(3)} · 文本相似度 '
                    '${(alternative.textSimilarity * 100).toStringAsFixed(1)}%',
                  );
                },
              ),
          ],
        ],
      ),
    );
  }
}
