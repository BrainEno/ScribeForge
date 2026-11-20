import 'review_queue.dart';

/// Stable within a store. Callers must keep identities scoped to the store.
typedef ReviewItemKey = (int project, int page, int pair);

ReviewItemKey reviewItemKey(ReviewQueueItem item) =>
    (item.projectId, item.pageIndex, item.pairIndex);

enum ReviewOrder { queue, highestRisk, physicalPage }

enum ReviewEvidenceFilter { any, crop, ambiguous, missingCandidate }

class ReviewPageRange {
  const ReviewPageRange(this.first, this.last);

  final int? first;
  final int? last;

  static ReviewPageRange? parse(String source) {
    final text = source.trim();
    if (text.isEmpty) return null;
    final pieces = text.split(RegExp(r'[-–—]'));
    if (pieces.length > 2) throw const FormatException('请输入页码或范围，例如 12–30。');
    int? number(String value) {
      final trimmed = value.trim();
      if (trimmed.isEmpty) return null;
      if (!RegExp(r'^\d+$').hasMatch(trimmed)) {
        throw const FormatException('页码必须是大于零的整数。');
      }
      final page = int.tryParse(trimmed);
      if (page == null || page < 1) {
        throw const FormatException('页码必须是大于零的整数。');
      }
      return page;
    }

    final start = number(pieces.first);
    final end = pieces.length == 1 ? start : number(pieces.last);
    if (start == null && end == null) {
      throw const FormatException('请至少填写范围的一端。');
    }
    if (start != null && end != null && start > end) {
      throw const FormatException('起始页不能大于结束页。');
    }
    return ReviewPageRange(start, end);
  }

  bool contains(int page) =>
      (first == null || page >= first!) && (last == null || page <= last!);
}

/// A view over pending evidence, never a change to the queue or its decisions.
class ReviewTriageQuery {
  const ReviewTriageQuery({
    this.text = '',
    this.pages,
    this.reason,
    this.minimumRisk = 0,
    this.evidence = ReviewEvidenceFilter.any,
    this.markedOnly = false,
    this.order = ReviewOrder.queue,
  });

  final String text;
  final ReviewPageRange? pages;
  final String? reason;
  final double minimumRisk;
  final ReviewEvidenceFilter evidence;
  final bool markedOnly;
  final ReviewOrder order;

  bool get filtersItems =>
      text.trim().isNotEmpty ||
      pages != null ||
      reason != null ||
      minimumRisk > 0 ||
      evidence != ReviewEvidenceFilter.any ||
      markedOnly;

  bool get isDefault => !filtersItems && order == ReviewOrder.queue;

  ReviewTriageResult apply(
    List<ReviewQueueItem> queue,
    Set<ReviewItemKey> marked,
  ) {
    final query = text.trim().toLowerCase();
    final indexes = <int>[];
    final reasons = <String, int>{};
    final physicalPages = <int>{};
    var markCount = 0;
    for (var index = 0; index < queue.length; index++) {
      final item = queue[index];
      final page = item.pageIndex + 1;
      final isMarked = marked.contains(reviewItemKey(item));
      if (isMarked) markCount++;
      for (final code in item.reasons.toSet()) {
        reasons.update(code, (count) => count + 1, ifAbsent: () => 1);
      }
      if ((pages != null && !pages!.contains(page)) ||
          (reason != null && !item.reasons.contains(reason)) ||
          item.score < minimumRisk ||
          (markedOnly && !isMarked) ||
          !_hasEvidence(item)) {
        continue;
      }
      if (query.isNotEmpty &&
          !<String?>[item.primaryText, item.secondaryText, item.vlmText].any(
            (candidate) => candidate?.toLowerCase().contains(query) ?? false,
          )) {
        continue;
      }
      indexes.add(index);
      physicalPages.add(page);
    }
    if (order != ReviewOrder.queue) {
      indexes.sort((a, b) {
        final left = queue[a];
        final right = queue[b];
        if (order == ReviewOrder.highestRisk) {
          final byRisk = right.score.compareTo(left.score);
          if (byRisk != 0) return byRisk;
        }
        final byPage = left.pageIndex.compareTo(right.pageIndex);
        if (byPage != 0) return byPage;
        final byPair = left.pairIndex.compareTo(right.pairIndex);
        return byPair == 0 ? a.compareTo(b) : byPair;
      });
    }
    return ReviewTriageResult(
      queue: queue,
      indexes: indexes,
      reasonCounts: reasons,
      matchingPages: physicalPages.length,
      markedCount: markCount,
    );
  }

  bool _hasEvidence(ReviewQueueItem item) => switch (evidence) {
    ReviewEvidenceFilter.any => true,
    ReviewEvidenceFilter.crop => item.cropPath?.isNotEmpty == true,
    ReviewEvidenceFilter.ambiguous =>
      item.ambiguity != null || item.reasons.contains('alignment_ambiguity'),
    ReviewEvidenceFilter.missingCandidate =>
      item.primaryText?.trim().isNotEmpty != true ||
          item.secondaryText?.trim().isNotEmpty != true,
  };
}

class ReviewTriageResult {
  ReviewTriageResult({
    required List<ReviewQueueItem> queue,
    required List<int> indexes,
    required Map<String, int> reasonCounts,
    required this.matchingPages,
    required this.markedCount,
  }) : _queue = queue,
       indexes = List<int>.unmodifiable(indexes),
       reasonCounts = Map<String, int>.unmodifiable(reasonCounts);

  final List<ReviewQueueItem> _queue;
  final List<int> indexes;
  final Map<String, int> reasonCounts;
  final int matchingPages;
  final int markedCount;

  int? visibleIndex(int selectedSourceIndex) =>
      indexes.contains(selectedSourceIndex)
      ? selectedSourceIndex
      : indexes.firstOrNull;

  int? move(int selectedSourceIndex, int delta) {
    final selected = visibleIndex(selectedSourceIndex);
    if (selected == null) return null;
    final position = indexes.indexOf(selected);
    return indexes[(position + delta).clamp(0, indexes.length - 1).toInt()];
  }

  int? firstOnPage(int page) {
    for (final sourceIndex in indexes) {
      if (_queue[sourceIndex].pageIndex + 1 == page) return sourceIndex;
    }
    return null;
  }
}

String reviewReasonLabel(String reason) => switch (reason) {
  'text_disagreement' => '文字不一致',
  'variant_disagreement' => '异体字差异',
  'punctuation_disagreement' => '标点差异',
  'missing_or_extra_line' => '缺行或多行',
  'low_confidence' => '置信度偏低',
  'unicode_anomaly' => '字符异常',
  'spatial_mismatch' => '位置不一致',
  'alignment_ambiguity' => '结构映射歧义',
  _ => reason,
};
