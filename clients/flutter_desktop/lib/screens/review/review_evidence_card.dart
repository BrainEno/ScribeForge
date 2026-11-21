import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/review_queue.dart';
import '../../models/review_triage.dart';
import '../../theme/scribe_theme.dart';
import '../../widgets/review_alignment_evidence.dart';
import '../../widgets/workspace_components.dart';
import 'review_image_inspector.dart';

class _UseReviewCandidateIntent extends Intent {
  const _UseReviewCandidateIntent(this.candidateNumber);

  final int candidateNumber;
}

class _RecordReviewDecisionIntent extends Intent {
  const _RecordReviewDecisionIntent();
}

class ReviewEvidenceCard extends StatelessWidget {
  const ReviewEvidenceCard({
    super.key,
    required this.item,
    required this.controller,
    required this.decisionFocusNode,
    required this.onRecord,
    required this.busy,
    required this.projectId,
  });

  final ReviewQueueItem item;
  final TextEditingController controller;
  final FocusNode decisionFocusNode;
  final Future<void> Function(ReviewQueueItem, String) onRecord;
  final bool busy;
  final int? projectId;

  bool get _canRecord => !busy && item.reviewToken != null;

  String _furnitureReasonLabel(String reason) => switch (reason) {
    'engine_block' => 'OCR 布局标记',
    'repeated_in_margin' => '重复页边行',
    'page_number' => '连续页码',
    _ => reason,
  };

  Widget _buildReviewEvidence(String label, String? text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
          ),
          const SizedBox(height: 2),
          SelectableText(text == null || text.isEmpty ? '（无文本）' : text),
        ],
      ),
    );
  }

  Widget _candidatePane(BuildContext context, String label, String? text) {
    final theme = Theme.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: ScribePalette.canvas,
        border: Border.all(color: ScribePalette.border),
        borderRadius: BorderRadius.circular(ScribeRadii.card),
      ),
      child: Padding(
        padding: const EdgeInsets.all(ScribeSpacing.x3),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(label, style: theme.textTheme.labelMedium),
            const SizedBox(height: ScribeSpacing.x2),
            SelectableText(
              text == null || text.isEmpty ? '（无文本）' : text,
              style: theme.textTheme.bodyLarge,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildReviewImage(String label, String imagePath) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
          ),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.file(
              File(imagePath),
              width: double.infinity,
              height: 150,
              fit: BoxFit.contain,
              errorBuilder: (context, error, stackTrace) => Container(
                height: 90,
                alignment: Alignment.center,
                color: Colors.black12,
                child: const Text('无法载入图像；文字证据仍可复核。'),
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Shortcuts(
      shortcuts: <ShortcutActivator, Intent>{
        const SingleActivator(LogicalKeyboardKey.digit1, alt: true):
            const _UseReviewCandidateIntent(1),
        const SingleActivator(LogicalKeyboardKey.digit2, alt: true):
            const _UseReviewCandidateIntent(2),
        const SingleActivator(LogicalKeyboardKey.digit3, alt: true):
            const _UseReviewCandidateIntent(3),
        const SingleActivator(LogicalKeyboardKey.enter, control: true):
            const _RecordReviewDecisionIntent(),
      },
      child: Actions(
        actions: <Type, Action<Intent>>{
          _UseReviewCandidateIntent: CallbackAction<_UseReviewCandidateIntent>(
            onInvoke: (intent) {
              final candidate = switch (intent.candidateNumber) {
                1 => item.primaryText,
                2 => item.secondaryText,
                3 => item.vlmText,
                _ => null,
              };
              if (candidate != null) {
                controller.text = candidate;
                controller.selection = TextSelection.collapsed(
                  offset: candidate.length,
                );
              }
              return null;
            },
          ),
          _RecordReviewDecisionIntent:
              CallbackAction<_RecordReviewDecisionIntent>(
                onInvoke: (_) {
                  if (_canRecord) {
                    unawaited(onRecord(item, controller.text));
                  }
                  return null;
                },
              ),
        },
        child: Card(
          margin: const EdgeInsets.only(bottom: 10),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: 8,
                  runSpacing: 4,
                  children: <Widget>[
                    Text(
                      '第 ${item.pageIndex + 1} 页 · 第 ${item.pairIndex + 1} 组',
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 14,
                      ),
                    ),
                    Chip(
                      label: Text('风险 ${(item.score * 100).round()}%'),
                      visualDensity: VisualDensity.compact,
                    ),
                    ...item.reasons.map(
                      (reason) => Chip(
                        label: Text(reviewReasonLabel(reason)),
                        visualDensity: VisualDensity.compact,
                      ),
                    ),
                    if (item.furnitureReason != null)
                      Chip(
                        avatar: const Icon(
                          Icons.auto_stories_outlined,
                          size: 16,
                        ),
                        label: Text(
                          '页边家具 · ${_furnitureReasonLabel(item.furnitureReason!)}',
                        ),
                        visualDensity: VisualDensity.compact,
                      ),
                  ],
                ),
                if (projectId != item.projectId)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(
                      '${item.projectTitle} · 项目 ${item.projectId}',
                      style: TextStyle(
                        color: Colors.grey.shade600,
                        fontSize: 11,
                      ),
                    ),
                  ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () => showReviewImageInspector(context, item),
                    icon: const Icon(Icons.zoom_in),
                    label: const Text('放大或比较图像证据'),
                  ),
                ),
                const Divider(height: 12),
                ReviewAlignmentEvidence(item: item),
                if (item.furnitureReason != null)
                  _buildReviewEvidence(
                    '页边家具分类依据',
                    _furnitureReasonLabel(item.furnitureReason!),
                  ),
                ScribeAdaptivePair(
                  splitAt: 620,
                  primaryShare: 0.5,
                  primary: _candidatePane(
                    context,
                    'OCR 引擎一候选',
                    item.primaryText,
                  ),
                  secondary: _candidatePane(
                    context,
                    'OCR 引擎二候选',
                    item.secondaryText,
                  ),
                ),
                const SizedBox(height: ScribeSpacing.x2),
                if (item.vlmText != null) ...[
                  _buildReviewEvidence('VLM 候选（仅供参考）', item.vlmText),
                  if (item.vlmUncertain)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        '视觉模型标记为不确定；请勿将其视为自动结论。',
                        style: TextStyle(
                          color: Colors.orange.shade800,
                          fontSize: 12,
                        ),
                      ),
                    ),
                ],
                const SizedBox(height: 4),
                if (item.cropPath != null)
                  _buildReviewImage('候选区域裁切', item.cropPath!),
                _buildReviewImage('原始页面图像', item.sourceImagePath),
                if (item.coordinateFrame == 'preprocessed' &&
                    item.ocrFrameImagePath != null)
                  _buildReviewImage('OCR 坐标帧（预处理图像）', item.ocrFrameImagePath!),
                if (item.coordinateFrame == 'unknown')
                  const Text('OCR 坐标帧未知或无法验证；请勿根据坐标推断原图位置。'),
                const SizedBox(height: 12),
                if (item.reviewToken == null)
                  const Text('此处理核心未提供核验版本；可查看证据，但请更新核心后再记录决策。'),
                TextField(
                  key: const ValueKey('review-decision-input'),
                  controller: controller,
                  focusNode: decisionFocusNode,
                  minLines: 1,
                  maxLines: 3,
                  decoration: const InputDecoration(
                    labelText: '人工确认的文字',
                    hintText: '比较上述证据后，输入你确认的文字',
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: <Widget>[
                    if (item.primaryText != null)
                      OutlinedButton.icon(
                        icon: const Icon(Icons.content_copy, size: 16),
                        label: const Text('填入 OCR 1'),
                        onPressed: busy
                            ? null
                            : () => controller.text = item.primaryText!,
                      ),
                    if (item.secondaryText != null)
                      OutlinedButton.icon(
                        icon: const Icon(Icons.content_copy, size: 16),
                        label: const Text('填入 OCR 2'),
                        onPressed: busy
                            ? null
                            : () => controller.text = item.secondaryText!,
                      ),
                    if (item.vlmText != null)
                      OutlinedButton.icon(
                        icon: const Icon(Icons.content_copy, size: 16),
                        label: const Text('填入 VLM 候选'),
                        onPressed: busy
                            ? null
                            : () => controller.text = item.vlmText!,
                      ),
                    ElevatedButton.icon(
                      icon: const Icon(Icons.fact_check_outlined),
                      label: const Text('记录人工决策'),
                      onPressed: _canRecord
                          ? () => onRecord(item, controller.text)
                          : null,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
