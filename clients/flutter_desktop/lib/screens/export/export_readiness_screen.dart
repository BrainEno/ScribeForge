import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/export_readiness.dart';
import '../../services/export_readiness_service.dart';
import '../evidence/ocr_evidence_list.dart';

enum ExportReadinessDestination {
  processing,
  review,
  evidence,
  history,
  textless,
  safety,
}

class ExportReadinessLink {
  const ExportReadinessLink(this.destination, {this.page, this.pair});

  final ExportReadinessDestination destination;
  final int? page;
  final int? pair;
}

class ExportReadinessScreen extends StatefulWidget {
  const ExportReadinessScreen({
    super.key,
    required this.service,
    required this.options,
    this.sourceMatches = true,
  });

  final ExportReadinessService service;
  final ExportReadinessOptions options;
  final bool sourceMatches;

  @override
  State<ExportReadinessScreen> createState() => _ExportReadinessScreenState();
}

class _ExportReadinessScreenState extends State<ExportReadinessScreen> {
  ExportReadinessReport? _report;
  late ExportReadinessOptions _options;
  ExportIssueKind? _kind;
  bool _loading = false;
  String? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    _options = widget.options;
    unawaited(_check());
  }

  Future<void> _check({ExportReadinessOptions? options}) async {
    final requested = options ?? _options;
    final request = ++_request;
    setState(() {
      _options = requested;
      _loading = true;
      _error = null;
      _report = null;
      if (options != null) _kind = null;
    });
    try {
      final report = await widget.service.check(requested);
      if (!mounted || request != _request) return;
      setState(() => _report = report);
    } on Object catch (error) {
      if (mounted && request == _request) setState(() => _error = '$error');
    } finally {
      if (mounted && request == _request) setState(() => _loading = false);
    }
  }

  Future<void> _filterPage() async {
    final page = await showDialog<int>(
      context: context,
      builder: (_) => _PageFilter(page: _options.page),
    );
    if (!mounted || page == null) return;
    await _check(options: _options.forPage(page));
  }

  Future<void> _copy() async {
    final report = _report;
    if (report == null) return;
    var message = '已复制当前检查摘要。';
    try {
      await Clipboard.setData(ClipboardData(text: report.describe()));
    } on PlatformException {
      message = '剪贴板不可用。';
    }
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  Widget _header() {
    final report = _report;
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (report != null)
          Text(report.title, style: theme.textTheme.titleLarge),
        const Text('只检查当时的项目内容，不写出文件。正式导出会重新检查；路径、格式和写入错误不在本检查范围内。'),
        if (!widget.sourceMatches)
          Text(
            '当前来源文件未通过工作台检查。即使内容检查通过，也不能代替重新关联来源。',
            style: TextStyle(color: theme.colorScheme.error),
          ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: <Widget>[
            Chip(label: Text('严格校验：${_options.strict ? '开启' : '关闭'}')),
            Chip(
              label: Text(_options.includePageFurniture ? '保留页边文字' : '不保留页边文字'),
            ),
            OutlinedButton.icon(
              key: const ValueKey('export-readiness-page-filter'),
              onPressed: _loading ? null : _filterPage,
              icon: const Icon(Icons.filter_alt_outlined),
              label: const Text('查看指定页的问题'),
            ),
            if (_options.page case final page?)
              InputChip(
                label: Text('第 ${page + 1} 页及项目级问题'),
                deleteButtonTooltipMessage: '清除物理页筛选',
                onDeleted: _loading
                    ? null
                    : () => _check(options: _options.forPage(null)),
              ),
          ],
        ),
        const SizedBox(height: 8),
        if (_loading) const LinearProgressIndicator(),
        if (_error case final error?)
          OcrEvidenceError(message: error, onRetry: _check),
        if (report != null) ...<Widget>[
          _ReadinessVerdict(report: report),
          if (report.summary case final summary?)
            _SummaryMetrics(summary: summary),
          if (report.totalIssues > 0) _typeFilters(report),
        ],
      ],
    );
  }

  Widget _typeFilters(ExportReadinessReport report) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      const SizedBox(height: 8),
      const Text('按类型筛选当前明细；下方计数为全书总数。'),
      Wrap(
        spacing: 6,
        runSpacing: 4,
        children: <Widget>[
          ChoiceChip(
            label: const Text('全部类型'),
            selected: _kind == null,
            onSelected: (_) => setState(() => _kind = null),
          ),
          for (final entry in report.counts.entries)
            ChoiceChip(
              label: Text('${entry.key.label} ${entry.value}'),
              selected: _kind == entry.key,
              onSelected: (selected) =>
                  setState(() => _kind = selected ? entry.key : null),
            ),
        ],
      ),
    ],
  );

  Widget _footer(ExportReadinessReport report, bool hasIssues) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 10),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (!hasIssues && report.totalIssues > 0)
          const Text('当前明细范围没有这类问题；请清除筛选或改查指定物理页。'),
        if (report.truncated)
          Text(
            '此范围共 ${report.matchingCount} 项，当前只显示前 ${report.issues.length} 项。可指定物理页缩小范围。',
          ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final report = _report;
    final items =
        report?.issues
            .where((issue) => _kind == null || issue.kind == _kind)
            .toList(growable: false) ??
        const <ExportReadinessIssue>[];
    return Scaffold(
      appBar: AppBar(
        title: const Text('导出前检查'),
        actions: <Widget>[
          IconButton(
            tooltip: '复制检查摘要',
            onPressed: report == null ? null : _copy,
            icon: const Icon(Icons.copy_outlined),
          ),
          IconButton(
            tooltip: '重新检查内容',
            onPressed: _loading ? null : _check,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: items.length + (report == null ? 1 : 2),
        itemBuilder: (context, index) {
          if (index == 0) return _header();
          if (index > items.length) return _footer(report!, items.isNotEmpty);
          final issue = items[index - 1];
          return _IssueCard(
            issue: issue,
            onAction: (link) => Navigator.of(context).pop(link),
          );
        },
      ),
    );
  }
}

class _ReadinessVerdict extends StatelessWidget {
  const _ReadinessVerdict({required this.report});

  final ExportReadinessReport report;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Card(
      color: report.ready ? colors.secondaryContainer : colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(report.ready ? Icons.task_alt : Icons.report_problem_outlined),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    report.ready
                        ? '本次内容检查未发现阻塞'
                        : '全书有 ${report.blockingCount} 项内容阻塞',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (report.options.page != null)
                    const Text('指定页筛选只改变问题明细，不改变全书结果。'),
                  if (report.summary == null)
                    const Text('尚未组装完整内容，不能判定其余内容是否需要抽查。'),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SummaryMetrics extends StatelessWidget {
  const _SummaryMetrics({required this.summary});

  final ExportContentSummary summary;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: Wrap(
      spacing: 6,
      runSpacing: 4,
      children: <Widget>[
        Chip(label: Text('组装 ${summary.pages} 页')),
        Chip(label: Text('${summary.chapters} 个章节 · ${summary.images} 张嵌入图')),
        Chip(
          label: Text(
            '人工接受 ${summary.humanAccepted} · 机器接受 ${summary.machineAccepted}',
          ),
        ),
        Chip(label: Text('机器最高风险 ${_percentage(summary.maxMachineRisk)}')),
        Chip(label: Text('已确认无文字 ${summary.textlessPages} 页')),
        Chip(label: Text('省略页边配对 ${summary.omittedFurniture} 组')),
      ],
    ),
  );
}

class _IssueCard extends StatelessWidget {
  const _IssueCard({required this.issue, required this.onAction});

  final ExportReadinessIssue issue;
  final ValueChanged<ExportReadinessLink> onAction;

  Widget _button(
    String label,
    IconData icon,
    ExportReadinessDestination destination,
  ) => TextButton.icon(
    icon: Icon(icon),
    label: Text(label),
    onPressed: () => onAction(
      ExportReadinessLink(destination, page: issue.page, pair: issue.pair),
    ),
  );

  Iterable<Widget> _actions() sync* {
    switch (issue.kind) {
      case ExportIssueKind.unresolvedPair:
        yield _button(
          '前往复核',
          Icons.fact_check_outlined,
          ExportReadinessDestination.review,
        );
      case ExportIssueKind.unverifiedPage:
      case ExportIssueKind.noPages:
        yield _button(
          '前往处理',
          Icons.auto_awesome,
          ExportReadinessDestination.processing,
        );
      case ExportIssueKind.spotCheck:
        yield _button(
          '查看决策历史',
          Icons.history,
          ExportReadinessDestination.history,
        );
      case ExportIssueKind.pageImage:
      case ExportIssueKind.evidenceError:
        yield _button(
          '前往项目安全',
          Icons.shield_outlined,
          ExportReadinessDestination.safety,
        );
      case ExportIssueKind.unconfirmedPage:
        yield _button(
          '核对原页并确认',
          Icons.draw_outlined,
          ExportReadinessDestination.textless,
        );
        yield _button(
          '前往处理',
          Icons.auto_awesome,
          ExportReadinessDestination.processing,
        );
    }
    if (issue.page != null) {
      yield _button(
        '查看这页的 OCR 证据',
        Icons.manage_search,
        ExportReadinessDestination.evidence,
      );
    }
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(issue.synopsis, style: Theme.of(context).textTheme.titleMedium),
          Text(issue.kind.advice),
          if (issue.reasons.isNotEmpty) Text('原因：${issue.reasonDescription}'),
          if (issue.characters.isNotEmpty)
            SelectableText('涉及字符：${issue.characters.join('、')}'),
          if (issue.actorLabel case final label?) Text('已记录来源：$label'),
          if (issue.score case final score?) Text('配对风险：${_percentage(score)}'),
          if (issue.kind == ExportIssueKind.unconfirmedPage)
            Text(
              issue.ink == null
                  ? '没有墨迹测量；不能据此判定空白。'
                  : '保存的墨迹比例：${_percentage(issue.ink!)}（不等于无文字确认）',
            ),
          if (issue.detail.isNotEmpty) SelectableText(issue.detail),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: _actions().toList(growable: false),
          ),
        ],
      ),
    ),
  );
}

class _PageFilter extends StatefulWidget {
  const _PageFilter({required this.page});

  final int? page;

  @override
  State<_PageFilter> createState() => _PageFilterState();
}

class _PageFilterState extends State<_PageFilter> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _input;

  @override
  void initState() {
    super.initState();
    final page = widget.page;
    _input = TextEditingController(text: page == null ? '' : '${page + 1}');
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  int? _parse(String? value) => int.tryParse(value?.trim() ?? '');

  void _submit() {
    if (_form.currentState?.validate() != true) return;
    Navigator.of(context).pop(_parse(_input.text)! - 1);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    scrollable: true,
    title: const Text('按物理页查看问题'),
    content: Form(
      key: _form,
      child: TextFormField(
        controller: _input,
        autofocus: true,
        keyboardType: TextInputType.number,
        decoration: const InputDecoration(labelText: '物理页码（从 1 开始）'),
        validator: (value) => (_parse(value) ?? 0) > 0 ? null : '请输入大于零的整数。',
        onFieldSubmitted: (_) => _submit(),
      ),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      FilledButton(onPressed: _submit, child: const Text('检查该页明细')),
    ],
  );
}

String _percentage(double value) => '${(value * 100).toStringAsFixed(1)}%';
