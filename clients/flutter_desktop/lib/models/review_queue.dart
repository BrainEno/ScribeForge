import 'dart:convert';

List<int> _lineIndexes(Object? raw, String fieldName) {
  if (raw == null) {
    return const <int>[];
  }
  if (raw is! List) {
    throw FormatException('Review JSON $fieldName must be a list.');
  }
  final indexes = <int>[];
  for (final value in raw) {
    if (value is! int || value < 0 || indexes.contains(value)) {
      throw FormatException(
        'Review JSON $fieldName has an invalid line index.',
      );
    }
    indexes.add(value);
  }
  return List<int>.unmodifiable(indexes);
}

double _finiteNumber(Object? raw, String fieldName) {
  if (raw is! num || !raw.isFinite) {
    throw FormatException('Review JSON $fieldName must be a finite number.');
  }
  return raw.toDouble();
}

class ReviewAlignmentAlternative {
  const ReviewAlignmentAlternative({
    required this.primaryLineIndexes,
    required this.secondaryLineIndexes,
    required this.score,
    required this.textSimilarity,
    required this.crossAxisDistance,
  });

  final List<int> primaryLineIndexes;
  final List<int> secondaryLineIndexes;
  final double score;
  final double textSimilarity;
  final double crossAxisDistance;

  static ReviewAlignmentAlternative fromJson(Map<String, dynamic> json) {
    final primary = _lineIndexes(
      json['primary_line_indexes'],
      'alternative primary_line_indexes',
    );
    final secondary = _lineIndexes(
      json['secondary_line_indexes'],
      'alternative secondary_line_indexes',
    );
    if (primary.isEmpty || secondary.isEmpty) {
      throw const FormatException(
        'A review alignment alternative needs lines from both engines.',
      );
    }
    return ReviewAlignmentAlternative(
      primaryLineIndexes: primary,
      secondaryLineIndexes: secondary,
      score: _finiteNumber(json['score'], 'alternative score'),
      textSimilarity: _finiteNumber(
        json['text_similarity'],
        'alternative text_similarity',
      ),
      crossAxisDistance: _finiteNumber(
        json['cross_axis_distance'],
        'alternative cross_axis_distance',
      ),
    );
  }
}

class ReviewAlignmentAmbiguity {
  const ReviewAlignmentAmbiguity({
    required this.reason,
    required this.scoreMargin,
    required this.alternatives,
  });

  final String reason;
  final double scoreMargin;
  final List<ReviewAlignmentAlternative> alternatives;

  static ReviewAlignmentAmbiguity fromJson(Object? raw) {
    if (raw is! Map) {
      throw const FormatException('Review JSON ambiguity must be an object.');
    }
    final json = Map<String, dynamic>.from(raw);
    final reason = json['reason'];
    final rawAlternatives = json['alternatives'];
    if (reason is! String || reason.isEmpty || rawAlternatives is! List) {
      throw const FormatException('Review JSON has invalid ambiguity fields.');
    }
    final scoreMargin = _finiteNumber(
      json['score_margin'],
      'ambiguity score_margin',
    );
    if (scoreMargin < 0) {
      throw const FormatException('Review JSON ambiguity margin is negative.');
    }
    final alternatives = <ReviewAlignmentAlternative>[];
    for (final rawAlternative in rawAlternatives) {
      if (rawAlternative is! Map) {
        throw const FormatException('Review JSON has an invalid alternative.');
      }
      alternatives.add(
        ReviewAlignmentAlternative.fromJson(
          Map<String, dynamic>.from(rawAlternative),
        ),
      );
    }
    if (alternatives.length < 2) {
      throw const FormatException(
        'Review JSON ambiguity needs at least two alternatives.',
      );
    }
    return ReviewAlignmentAmbiguity(
      reason: reason,
      scoreMargin: scoreMargin,
      alternatives: List<ReviewAlignmentAlternative>.unmodifiable(alternatives),
    );
  }
}

class ReviewQueueSnapshot {
  const ReviewQueueSnapshot({required this.items});

  final List<ReviewQueueItem> items;

  static ReviewQueueSnapshot fromJsonText(String raw) {
    final decoded = jsonDecode(raw);
    if (decoded is! Map || decoded['schema_version'] != 1) {
      throw const FormatException(
        'Unsupported ScribeForge review JSON schema.',
      );
    }
    final projects = decoded['projects'];
    if (projects is! List) {
      throw const FormatException(
        'Review JSON is missing the projects collection.',
      );
    }

    final items = <ReviewQueueItem>[];
    for (final rawProject in projects) {
      if (rawProject is! Map) {
        throw const FormatException(
          'Review JSON contains an invalid project record.',
        );
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
          throw const FormatException(
            'Review JSON contains an invalid queue item.',
          );
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
    return ReviewQueueSnapshot(
      items: List<ReviewQueueItem>.unmodifiable(items),
    );
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
    this.coordinateFrame = 'unknown',
    this.ocrFrameImagePath,
    this.primaryLineIndexes = const <int>[],
    this.secondaryLineIndexes = const <int>[],
    this.alignmentStatus = 'unknown',
    this.ambiguity,
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
  final String coordinateFrame;
  final String? ocrFrameImagePath;
  final List<int> primaryLineIndexes;
  final List<int> secondaryLineIndexes;
  final String alignmentStatus;
  final ReviewAlignmentAmbiguity? ambiguity;

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
    final coordinateFrame = json['coordinate_frame'];
    final ocrFrameImagePath = json['ocr_frame_image_path'];
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
        sourceImagePath is! String ||
        (coordinateFrame != null && coordinateFrame is! String) ||
        (ocrFrameImagePath != null && ocrFrameImagePath is! String)) {
      throw const FormatException('Review JSON has invalid evidence fields.');
    }

    final primaryLineIndexes = _lineIndexes(
      json['primary_line_indexes'],
      'primary_line_indexes',
    );
    final secondaryLineIndexes = _lineIndexes(
      json['secondary_line_indexes'],
      'secondary_line_indexes',
    );
    final alignmentStatus = json['alignment_status'] ?? 'unknown';
    if (alignmentStatus is! String ||
        !const <String>{
          'unknown',
          'matched',
          'unmatched',
          'ambiguous',
        }.contains(alignmentStatus)) {
      throw const FormatException(
        'Review JSON has an invalid alignment status.',
      );
    }
    final rawAmbiguity = json['ambiguity'];
    final ambiguity = rawAmbiguity == null
        ? null
        : ReviewAlignmentAmbiguity.fromJson(rawAmbiguity);
    if ((alignmentStatus == 'ambiguous') != (ambiguity != null)) {
      throw const FormatException(
        'Review JSON alignment status and ambiguity evidence disagree.',
      );
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
      coordinateFrame: coordinateFrame as String? ?? 'unknown',
      ocrFrameImagePath: ocrFrameImagePath as String?,
      primaryLineIndexes: primaryLineIndexes,
      secondaryLineIndexes: secondaryLineIndexes,
      alignmentStatus: alignmentStatus,
      ambiguity: ambiguity,
    );
  }
}
