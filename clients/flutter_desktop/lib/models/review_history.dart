import 'dart:convert';

import 'review_triage.dart';

enum ReviewHistoryActor {
  human('human', '人工确认'),
  automatic('automatic', '机器接受'),
  vlm('vlm', '视觉模型接受');

  const ReviewHistoryActor(this.value, this.label);
  final String value;
  final String label;
}

enum ReviewHistoryBinding {
  current('current', '引用当前核验批次'),
  historical('historical', '引用历史核验批次'),
  unverified('unverified', '不能确认核验引用');

  const ReviewHistoryBinding(this.value, this.label);
  final String value;
  final String label;
}

class ReviewHistoryFilter {
  const ReviewHistoryFilter({this.pageIndex, this.pairIndex, this.actor});

  final int? pageIndex;
  final int? pairIndex;
  final ReviewHistoryActor? actor;

  bool get isEmpty => pageIndex == null && pairIndex == null && actor == null;

  String get description => <String>[
    if (pageIndex != null) '第 ${pageIndex! + 1} 页',
    if (pairIndex != null) '第 ${pairIndex! + 1} 组',
    if (actor != null) actor!.label,
  ].join(' · ');
}

class ReviewHistoryRecord {
  const ReviewHistoryRecord({
    required this.id,
    required this.pageIndex,
    required this.pairIndex,
    required this.actor,
    required this.createdAt,
    required this.text,
    required this.textTruncated,
    required this.evidenceRefs,
    required this.citationInvalid,
    required this.verificationId,
    required this.currentVerificationId,
    required this.binding,
    required this.latestForPair,
    required this.previousDecisionId,
    required this.riskScore,
    required this.riskReasons,
  });

  factory ReviewHistoryRecord.fromJson(Object? value) {
    if (value is! Map) throw const FormatException('决策记录不是对象。');
    final row = Map<String, dynamic>.from(value);
    final id = _id(row, 'id')!;
    final previous = _id(row, 'previous_decision_id', optional: true);
    final run = _id(row, 'verification_id', optional: true);
    final currentRun = _id(row, 'current_verification_id', optional: true);
    final binding = ReviewHistoryBinding.values
        .where((b) => b.value == row['binding'])
        .firstOrNull;
    final risk = row['risk_score'];
    if (binding == null ||
        (previous != null && previous >= id) ||
        (binding == ReviewHistoryBinding.current &&
            (run == null || run != currentRun)) ||
        (binding == ReviewHistoryBinding.historical &&
            (run == null || currentRun == null || run >= currentRun)) ||
        (risk != null &&
            (risk is! num || !risk.isFinite || risk < 0 || risk > 1))) {
      throw const FormatException('决策记录含无效的核验或风险字段。');
    }
    return ReviewHistoryRecord(
      id: id,
      pageIndex: _id(row, 'page_index', allowZero: true)!,
      pairIndex: _id(row, 'pair_index', allowZero: true)!,
      actor: _text(row, 'actor'),
      createdAt: _text(row, 'created_at'),
      text: _text(row, 'text'),
      textTruncated: _flag(row, 'text_truncated'),
      evidenceRefs: _strings(row, 'evidence_refs'),
      citationInvalid: _flag(row, 'citation_invalid'),
      verificationId: run,
      currentVerificationId: currentRun,
      binding: binding,
      latestForPair: _flag(row, 'latest_for_pair'),
      previousDecisionId: previous,
      riskScore: (risk as num?)?.toDouble(),
      riskReasons: _strings(row, 'risk_reasons'),
    );
  }

  final int id;
  final int pageIndex;
  final int pairIndex;
  final String actor;
  final String createdAt;
  final String text;
  final bool textTruncated;
  final List<String> evidenceRefs;
  final bool citationInvalid;
  final int? verificationId;
  final int? currentVerificationId;
  final ReviewHistoryBinding binding;
  final bool latestForPair;
  final int? previousDecisionId;
  final double? riskScore;
  final List<String> riskReasons;

  String get location => '第 ${pageIndex + 1} 页 · 第 ${pairIndex + 1} 组';

  String get actorLabel =>
      ReviewHistoryActor.values
          .where((value) => value.value == actor)
          .firstOrNull
          ?.label ??
      actor;

  String get orderLabel => latestForPair ? '此页组的最后一条记录' : '此页组还有更新记录';

  bool comparableTo(ReviewHistoryRecord other) =>
      pageIndex == other.pageIndex &&
      pairIndex == other.pairIndex &&
      verificationId != null &&
      verificationId == other.verificationId &&
      binding != ReviewHistoryBinding.unverified &&
      other.binding != ReviewHistoryBinding.unverified;

  String describeForClipboard() => <String>[
    'ScribeForge 决策历史（不是导出成品或完整证据包）',
    '决策 #$id · $location · $actorLabel',
    if (createdAt.isNotEmpty) '记录时间：$createdAt（数据库时间）',
    '核验绑定：${binding.label}；$orderLabel',
    if (verificationId != null) '记录引用核验 #$verificationId',
    if (currentVerificationId != null) '读取时当前核验 #$currentVerificationId',
    if (riskScore != null) '记录风险：${(riskScore! * 100).round()}%',
    if (riskReasons.isNotEmpty)
      '风险原因：${riskReasons.map(reviewReasonLabel).join('、')}',
    if (citationInvalid) '证据引用损坏或不能解析。',
    if (evidenceRefs.isNotEmpty) '证据引用：${evidenceRefs.join('、')}',
    '',
    textTruncated ? '以下仅为文字节选：' : '记录的文字：',
    text,
  ].join('\n');
}

class ReviewHistoryBatch {
  const ReviewHistoryBatch({
    required this.projectId,
    required this.projectTitle,
    required this.snapshotId,
    required this.nextBeforeId,
    required this.records,
  });

  factory ReviewHistoryBatch.fromText(String source) {
    final raw = jsonDecode(source);
    if (raw is! Map<String, dynamic> ||
        raw['schema_version'] != 1 ||
        raw['decisions'] is! List) {
      throw const FormatException('核心未返回支持的决策历史格式。');
    }
    final snapshot = _id(raw, 'snapshot_id', allowZero: true)!;
    final cursor = _id(raw, 'next_before_id', optional: true);
    final records = <ReviewHistoryRecord>[
      for (final row in raw['decisions'] as List)
        ReviewHistoryRecord.fromJson(row),
    ];
    var priorId = snapshot;
    for (var index = 0; index < records.length; index++) {
      final id = records[index].id;
      if (id > snapshot || (index > 0 && priorId <= id)) {
        throw const FormatException('决策历史顺序无效。');
      }
      priorId = id;
    }
    if (cursor != null && (records.isEmpty || cursor != records.last.id)) {
      throw const FormatException('决策历史分页游标无效。');
    }
    return ReviewHistoryBatch(
      projectId: _id(raw, 'project_id')!,
      projectTitle: _text(raw, 'project_title'),
      snapshotId: snapshot,
      nextBeforeId: cursor,
      records: List<ReviewHistoryRecord>.unmodifiable(records),
    );
  }

  final int projectId;
  final String projectTitle;
  final int snapshotId;
  final int? nextBeforeId;
  final List<ReviewHistoryRecord> records;
}

int? _id(
  Map<String, dynamic> map,
  String key, {
  bool optional = false,
  bool allowZero = false,
}) {
  final value = map[key];
  if (value == null && optional) return null;
  if (value is int && value >= (allowZero ? 0 : 1)) return value;
  throw FormatException('无效的决策历史字段：$key。');
}

String _text(Map<String, dynamic> map, String key) {
  final value = map[key];
  if (value is String) return value;
  throw FormatException('无效的决策历史字段：$key。');
}

bool _flag(Map<String, dynamic> map, String key) {
  final value = map[key];
  if (value is bool) return value;
  throw FormatException('无效的决策历史字段：$key。');
}

List<String> _strings(Map<String, dynamic> map, String key) {
  final value = map[key];
  if (value is List && value.every((element) => element is String)) {
    return List<String>.unmodifiable(value.cast<String>());
  }
  throw FormatException('无效的决策历史字段：$key。');
}
