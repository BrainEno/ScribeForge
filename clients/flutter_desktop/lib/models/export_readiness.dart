import 'dart:convert';

import 'ocr_evidence.dart';
import 'review_triage.dart';

enum ExportIssueKind {
  unverifiedPage('unverified_page', '页未核验'),
  unresolvedPair('unresolved_pair', '配对待复核'),
  unconfirmedPage('unconfirmed_page', '无文字页待确认'),
  spotCheck('spot_check', '已接受内容待抽查'),
  pageImage('page_image', '必需页图异常'),
  layoutRegion('layout_region', '图表区域可能遗漏'),
  evidenceError('evidence_error', '证据链无法解析'),
  noPages('no_pages', '项目没有可导出的页');

  const ExportIssueKind(this.value, this.label);
  final String value;
  final String label;

  static ExportIssueKind parse(String value) {
    final result = values.where((kind) => kind.value == value).firstOrNull;
    if (result == null) throw const FormatException('无法识别导出检查问题类型。');
    return result;
  }

  String get advice => switch (this) {
    unverifiedPage => '请到处理页完成这一页的双路 OCR 与核验。',
    unresolvedPair => '对照两路 OCR 与页图，再明确记录人工决策。',
    unconfirmedPage => '请检查原页。只有真的无文字时才确认；有文字漏读时应重新处理。',
    spotCheck => '检查所引用的 OCR 和决策历史。关闭严格模式不会让风险消失。',
    pageImage => '不要从导出中省略此页图。可从已验证备份恢复，或重新导入。',
    layoutRegion => '请对照原页检查。当前导出不保留混排正文中的图表，模型检测也可能漏报。',
    evidenceError => '不要靠旧草稿或历史文字绕过此问题。请先从项目安全页检查证据。',
    noPages => '先导入来源并完成处理。',
  };
}

class ExportReadinessOptions {
  const ExportReadinessOptions({
    required this.strict,
    required this.includePageFurniture,
    this.page,
  });

  final bool strict;
  final bool includePageFurniture;
  final int? page;

  ExportReadinessOptions forPage(int? page) => ExportReadinessOptions(
    strict: strict,
    includePageFurniture: includePageFurniture,
    page: page,
  );
}

class ExportReadinessIssue {
  ExportReadinessIssue(EvidenceFields fields)
    : kind = ExportIssueKind.parse(fields.text('kind')),
      blocking = fields.flag('blocking'),
      page = fields.optionalIndex('page_index'),
      pair = fields.optionalIndex('pair_index'),
      detail = fields.text('detail'),
      reasons = fields.strings('reasons'),
      characters = fields.strings('characters'),
      actor = fields.optionalText('actor'),
      score = fields.optionalNumber('score'),
      ink = fields.optionalNumber('ink') {
    final needsPair =
        kind == ExportIssueKind.unresolvedPair ||
        kind == ExportIssueKind.spotCheck;
    if (needsPair != (pair != null) ||
        (pair != null && page == null) ||
        (!blocking &&
            kind != ExportIssueKind.spotCheck &&
            kind != ExportIssueKind.layoutRegion) ||
        (score != null && (score! < 0 || score! > 1)) ||
        (ink != null && (ink! < 0 || ink! > 1))) {
      throw const FormatException('导出检查包含无效的问题记录。');
    }
  }

  final ExportIssueKind kind;
  final bool blocking;
  final int? page;
  final int? pair;
  final String detail;
  final List<String> reasons;
  final List<String> characters;
  final String? actor;
  final double? score;
  final double? ink;

  String get location => <String>[
    if (page == null) '项目级' else '第 ${page! + 1} 页',
    if (pair != null) '第 ${pair! + 1} 组',
  ].join(' · ');

  String get reasonDescription => reasons.map(reviewReasonLabel).join('、');

  String? get actorLabel => switch (actor) {
    'automatic' => '自动接受',
    'vlm' => '视觉模型接受',
    'human' => '人工接受',
    _ => actor,
  };

  String get synopsis => '$location：${kind.label}${blocking ? '（阻塞）' : '（提醒）'}';
}

class ExportContentSummary {
  ExportContentSummary(EvidenceFields fields)
    : pages = fields.index('pages'),
      chapters = fields.index('chapters'),
      images = fields.index('images'),
      textlessPages = fields.index('confirmed_textless_pages'),
      omittedFurniture = fields.index('omitted_furniture_pairs'),
      acceptedPairs = fields.index('accepted_pairs'),
      machineAccepted = fields.index('machine_accepted'),
      humanAccepted = fields.index('human_accepted'),
      maxMachineRisk = fields.number('max_machine_risk') {
    if (acceptedPairs != machineAccepted + humanAccepted ||
        maxMachineRisk < 0 ||
        maxMachineRisk > 1 ||
        textlessPages > pages ||
        images > pages) {
      throw const FormatException('导出检查的内容统计不一致。');
    }
  }

  final int pages;
  final int chapters;
  final int images;
  final int textlessPages;
  final int omittedFurniture;
  final int acceptedPairs;
  final int machineAccepted;
  final int humanAccepted;
  final double maxMachineRisk;
}

class ExportReadinessReport {
  ExportReadinessReport.fromText(
    String source, {
    required int projectId,
    required ExportReadinessOptions expected,
  }) : this._parse(EvidenceFields(jsonDecode(source)), projectId, expected);

  ExportReadinessReport._parse(
    EvidenceFields fields,
    int projectId,
    this.options,
  ) : title = fields.text('project_title'),
      ready = fields.flag('ready'),
      blockingCount = fields.index('blocking_count'),
      matchingCount = fields.index('matching_count'),
      counts = _counts(fields.object('issue_counts')),
      issues = List<ExportReadinessIssue>.unmodifiable(
        fields.rows('issues').map(ExportReadinessIssue.new),
      ),
      summary = _summary(fields) {
    final echoed = fields.object('options');
    if (fields.index('schema_version') != 1 ||
        fields.index('project_id') != projectId ||
        echoed.flag('strict_acceptance') != options.strict ||
        echoed.flag('include_page_furniture') != options.includePageFurniture ||
        echoed.optionalIndex('page_index') != options.page) {
      throw const FormatException('导出检查不属于当前项目或配置。');
    }
    final observed = <ExportIssueKind, int>{};
    for (final issue in issues) {
      observed.update(issue.kind, (count) => count + 1, ifAbsent: () => 1);
      if ((options.page != null &&
              issue.page != null &&
              issue.page != options.page) ||
          (issue.kind == ExportIssueKind.spotCheck &&
              issue.blocking != options.strict)) {
        throw const FormatException('导出检查结果超出当前检查范围。');
      }
    }
    final advisoryCount =
        (options.strict ? 0 : (counts[ExportIssueKind.spotCheck] ?? 0)) +
        (counts[ExportIssueKind.layoutRegion] ?? 0);
    if (ready != (blockingCount == 0) ||
        blockingCount != totalIssues - advisoryCount ||
        matchingCount < issues.length ||
        matchingCount > totalIssues ||
        (options.page == null && matchingCount != totalIssues) ||
        observed.entries.any(
          (entry) => entry.value > (counts[entry.key] ?? 0),
        )) {
      throw const FormatException('导出检查的问题数量不一致。');
    }
  }

  final String title;
  final bool ready;
  final int blockingCount;
  final int matchingCount;
  final Map<ExportIssueKind, int> counts;
  final List<ExportReadinessIssue> issues;
  final ExportContentSummary? summary;
  final ExportReadinessOptions options;

  int get totalIssues => counts.values.fold(0, (total, value) => total + value);
  bool get truncated => matchingCount > issues.length;

  static Map<ExportIssueKind, int> _counts(EvidenceFields fields) =>
      Map.unmodifiable(<ExportIssueKind, int>{
        for (final kind in ExportIssueKind.values)
          if (fields.optionalIndex(kind.value) case final count?) kind: count,
      });

  static ExportContentSummary? _summary(EvidenceFields fields) {
    final summary = fields.optionalObject('summary');
    return summary == null ? null : ExportContentSummary(summary);
  }

  String describe() => <String>[
    'ScribeForge 导出内容检查 · $title',
    ready ? '本次内容检查未发现阻塞。' : '本次内容检查发现 $blockingCount 项阻塞。',
    '严格校验：${options.strict ? '开启' : '关闭'}；保留页边文字：${options.includePageFurniture ? '是' : '否'}。',
    if (options.page != null) '明细范围：第 ${options.page! + 1} 页与项目级问题。',
    for (final entry in counts.entries) '${entry.key.label}：${entry.value}',
    if (truncated) '只列出 $matchingCount 项匹配问题中的前 ${issues.length} 项。',
    for (final issue in issues) '- ${issue.synopsis}',
    '图表检测可能漏报；没有提醒不代表图文完整。混排图表目前不会嵌入。',
    '这不是正式导出或文件校验。',
  ].join('\n');
}
