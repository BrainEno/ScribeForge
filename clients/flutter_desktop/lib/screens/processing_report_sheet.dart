import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/processing_report.dart';
import '../services/processing_report_loader.dart';

/// Bottom sheet that renders the schema-v1 processing report for the current
/// project (issue #219): total measured time, bottleneck stage, per-engine
/// timings, slowest pages, risk distribution, review rate, and the pages that
/// deserve a human look. Unmeasured values are shown as N/A, never as 0.
class ProcessingReportSheet extends StatefulWidget {
  const ProcessingReportSheet({
    super.key,
    required this.executable,
    required this.storePath,
    this.projectId,
    this.loader,
    this.processing = false,
    this.exportHandler,
  });

  final String executable;
  final String storePath;
  final int? projectId;

  /// Injectable for tests; defaults to a real CLI-backed loader.
  final ProcessingReportLoader? loader;

  /// True while a `process` run is active: the sheet must not present stale
  /// data as if it belonged to the in-flight run.
  final bool processing;

  /// Writes the Markdown (or JSON) export of [report] into a user-chosen
  /// directory and returns the written file path. Null disables the buttons.
  final Future<String?> Function(
    ProcessingReportSnapshot report, {
    required bool markdown,
  })? exportHandler;

  @override
  State<ProcessingReportSheet> createState() => _ProcessingReportSheetState();
}

class _ProcessingReportSheetState extends State<ProcessingReportSheet> {
  ProcessingReportSnapshot? _report;
  String? _error;
  bool _loading = false;
  bool _onlyReviewPages = false;
  String? _notice;

  @override
  void initState() {
    super.initState();
    // Never show stale data while a run is in flight: load on open only when
    // the pipeline is idle, otherwise wait for the user to refresh.
    if (!widget.processing) _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
      _notice = null;
    });
    final loader =
        widget.loader ?? const ProcessingReportLoader();
    try {
      final report = await loader.load(
        executable: widget.executable,
        storePath: widget.storePath,
        projectId: widget.projectId,
      );
      if (!mounted) return;
      setState(() {
        _report = report;
        _loading = false;
      });
    } on ProcessingReportLoadException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '读取处理报告时出错：$error';
        _loading = false;
      });
    }
  }

  Future<void> _copy(String label, String text) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    setState(() => _notice = '已复制$label到剪贴板。');
  }

  Future<void> _export({required bool markdown}) async {
    final handler = widget.exportHandler;
    final report = _report;
    if (handler == null || report == null) return;
    setState(() => _notice = null);
    final path = await handler(report, markdown: markdown);
    if (!mounted) return;
    setState(() {
      _notice = path == null ? '导出已取消。' : '已导出到：$path';
    });
  }

  static String _seconds(double? value) =>
      value == null ? 'N/A（未测量）' : '${value.toStringAsFixed(1)} 秒';

  static String _percent(double? value) =>
      value == null ? 'N/A' : '${(value * 100).toStringAsFixed(1)}%';

  @override
  Widget build(BuildContext context) {
    final report = _report;
    return DraggableScrollableSheet(
      initialChildSize: 0.85,
      maxChildSize: 0.95,
      expand: false,
      builder: (context, controller) => Material(
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 16, 12, 8),
                child: Row(
                  children: <Widget>[
                    const Icon(Icons.assessment_outlined),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '处理报告${report != null && report.title.isNotEmpty ? '：${report.title}' : ''}',
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    IconButton(
                      key: const ValueKey('refresh-report-button'),
                      onPressed: _loading || widget.processing
                          ? null
                          : _refresh,
                      icon: const Icon(Icons.refresh),
                      tooltip: '重新读取报告',
                    ),
                    IconButton(
                      key: const ValueKey('close-report-sheet-button'),
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
              ),
              if (widget.processing)
                const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 20),
                  child: Text(
                    '处理正在进行中；为避免展示过期数据，报告将在处理完成后刷新。',
                    style: TextStyle(color: Colors.orange, fontSize: 12),
                  ),
                ),
              if (_notice != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
                  child: Text(
                    _notice!,
                    style: TextStyle(
                      color: Colors.green.shade700,
                      fontSize: 12,
                    ),
                  ),
                ),
              _buildActionRow(report),
              const Divider(height: 1),
              Expanded(
                child: _loading && report == null
                    ? const Center(child: CircularProgressIndicator())
                    : _error != null && report == null
                    ? _buildError(controller)
                    : report == null
                    ? _buildEmpty(controller)
                    : _buildReport(controller, report),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildActionRow(ProcessingReportSnapshot? report) {
    final canCopy = report != null && !_loading;
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 8),
      child: Wrap(
        spacing: 8,
        runSpacing: 4,
        children: <Widget>[
          OutlinedButton.icon(
            key: const ValueKey('copy-report-json-button'),
            onPressed: canCopy
                ? () => _copy(' JSON 报告', report.rawJson)
                : null,
            icon: const Icon(Icons.code, size: 16),
            label: const Text('复制 JSON'),
          ),
          OutlinedButton.icon(
            key: const ValueKey('copy-report-markdown-button'),
            onPressed: canCopy
                ? () => _copy(
                    ' Markdown 摘要',
                    processingReportMarkdown(report),
                  )
                : null,
            icon: const Icon(Icons.article_outlined, size: 16),
            label: const Text('复制 Markdown'),
          ),
          if (widget.exportHandler != null) ...<Widget>[
            OutlinedButton.icon(
              key: const ValueKey('export-report-markdown-button'),
              onPressed: canCopy
                  ? () => _export(markdown: true)
                  : null,
              icon: const Icon(Icons.save_alt, size: 16),
              label: const Text('导出 Markdown'),
            ),
            OutlinedButton.icon(
              key: const ValueKey('export-report-json-button'),
              onPressed: canCopy ? () => _export(markdown: false) : null,
              icon: const Icon(Icons.save_alt_outlined, size: 16),
              label: const Text('导出 JSON'),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildError(ScrollController controller) => ListView(
    controller: controller,
    padding: const EdgeInsets.all(20),
    children: <Widget>[
      Icon(Icons.error_outline, color: Colors.red.shade400, size: 40),
      const SizedBox(height: 8),
      Text(_error!, style: const TextStyle(fontSize: 13)),
      const SizedBox(height: 12),
      FilledButton.icon(
        key: const ValueKey('retry-report-button'),
        onPressed: _loading ? null : _refresh,
        icon: const Icon(Icons.refresh),
        label: const Text('重试'),
      ),
    ],
  );

  Widget _buildEmpty(ScrollController controller) => ListView(
    controller: controller,
    padding: const EdgeInsets.all(20),
    children: const <Widget>[
      Text(
        '还没有可查看的报告。运行一次处理（即使部分失败）后，这里会显示 '
        '耗时、瓶颈、最慢页与风险分布。',
        style: TextStyle(fontSize: 13),
      ),
    ],
  );

  Widget _buildReport(
    ScrollController controller,
    ProcessingReportSnapshot report,
  ) {
    final pages = _onlyReviewPages ? report.pagesNeedingReview : report.pages;
    return ListView(
      controller: controller,
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        _buildSummaryCards(report),
        const SizedBox(height: 16),
        _buildEngineSection(report),
        const SizedBox(height: 16),
        _buildSlowestPages(report),
        const SizedBox(height: 16),
        _buildRiskSection(report),
        const SizedBox(height: 16),
        _buildReviewSection(report),
        const SizedBox(height: 16),
        Row(
          children: <Widget>[
            const Expanded(
              child: Text(
                '页面明细',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            ToggleButtons(
              isSelected: [_onlyReviewPages],
              onPressed: (index) =>
                  setState(() => _onlyReviewPages = !_onlyReviewPages),
              children: const <Widget>[Text('只看需抽查页')],
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (pages.isEmpty)
          const Text('没有需要抽查的页。', style: TextStyle(fontSize: 12))
        else
          _buildPagesTable(pages),
      ],
    );
  }

  Widget _buildSummaryCards(ProcessingReportSnapshot report) => Wrap(
    spacing: 12,
    runSpacing: 12,
    children: <Widget>[
      _summaryCard(
        '实测总耗时',
        _seconds(report.timing.observedSeconds),
        subtitle:
            '已计时任务 ${report.timing.timedJobs}，未计时 ${report.timing.unavailableJobs}',
      ),
      _summaryCard('瓶颈阶段', report.timing.bottleneckStage ?? 'N/A（无已计时任务）'),
      _summaryCard(
        '页数',
        '${report.pagesTotal} 页',
        subtitle:
            'OCR 完成 ${report.ocrCompletePages} · 失败 ${report.failedPages} · 进行中 ${report.runningPages}',
      ),
      _summaryCard(
        '复核率',
        _percent(report.review.reviewRequiredRatio),
        subtitle: '待处理 ${report.review.openPairs} · 高风险 ${report.review.openHighRisk}',
      ),
      _summaryCard('高风险配对', '${report.highRiskPairsTotal}'),
    ],
  );

  Widget _summaryCard(String label, String value, {String? subtitle}) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: SizedBox(
        width: 170,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              label,
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
            ),
            const SizedBox(height: 4),
            Text(
              value,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            if (subtitle != null) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                subtitle,
                style: TextStyle(color: Colors.grey.shade600, fontSize: 11),
              ),
            ],
          ],
        ),
      ),
    ),
  );

  Widget _buildEngineSection(ProcessingReportSnapshot report) {
    final engines = report.timing.engines;
    if (engines.isEmpty) return const SizedBox.shrink();
    final maxSeconds = engines
        .map((engine) => engine.observedSeconds ?? 0)
        .fold<double>(0, (a, b) => a > b ? a : b);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text('引擎耗时', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        for (final engine in engines) ...<Widget>[
          Text(
            '${engine.engine}（${engine.stage}）：${_seconds(engine.observedSeconds)}，'
            '占比 ${_percent(engine.share)}',
            style: const TextStyle(fontSize: 12),
          ),
          const SizedBox(height: 4),
          LinearProgressIndicator(
            value: maxSeconds > 0
                ? ((engine.observedSeconds ?? 0) / maxSeconds).clamp(0.0, 1.0)
                : 0,
          ),
          const SizedBox(height: 8),
        ],
      ],
    );
  }

  Widget _buildSlowestPages(ProcessingReportSnapshot report) {
    if (report.slowestPages.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text('最慢页', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        for (final page in report.slowestPages)
          Text(
            '第 ${page.pageIndex + 1} 页：${_seconds(page.observedSeconds)}',
            style: const TextStyle(fontSize: 12),
          ),
      ],
    );
  }

  Widget _buildRiskSection(ProcessingReportSnapshot report) {
    if (report.riskDistribution.isEmpty && report.riskReasons.isEmpty) {
      return const SizedBox.shrink();
    }
    final maxCount = report.riskDistribution
        .map((bucket) => bucket.count)
        .fold<int>(0, (a, b) => a > b ? a : b);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text('风险分布', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        for (final bucket in report.riskDistribution) ...<Widget>[
          Text(
            '低风险 ${bucket.low} / 高风险 ${bucket.high}：${bucket.count} 个配对',
            style: const TextStyle(fontSize: 12),
          ),
          const SizedBox(height: 2),
          LinearProgressIndicator(
            value: maxCount > 0 ? bucket.count / maxCount : 0,
            backgroundColor: Colors.red.shade50,
            color: Colors.red.shade300,
          ),
          const SizedBox(height: 6),
        ],
        if (report.riskReasons.isNotEmpty)
          Text(
            '风险原因：${report.riskReasons.entries.map((entry) => '${entry.key} ×${entry.value}').join('，')}',
            style: TextStyle(color: Colors.grey.shade700, fontSize: 11),
          ),
      ],
    );
  }

  Widget _buildReviewSection(ProcessingReportSnapshot report) {
    final review = report.review;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text('复核', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Text(
          '配对总数 ${review.pairs}：机器自动采纳 ${review.acceptedAutomatic}，'
          'VLM 采纳 ${review.acceptedVlm}，人工采纳 ${review.acceptedHuman}，'
          '待处理 ${review.openPairs}（高风险 ${review.openHighRisk}，'
          '阻塞 ${review.openBlocked}）。',
          style: const TextStyle(fontSize: 12),
        ),
      ],
    );
  }

  Widget _buildPagesTable(List<PageReportEntry> pages) => Table(
    columnWidths: const <int, TableColumnWidth>{
      0: FixedColumnWidth(64),
      1: FlexColumnWidth(),
      2: FixedColumnWidth(80),
      3: FixedColumnWidth(80),
      4: FixedColumnWidth(80),
      5: FixedColumnWidth(110),
    },
    border: TableBorder.all(color: Colors.grey.shade300, width: 0.5),
    children: <TableRow>[
      const TableRow(
        children: <Widget>[
          _PageCell.header('页码'),
          _PageCell.header('状态'),
          _PageCell.header('待处理'),
          _PageCell.header('高风险'),
          _PageCell.header('耗时'),
          _PageCell.header('风险原因'),
        ],
      ),
      for (final page in pages)
        TableRow(
          children: <Widget>[
            _PageCell('${page.pageIndex + 1}'),
            _PageCell(page.status),
            _PageCell('${page.openPairs}'),
            _PageCell('${page.highRiskPairs}'),
            _PageCell(_seconds(page.observedSeconds)),
            _PageCell(
              page.riskReasons.entries
                  .map((entry) => '${entry.key}×${entry.value}')
                  .join(', '),
            ),
          ],
        ),
    ],
  );
}

class _PageCell extends StatelessWidget {
  const _PageCell(this.text, {this.header = false});

  factory _PageCell.header(String text) => _PageCell(text, header: true);

  final String text;
  final bool header;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
    child: Text(
      text,
      style: TextStyle(
        fontSize: 11,
        fontWeight: header ? FontWeight.w600 : FontWeight.normal,
      ),
    ),
  );
}
