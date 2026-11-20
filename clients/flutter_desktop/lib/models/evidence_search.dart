import 'dart:convert';

import 'ocr_evidence.dart';
import 'review_history.dart';

enum EvidenceSearchSource {
  ocr('OCR 原文'),
  decisions('已记录决策');

  const EvidenceSearchSource(this.label);
  final String label;
}

class EvidenceSearchQuery {
  const EvidenceSearchQuery({
    required this.term,
    required this.source,
    this.pageStart,
    this.pageEnd,
    this.actor,
  });

  static const maximumTermLength = 160;
  final String term;
  final EvidenceSearchSource source;
  final int? pageStart;
  final int? pageEnd;
  final ReviewHistoryActor? actor;

  String get description => <String>[
    source.label,
    if (pageStart != null && pageEnd == pageStart)
      '第 ${pageStart! + 1} 页'
    else if (pageStart != null && pageEnd != null)
      '第 ${pageStart! + 1}–${pageEnd! + 1} 页'
    else if (pageStart != null)
      '第 ${pageStart! + 1} 页起'
    else if (pageEnd != null)
      '至第 ${pageEnd! + 1} 页',
    if (actor != null) actor!.label,
  ].join(' · ');

  bool includes(EvidenceSearchHit hit) =>
      (pageStart == null || hit.page >= pageStart!) &&
      (pageEnd == null || hit.page <= pageEnd!) &&
      (actor == null ||
          (hit is DecisionSearchHit && actor!.value == hit.actor));
}

class SearchExcerpt {
  SearchExcerpt(EvidenceFields fields, String expected)
    : before = fields.text('before'),
      match = fields.text('match'),
      after = fields.text('after'),
      omittedBefore = fields.flag('omitted_before'),
      omittedAfter = fields.flag('omitted_after') {
    if (match != expected) throw const FormatException('搜索结果不包含所请求的原文。');
  }

  final String before;
  final String match;
  final String after;
  final bool omittedBefore;
  final bool omittedAfter;

  bool get truncated => omittedBefore || omittedAfter;
  String get text => '$before$match$after';
}

sealed class EvidenceSearchHit {
  EvidenceSearchHit(EvidenceFields fields, String term)
    : id = fields.index('id'),
      page = fields.index('page_index'),
      excerpt = SearchExcerpt(fields.object('excerpt'), term) {
    if (id < 1) throw const FormatException('缺少证据记录标识。');
  }

  final int id;
  final int page;
  final SearchExcerpt excerpt;
  String get location;
  String get summary;
}

class OcrSearchHit extends EvidenceSearchHit {
  OcrSearchHit(EvidenceFields fields, String term)
    : run = fields.index('run_id'),
      line = fields.index('line_index'),
      engine = fields.text('engine'),
      version = fields.text('engine_version'),
      role = fields.text('role_in_latest_verification'),
      box = fields.numbers('box'),
      super(fields, term) {
    if (box.length != 4 ||
        !const {'primary', 'secondary', 'other', 'unverified'}.contains(role)) {
      throw const FormatException('OCR 搜索结果包含无效坐标或批次角色。');
    }
  }

  final int run;
  final int line;
  final String engine;
  final String version;
  final String role;
  final List<double> box;

  @override
  String get location => '第 ${page + 1} 页 · 行索引 $line';

  String get roleLabel => switch (role) {
    'primary' => '最新核验主侧',
    'secondary' => '最新核验辅侧',
    'other' => '不在最新核验两侧',
    _ => '此页尚无核验',
  };

  String get coordinates =>
      box.map((value) => value.toStringAsFixed(4)).join(', ');

  @override
  String get summary => '$engine · $version · OCR #$run · $roleLabel';
}

class DecisionSearchHit extends EvidenceSearchHit {
  DecisionSearchHit(EvidenceFields fields, String term)
    : pair = fields.index('pair_index'),
      actor = fields.text('actor'),
      createdAt = fields.text('created_at'),
      citations = fields.strings('evidence_refs'),
      citationsIncomplete = fields.flag('citations_incomplete'),
      verification = fields.optionalIndex('verification_id'),
      binding = fields.text('binding'),
      latestForPair = fields.flag('latest_for_pair'),
      super(fields, term) {
    if (!const {'current', 'historical', 'unverified'}.contains(binding) ||
        (binding != 'unverified' && verification == null)) {
      throw const FormatException('决策搜索结果含无效核验绑定。');
    }
  }

  final int pair;
  final String actor;
  final String createdAt;
  final List<String> citations;
  final bool citationsIncomplete;
  final int? verification;
  final String binding;
  final bool latestForPair;

  @override
  String get location => '第 ${page + 1} 页 · 第 ${pair + 1} 组';

  String get actorLabel =>
      ReviewHistoryActor.values
          .where((value) => value.value == actor)
          .firstOrNull
          ?.label ??
      actor;

  String get bindingLabel => switch (binding) {
    'current' => '引用当前核验批次',
    'historical' => '引用历史核验批次',
    _ => '不能确认核验引用',
  };

  @override
  String get summary => '决策 #$id · $actorLabel · $bindingLabel';
}

class EvidenceSearchBatch {
  EvidenceSearchBatch.fromText(
    String text, {
    required int projectId,
    required EvidenceSearchQuery query,
    int? requestedSnapshot,
    int? before,
  }) : this._decode(
         EvidenceFields(jsonDecode(text)),
         projectId,
         query,
         requestedSnapshot,
         before,
       );

  EvidenceSearchBatch._decode(
    EvidenceFields fields,
    int projectId,
    EvidenceSearchQuery query,
    int? requestedSnapshot,
    int? before,
  ) : title = fields.text('project_title'),
      snapshot = fields.index('snapshot_id'),
      nextBefore = fields.optionalIndex('next_before'),
      hits = List<EvidenceSearchHit>.unmodifiable(
        fields
            .rows('hits')
            .map(
              (row) => switch (query.source) {
                EvidenceSearchSource.ocr => OcrSearchHit(row, query.term),
                EvidenceSearchSource.decisions => DecisionSearchHit(
                  row,
                  query.term,
                ),
              },
            ),
      ) {
    if (fields.index('schema_version') != 1 ||
        fields.index('project_id') != projectId ||
        fields.text('term') != query.term ||
        fields.text('source') != query.source.name ||
        (requestedSnapshot != null && requestedSnapshot != snapshot)) {
      throw const FormatException('搜索结果不属于请求的项目、查询或快照。');
    }
    var previous = before;
    for (final hit in hits) {
      if (hit.id > snapshot ||
          (previous != null && hit.id >= previous) ||
          !query.includes(hit)) {
        throw const FormatException('搜索结果超出请求范围或顺序无效。');
      }
      previous = hit.id;
    }
    if (nextBefore != null && (hits.isEmpty || nextBefore != hits.last.id)) {
      throw const FormatException('搜索结果分页标记无效。');
    }
  }

  final String title;
  final int snapshot;
  final int? nextBefore;
  final List<EvidenceSearchHit> hits;
}
