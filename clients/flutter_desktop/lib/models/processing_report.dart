import 'dart:convert';

/// Parsed view over the CLI's schema-v1 `report --json` payload
/// (issue #219). Every timing field is nullable on purpose: the report
/// marks unmeasured data as N/A and the UI must show N/A, never 0.
class ProcessingReportSnapshot {
  const ProcessingReportSnapshot({
    required this.projectId,
    required this.title,
    required this.pagesTotal,
    required this.ocrCompletePages,
    required this.failedPages,
    required this.runningPages,
    required this.review,
    required this.riskReasons,
    required this.riskDistribution,
    required this.highRiskPairsTotal,
    required this.timing,
    required this.slowestPages,
    required this.stages,
    required this.pages,
    required this.rawJson,
  });

  final int? projectId;
  final String title;
  final int pagesTotal;
  final int ocrCompletePages;
  final int failedPages;
  final int runningPages;
  final ReviewSummary review;
  final Map<String, int> riskReasons;
  final List<RiskBucket> riskDistribution;
  final int highRiskPairsTotal;
  final TimingSummary timing;
  final List<SlowestPage> slowestPages;
  final List<StageSummary> stages;
  final List<PageReportEntry> pages;

  /// The untouched JSON text, used for the "copy/export JSON" action.
  final String rawJson;

  factory ProcessingReportSnapshot.fromJsonText(String source) {
    final decoded = jsonDecode(source);
    if (decoded is! Map) {
      throw const FormatException('report must be a JSON object.');
    }
    final json = Map<String, dynamic>.from(decoded);
    if (json['schema_version'] != 1) {
      throw const FormatException('Unsupported report schema version.');
    }
    return ProcessingReportSnapshot(
      projectId: json['project_id'] is int ? json['project_id'] as int : null,
      title: json['title'] is String ? json['title'] as String : '',
      pagesTotal: _int(json['pages_total']),
      ocrCompletePages: _int(json['ocr_complete_pages']),
      failedPages: _int(json['failed_pages']),
      runningPages: _int(json['running_pages']),
      review: ReviewSummary.fromMap(_map(json['review'])),
      riskReasons: _stringIntMap(json['risk_reasons']),
      riskDistribution: [
        for (final raw in _list(json['risk_distribution']))
          if (raw is Map) RiskBucket.fromMap(Map<String, dynamic>.from(raw)),
      ],
      highRiskPairsTotal: _int(json['high_risk_pairs_total']),
      timing: TimingSummary.fromMap(_map(json['timing'])),
      slowestPages: [
        for (final raw in _list(json['slowest_pages']))
          if (raw is Map) SlowestPage.fromMap(Map<String, dynamic>.from(raw)),
      ],
      stages: [
        for (final raw in _list(json['stages']))
          if (raw is Map) StageSummary.fromMap(Map<String, dynamic>.from(raw)),
      ],
      pages: [
        for (final raw in _list(json['pages']))
          if (raw is Map)
            PageReportEntry.fromMap(Map<String, dynamic>.from(raw)),
      ],
      rawJson: source,
    );
  }

  /// Pages that deserve a human look: open pairs or high-risk pairs.
  List<PageReportEntry> get pagesNeedingReview => [
    for (final page in pages)
      if (page.openPairs > 0 || page.highRiskPairs > 0) page,
  ];

  static int _int(Object? value) => value is int ? value : 0;

  static Map<String, dynamic> _map(Object? value) =>
      value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};

  static List<Object?> _list(Object? value) =>
      value is List ? value : const <Object?>[];

  static Map<String, int> _stringIntMap(Object? value) {
    final map = _map(value);
    return <String, int>{
      for (final entry in map.entries)
        entry.key: entry.value is int ? entry.value as int : 0,
    };
  }
}

class ReviewSummary {
  const ReviewSummary({
    required this.pairs,
    required this.acceptedAutomatic,
    required this.acceptedVlm,
    required this.acceptedHuman,
    required this.openPairs,
    required this.openHighRisk,
    required this.openBlocked,
    required this.reviewRequiredRatio,
  });

  final int pairs;
  final int acceptedAutomatic;
  final int acceptedVlm;
  final int acceptedHuman;
  final int openPairs;
  final int openHighRisk;
  final int openBlocked;

  /// Nullable: N/A when the store has no reviewable pairs at all.
  final double? reviewRequiredRatio;

  factory ReviewSummary.fromMap(Map<String, dynamic> json) {
    final ratio = json['review_required_ratio'];
    return ReviewSummary(
      pairs: ProcessingReportSnapshot._int(json['pairs']),
      acceptedAutomatic: ProcessingReportSnapshot._int(
        json['accepted_automatic'],
      ),
      acceptedVlm: ProcessingReportSnapshot._int(json['accepted_vlm']),
      acceptedHuman: ProcessingReportSnapshot._int(json['accepted_human']),
      openPairs: ProcessingReportSnapshot._int(json['open_pairs']),
      openHighRisk: ProcessingReportSnapshot._int(json['open_high_risk']),
      openBlocked: ProcessingReportSnapshot._int(json['open_blocked']),
      reviewRequiredRatio: ratio is num && ratio.isFinite
          ? ratio.toDouble()
          : null,
    );
  }
}

class RiskBucket {
  const RiskBucket({
    required this.low,
    required this.high,
    required this.count,
  });

  final int low;
  final int high;
  final int count;

  factory RiskBucket.fromMap(Map<String, dynamic> json) => RiskBucket(
    low: ProcessingReportSnapshot._int(json['low']),
    high: ProcessingReportSnapshot._int(json['high']),
    count: ProcessingReportSnapshot._int(json['count']),
  );
}

class EngineTiming {
  const EngineTiming({
    required this.engine,
    required this.stage,
    required this.timedJobs,
    required this.unavailableJobs,
    required this.observedSeconds,
    required this.share,
    required this.meanSeconds,
  });

  final String engine;
  final String stage;
  final int timedJobs;
  final int unavailableJobs;

  /// Nullable: N/A when no timed jobs exist for this engine.
  final double? observedSeconds;
  final double? share;
  final double? meanSeconds;

  factory EngineTiming.fromMap(Map<String, dynamic> json) {
    double? ratio(Object? value) =>
        value is num && value.isFinite ? value.toDouble() : null;

    return EngineTiming(
      engine: json['engine'] is String ? json['engine'] as String : '',
      stage: json['stage'] is String ? json['stage'] as String : '',
      timedJobs: ProcessingReportSnapshot._int(json['timed_jobs']),
      unavailableJobs: ProcessingReportSnapshot._int(json['unavailable_jobs']),
      observedSeconds: ratio(json['observed_seconds']),
      share: ratio(json['share']),
      meanSeconds: ratio(json['mean_seconds']),
    );
  }
}

class TimingSummary {
  const TimingSummary({
    required this.scope,
    required this.observedSeconds,
    required this.timedJobs,
    required this.unavailableJobs,
    required this.bottleneckStage,
    required this.engines,
  });

  final String scope;

  /// Nullable: N/A when nothing was measured.
  final double? observedSeconds;
  final int timedJobs;
  final int unavailableJobs;

  /// Nullable: no bottleneck when nothing was measured.
  final String? bottleneckStage;
  final List<EngineTiming> engines;

  factory TimingSummary.fromMap(Map<String, dynamic> json) {
    final observed = json['observed_seconds'];
    return TimingSummary(
      scope: json['scope'] is String ? json['scope'] as String : '',
      observedSeconds: observed is num && observed.isFinite
          ? observed.toDouble()
          : null,
      timedJobs: ProcessingReportSnapshot._int(json['timed_jobs']),
      unavailableJobs: ProcessingReportSnapshot._int(json['unavailable_jobs']),
      bottleneckStage: json['bottleneck_stage'] is String
          ? json['bottleneck_stage'] as String
          : null,
      engines: [
        for (final raw in ProcessingReportSnapshot._list(json['engines']))
          if (raw is Map) EngineTiming.fromMap(Map<String, dynamic>.from(raw)),
      ],
    );
  }
}

class SlowestPage {
  const SlowestPage({required this.pageIndex, required this.observedSeconds});

  final int pageIndex;

  /// Nullable: N/A when the page has no measured timing.
  final double? observedSeconds;

  factory SlowestPage.fromMap(Map<String, dynamic> json) {
    final seconds = json['observed_seconds'];
    return SlowestPage(
      pageIndex: ProcessingReportSnapshot._int(json['page_index']),
      observedSeconds: seconds is num && seconds.isFinite
          ? seconds.toDouble()
          : null,
    );
  }
}

class StageSummary {
  const StageSummary({
    required this.stage,
    required this.jobs,
    required this.pending,
    required this.running,
    required this.succeeded,
    required this.failed,
    required this.attempts,
    required this.observedSeconds,
  });

  final String stage;
  final int jobs;
  final int pending;
  final int running;
  final int succeeded;
  final int failed;
  final int attempts;

  /// Nullable: N/A when no jobs in this stage were timed.
  final double? observedSeconds;

  factory StageSummary.fromMap(Map<String, dynamic> json) {
    final seconds = json['observed_seconds'];
    return StageSummary(
      stage: json['stage'] is String ? json['stage'] as String : '',
      jobs: ProcessingReportSnapshot._int(json['jobs']),
      pending: ProcessingReportSnapshot._int(json['pending']),
      running: ProcessingReportSnapshot._int(json['running']),
      succeeded: ProcessingReportSnapshot._int(json['succeeded']),
      failed: ProcessingReportSnapshot._int(json['failed']),
      attempts: ProcessingReportSnapshot._int(json['attempts']),
      observedSeconds: seconds is num && seconds.isFinite
          ? seconds.toDouble()
          : null,
    );
  }
}

class PageReportEntry {
  const PageReportEntry({
    required this.pageIndex,
    required this.status,
    required this.alignedPairs,
    required this.highRiskPairs,
    required this.openPairs,
    required this.riskReasons,
    required this.observedSeconds,
  });

  final int pageIndex;
  final String status;
  final int alignedPairs;
  final int highRiskPairs;
  final int openPairs;
  final Map<String, int> riskReasons;

  /// Nullable: N/A when the page has no measured timing.
  final double? observedSeconds;

  bool get needsReview => openPairs > 0 || highRiskPairs > 0;

  factory PageReportEntry.fromMap(Map<String, dynamic> json) {
    final seconds = json['observed_seconds'];
    return PageReportEntry(
      pageIndex: ProcessingReportSnapshot._int(json['page_index']),
      status: json['status'] is String ? json['status'] as String : '',
      alignedPairs: ProcessingReportSnapshot._int(json['aligned_pairs']),
      highRiskPairs: ProcessingReportSnapshot._int(json['high_risk_pairs']),
      openPairs: ProcessingReportSnapshot._int(json['open_pairs']),
      riskReasons: <String, int>{
        for (final entry in ProcessingReportSnapshot._map(
          json['risk_reasons'],
        ).entries)
          entry.key: entry.value is int ? entry.value as int : 0,
      },
      observedSeconds: seconds is num && seconds.isFinite
          ? seconds.toDouble()
          : null,
    );
  }
}

/// Renders a compact Markdown summary for copy/export (issue #219).
/// N/A is rendered explicitly; nothing unmeasured is turned into 0.
String processingReportMarkdown(ProcessingReportSnapshot report) {
  String seconds(double? value) =>
      value == null ? 'N/A（未测量）' : '${value.toStringAsFixed(1)} 秒';

  String percent(double? value) =>
      value == null ? 'N/A' : '${(value * 100).toStringAsFixed(1)}%';

  final buffer = StringBuffer()
    ..writeln('# 处理报告：${report.title.isEmpty ? '未命名项目' : report.title}')
    ..writeln()
    ..writeln(
      '- 页数：${report.pagesTotal}（OCR 完成 ${report.ocrCompletePages}，'
      '失败 ${report.failedPages}，进行中 ${report.runningPages}）',
    )
    ..writeln(
      '- 实测总耗时：${seconds(report.timing.observedSeconds)}'
      '（${report.timing.scope}；已计时任务 ${report.timing.timedJobs}，'
      '未计时 ${report.timing.unavailableJobs}）',
    )
    ..writeln('- 瓶颈阶段：${report.timing.bottleneckStage ?? 'N/A（无已计时任务）'}')
    ..writeln(
      '- 复核率：${percent(report.review.reviewRequiredRatio)}'
      '（机器自动采纳 ${report.review.acceptedAutomatic}，'
      'VLM 采纳 ${report.review.acceptedVlm}，'
      '人工采纳 ${report.review.acceptedHuman}，'
      '待处理 ${report.review.openPairs}，'
      '其中高风险 ${report.review.openHighRisk}，'
      '阻塞 ${report.review.openBlocked}）',
    )
    ..writeln('- 高风险配对总数：${report.highRiskPairsTotal}');
  if (report.timing.engines.isNotEmpty) {
    buffer
      ..writeln()
      ..writeln('## 引擎耗时')
      ..writeln()
      ..writeln('| 引擎 | 阶段 | 耗时 | 占比 | 均值 |')
      ..writeln('| --- | --- | --- | --- | --- |');
    for (final engine in report.timing.engines) {
      buffer.writeln(
        '| ${engine.engine} | ${engine.stage} | ${seconds(engine.observedSeconds)} '
        '| ${percent(engine.share)} | ${seconds(engine.meanSeconds)} |',
      );
    }
  }
  if (report.slowestPages.isNotEmpty) {
    buffer
      ..writeln()
      ..writeln('## 最慢页')
      ..writeln()
      ..writeln('| 页码 | 实测耗时 |')
      ..writeln('| --- | --- |');
    for (final page in report.slowestPages) {
      buffer.writeln(
        '| ${page.pageIndex + 1} | ${seconds(page.observedSeconds)} |',
      );
    }
  }
  if (report.riskDistribution.isNotEmpty) {
    buffer
      ..writeln()
      ..writeln('## 风险分布')
      ..writeln()
      ..writeln('| 低风险 | 高风险 | 配对数 |')
      ..writeln('| --- | --- | --- |');
    for (final bucket in report.riskDistribution) {
      buffer.writeln('| ${bucket.low} | ${bucket.high} | ${bucket.count} |');
    }
  }
  if (report.pagesNeedingReview.isNotEmpty) {
    buffer
      ..writeln()
      ..writeln('## 建议抽查页（有待处理或高风险配对）')
      ..writeln();
    for (final page in report.pagesNeedingReview) {
      buffer.writeln(
        '- 第 ${page.pageIndex + 1} 页：待处理 ${page.openPairs}，'
        '高风险 ${page.highRiskPairs}，耗时 ${seconds(page.observedSeconds)}',
      );
    }
  }
  return buffer.toString();
}
