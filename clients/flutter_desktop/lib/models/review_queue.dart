import 'dart:convert';

class ReviewQueueSnapshot {
  const ReviewQueueSnapshot({required this.items});

  final List<ReviewQueueItem> items;

  static ReviewQueueSnapshot fromJsonText(String raw) {
    final decoded = jsonDecode(raw);
    if (decoded is! Map || decoded['schema_version'] != 1) {
      throw const FormatException('Unsupported ScribeForge review JSON schema.');
    }
    final projects = decoded['projects'];
    if (projects is! List) {
      throw const FormatException('Review JSON is missing the projects collection.');
    }

    final items = <ReviewQueueItem>[];
    for (final rawProject in projects) {
      if (rawProject is! Map) {
        throw const FormatException('Review JSON contains an invalid project record.');
      }
      final project = Map<String, dynamic>.from(rawProject);
      final projectId = project['id'];
      final projectTitle = project['title'];
      final rawItems = project['items'];
      if (projectId is! int || projectTitle is! String || rawItems is! List) {
        throw const FormatException('Review JSON has invalid project fields.');
      }
      for (final rawItem in rawItems) {
        if (rawItem is! Map) {
          throw const FormatException('Review JSON contains an invalid queue item.');
        }
        items.add(
          ReviewQueueItem.fromJson(
            projectId: projectId,
            projectTitle: projectTitle,
            json: Map<String, dynamic>.from(rawItem),
          ),
        );
      }
    }
    return ReviewQueueSnapshot(items: List<ReviewQueueItem>.unmodifiable(items));
  }
}

class ReviewQueueItem {
  const ReviewQueueItem({
    required this.projectId,
    required this.projectTitle,
    required this.pageIndex,
    required this.pairIndex,
    required this.score,
    required this.reasons,
    required this.furnitureReason,
    required this.primaryText,
    required this.secondaryText,
    required this.vlmText,
    required this.vlmUncertain,
    required this.cropPath,
    required this.sourceImagePath,
  });

  final int projectId;
  final String projectTitle;
  final int pageIndex;
  final int pairIndex;
  final double score;
  final List<String> reasons;
  final String? furnitureReason;
  final String? primaryText;
  final String? secondaryText;
  final String? vlmText;
  final bool vlmUncertain;
  final String? cropPath;
  final String sourceImagePath;

  String get description =>
      '风险 ${score.toStringAsFixed(2)}${reasons.isEmpty ? '' : ' · ${reasons.join('、')}'}';

  static ReviewQueueItem fromJson({
    required int projectId,
    required String projectTitle,
    required Map<String, dynamic> json,
  }) {
    final pageIndex = json['page_index'];
    final pairIndex = json['pair_index'];
    final rawScore = json['score'];
    final rawReasons = json['reasons'];
    final rawFurnitureReason = json['furniture_reason'];
    final rawPrimaryText = json['primary_text'];
    final rawSecondaryText = json['secondary_text'];
    final rawVlmText = json['vlm_text'];
    final vlmUncertain = json['vlm_uncertain'];
    final rawCropPath = json['crop_path'];
    final sourceImagePath = json['source_image_path'];
    if (pageIndex is! int ||
        pairIndex is! int ||
        rawScore is! num ||
        !rawScore.isFinite ||
        rawReasons is! List ||
        rawReasons.any((reason) => reason is! String) ||
        (rawFurnitureReason != null && rawFurnitureReason is! String) ||
        (rawPrimaryText != null && rawPrimaryText is! String) ||
        (rawSecondaryText != null && rawSecondaryText is! String) ||
        (rawVlmText != null && rawVlmText is! String) ||
        vlmUncertain is! bool ||
        (rawCropPath != null && rawCropPath is! String) ||
        sourceImagePath is! String) {
      throw const FormatException('Review JSON has invalid evidence fields.');
    }
    return ReviewQueueItem(
      projectId: projectId,
      projectTitle: projectTitle,
      pageIndex: pageIndex,
      pairIndex: pairIndex,
      score: rawScore.toDouble(),
      reasons: List<String>.unmodifiable(rawReasons.cast<String>()),
      furnitureReason: rawFurnitureReason as String?,
      primaryText: rawPrimaryText as String?,
      secondaryText: rawSecondaryText as String?,
      vlmText: rawVlmText as String?,
      vlmUncertain: vlmUncertain,
      cropPath: rawCropPath as String?,
      sourceImagePath: sourceImagePath,
    );
  }
}
