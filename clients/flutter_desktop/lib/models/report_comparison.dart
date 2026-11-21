import 'dart:convert';

typedef _Row = Map<String, dynamic>;

_Row _object(Object? value) {
  if (value is! _Row) throw const FormatException('报告响应包含无效对象。');
  return value;
}

List<T> _list<T>(Object? value, T Function(_Row) parse) {
  if (value is! List) throw const FormatException('报告响应包含无效列表。');
  return List<T>.unmodifiable(value.map((item) => parse(_object(item))));
}

String _text(Object? value) {
  if (value is! String || value.isEmpty) {
    throw const FormatException('报告响应包含无效文字。');
  }
  return value;
}

int _count(Object? value) {
  if (value is! int || value < 0) {
    throw const FormatException('报告响应包含无效计数。');
  }
  return value;
}

bool _flag(Object? value) {
  if (value is! bool) throw const FormatException('报告响应包含无效标记。');
  return value;
}

num? _number(Object? value) {
  if (value == null) return null;
  if (value is! num || !value.isFinite) {
    throw const FormatException('报告响应包含无效数值。');
  }
  return value;
}

class ReportDelta {
  ReportDelta.fromJson(_Row row)
    : before = _number(row['old']),
      after = _number(row['new']),
      change = _number(row['delta']);

  final num? before;
  final num? after;
  final num? change;

  static Map<String, ReportDelta> readMap(Object? value) {
    final entries = _object(value).entries;
    return Map.unmodifiable({
      for (final entry in entries)
        entry.key: ReportDelta.fromJson(_object(entry.value)),
    });
  }
}

class ReportIdentity {
  ReportIdentity.fromJson(_Row row)
    : digest = _text(row['sha256']),
      title = _text(row['title']),
      projectId = _count(row['project_id']),
      timingScope = _text(row['timing_scope']),
      measured = _count(row['timed_jobs']),
      unmeasured = _count(row['unavailable_jobs']) {
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) {
      throw const FormatException('报告指纹无效。');
    }
  }

  final String digest;
  final String title;
  final int projectId;
  final String timingScope;
  final int measured;
  final int unmeasured;
}

class ReportStageDelta {
  ReportStageDelta.fromJson(_Row row)
    : name = _text(row['stage']),
      seconds = ReportDelta.fromJson(_object(row['seconds'])),
      counts = ReportDelta.readMap(row['counts']);

  final String name;
  final ReportDelta seconds;
  final Map<String, ReportDelta> counts;
}

class ReportPageStageDelta {
  ReportPageStageDelta.fromJson(_Row row)
    : name = _text(row['stage']),
      oldState = row['old_state'] == null ? null : _text(row['old_state']),
      newState = row['new_state'] == null ? null : _text(row['new_state']),
      seconds = ReportDelta.fromJson(_object(row['seconds'])),
      attempts = ReportDelta.fromJson(_object(row['attempts']));

  final String name;
  final String? oldState;
  final String? newState;
  final ReportDelta seconds;
  final ReportDelta attempts;
}

class ReportPageDelta {
  ReportPageDelta.fromJson(_Row row)
    : index = _count(row['page_index']),
      oldStatus = row['old_status'] == null ? null : _text(row['old_status']),
      newStatus = row['new_status'] == null ? null : _text(row['new_status']),
      coverageChanged = _flag(row['coverage_changed']),
      seconds = ReportDelta.fromJson(_object(row['seconds'])),
      counts = ReportDelta.readMap(row['counts']),
      reasons = ReportDelta.readMap(row['reasons']),
      stages = _list(row['stages'], ReportPageStageDelta.fromJson),
      categories = List<String>.unmodifiable(
        (row['categories'] as List).map(_text),
      );

  final int index;
  final String? oldStatus;
  final String? newStatus;
  final bool coverageChanged;
  final ReportDelta seconds;
  final Map<String, ReportDelta> counts;
  final Map<String, ReportDelta> reasons;
  final List<String> categories;
  final List<ReportPageStageDelta> stages;
}

class ReportComparison {
  factory ReportComparison.decode(String text) {
    final row = _object(jsonDecode(text));
    if (row['schema_version'] != 1 ||
        row['kind'] != 'processing_report_comparison') {
      throw const FormatException('不支持此处理报告对比响应。');
    }
    return ReportComparison._(row);
  }

  ReportComparison._(_Row row)
    : baseline = ReportIdentity.fromJson(_object(row['baseline'])),
      candidate = ReportIdentity.fromJson(_object(row['candidate'])),
      sameLabels = _flag(row['same_labels']),
      pageComparison = _flag(row['page_comparison']),
      sameScope = _flag(row['same_timing_scope']),
      sameCoverage = _flag(row['same_timing_coverage']),
      summary = ReportDelta.readMap(row['summary']),
      review = ReportDelta.readMap(row['review']),
      reasons = ReportDelta.readMap(row['reasons']),
      stages = _list(row['stages'], ReportStageDelta.fromJson),
      seconds = ReportDelta.fromJson(_object(row['seconds'])),
      filter = _text(row['filter']),
      order = _text(row['order']),
      offset = _count(row['offset']),
      matched = _count(row['matched']),
      pageCounts = Map.unmodifiable(
        _object(
          row['page_counts'],
        ).map((key, value) => MapEntry(key, _count(value))),
      ),
      nextOffset = row['next_offset'] == null
          ? null
          : _count(row['next_offset']),
      pages = _list(row['pages'], ReportPageDelta.fromJson) {
    final end = offset + pages.length;
    if (end > matched ||
        pages.map((page) => page.index).toSet().length != pages.length ||
        (end < matched
            ? nextOffset != end || pages.isEmpty
            : nextOffset != null)) {
      throw const FormatException('对比响应的分页信息不一致。');
    }
  }

  final ReportIdentity baseline;
  final ReportIdentity candidate;
  final bool sameLabels;
  final bool pageComparison;
  final bool sameScope;
  final bool sameCoverage;
  final Map<String, ReportDelta> summary;
  final Map<String, ReportDelta> review;
  final Map<String, ReportDelta> reasons;
  final List<ReportStageDelta> stages;
  final ReportDelta seconds;
  final String filter;
  final String order;
  final int offset;
  final int matched;
  final Map<String, int> pageCounts;
  final int? nextOffset;
  final List<ReportPageDelta> pages;
}
