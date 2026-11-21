import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/report_comparison.dart';
import '../../services/desktop_file_picker.dart';
import '../../services/report_comparison_service.dart';

const _filters = <String, String>{
  'changed': '有变化',
  'all': '全部',
  'timing': '计时变化',
  'review': '复核变化',
  'status': '状态或尝试次数变化',
  'coverage': '计时覆盖变化',
};
const _orders = <String, String>{
  'page': '页码',
  'slowdown': '耗时增幅',
  'speedup': '耗时降幅',
  'review': '待复核增幅',
};
const _labels = <String, String>{
  'pages_total': '总页数',
  'ocr_complete_pages': '已完成 OCR',
  'failed_pages': '失败页',
  'running_pages': '正在处理的页',
  'pairs': '配对总数',
  'accepted_automatic': '自动接受',
  'accepted_vlm': 'VLM 接受',
  'accepted_human': '人工接受',
  'open_pairs': '待复核',
  'open_high_risk': '待复核高风险配对',
  'open_blocked': '阻塞配对',
  'aligned_pairs': '已对齐配对',
  'high_risk_pairs': '高风险配对',
  'jobs': '任务数',
  'pending': '待处理',
  'running': '运行中',
  'succeeded': '成功',
  'failed': '失败',
  'attempts': '尝试次数',
  'timed_jobs': '已计时任务',
  'import': '导入',
  'preprocess': '预处理',
  'incomplete': '未完成',
  'primary_ocr': '主 OCR',
  'secondary_ocr': '第二 OCR',
  'align': '对齐',
  'verify': '验证',
  'ocr_complete': 'OCR 已完成',
};

String _label(String key) => _labels[key] ?? key;

String _number(num? value, {bool seconds = false, bool change = false}) {
  if (value == null) return '—';
  final sign = change && value > 0 ? '+' : '';
  if (!seconds) return '$sign$value';
  final text = value != 0 && value.abs() < 0.01
      ? value.toStringAsExponential(2)
      : value.toStringAsFixed(2);
  return '$sign$text 秒';
}

String _transition(String? before, String? after) =>
    '${before == null ? '无记录' : _label(before)} → '
    '${after == null ? '无记录' : _label(after)}';

class ReportComparisonScreen extends StatefulWidget {
  const ReportComparisonScreen({
    super.key,
    required this.service,
    required this.picker,
  });

  final ReportComparisonService service;
  final DesktopFilePicker picker;

  @override
  State<ReportComparisonScreen> createState() => _ReportComparisonScreenState();
}

class _ReportComparisonScreenState extends State<ReportComparisonScreen> {
  String? _baseline;
  String? _candidate;
  ReportComparison? _result;
  String? _error;
  String _filter = 'changed';
  String _order = 'page';
  bool _busy = false;
  bool _allowUnmatched = false;
  bool _copied = false;
  final _pageScroll = ScrollController();

  @override
  void dispose() {
    _pageScroll.dispose();
    super.dispose();
  }

  void _reset() {
    _result = null;
    _error = null;
    _allowUnmatched = false;
    _copied = false;
  }

  Future<void> _pick({required bool baseline}) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final path = await widget.picker.pickFile(
        dialogTitle: baseline ? '选择基准处理报告' : '选择新版处理报告',
        allowedExtensions: const ['json'],
      );
      if (!mounted || path == null || path.isEmpty) return;
      setState(() {
        if (baseline) {
          _baseline = path;
        } else {
          _candidate = path;
        }
        _reset();
      });
    } on Object catch (error) {
      if (mounted) setState(() => _error = '无法选择报告：$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load({
    String? filter,
    String? order,
    int offset = 0,
    bool restart = false,
    bool? allowUnmatched,
  }) async {
    final left = _baseline;
    final right = _candidate;
    if (_busy || left == null || right == null) return;
    final nextFilter = filter ?? _filter;
    final nextOrder = order ?? _order;
    final allow = allowUnmatched ?? (restart ? false : _allowUnmatched);
    final pinned = restart ? null : _result;
    setState(() {
      _busy = true;
      _error = null;
      _copied = false;
    });
    try {
      final result = await widget.service.compare(
        baseline: left,
        candidate: right,
        filter: nextFilter,
        order: nextOrder,
        offset: offset,
        allowUnmatched: allow,
        pinned: pinned,
      );
      if (!mounted) return;
      setState(() {
        _result = result;
        _filter = nextFilter;
        _order = nextOrder;
        _allowUnmatched = allow;
      });
      if (_pageScroll.hasClients) _pageScroll.jumpTo(0);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _error = '读取失败：$error');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _swap() {
    setState(() {
      final previous = _baseline;
      _baseline = _candidate;
      _candidate = previous;
      _reset();
    });
  }

  Future<void> _copy(ReportComparison result) async {
    final buffer = StringBuffer()
      ..writeln('处理报告对比（仅代表保存的快照）')
      ..writeln('基准：${result.baseline.title} · ${result.baseline.digest}')
      ..writeln('新版：${result.candidate.title} · ${result.candidate.digest}')
      ..writeln('相同项目标签不证明来源相同；计时是最近一次尝试的累计时间。')
      ..writeln('— 表示未记录或不可比较。');
    for (final entry in [...result.summary.entries, ...result.review.entries]) {
      final delta = entry.value;
      buffer.writeln(
        '${_label(entry.key)}：${_number(delta.before)} → '
        '${_number(delta.after)}（${_number(delta.change, change: true)}）',
      );
    }
    final seconds = result.seconds;
    buffer.writeln(
      '累计计时：${_number(seconds.before, seconds: true)} → '
      '${_number(seconds.after, seconds: true)}'
      '（${_number(seconds.change, seconds: true, change: true)}）',
    );
    if (result.pageComparison) {
      buffer.writeln(
        '当前结果页：${_filters[result.filter]}，${_orders[result.order]}排序'
        '（本批 ${result.pages.length} 条，共 ${result.matched} 条）',
      );
      for (final page in result.pages) {
        buffer.writeln(
          '第 ${page.index + 1} 页：${_transition(page.oldStatus, page.newStatus)}；'
          '计时差 ${_number(page.seconds.change, seconds: true, change: true)}；'
          '待复核差 ${_number(page.counts["open_pairs"]?.change, change: true)}',
        );
      }
    } else {
      buffer.writeln('项目标签或页数不一致；未按页对比。');
    }
    try {
      await Clipboard.setData(ClipboardData(text: buffer.toString()));
      if (mounted && identical(result, _result)) setState(() => _copied = true);
    } on Object catch (error) {
      if (mounted && identical(result, _result)) {
        setState(() => _error = '复制对比摘要失败：$error');
      }
    }
  }

  Widget _fileButton(String? path, {required bool baseline}) {
    final role = baseline ? '基准' : '新版';
    final name = path == null
        ? null
        : Uri.file(path, windows: Platform.isWindows).pathSegments.last;
    return OutlinedButton.icon(
      key: ValueKey(baseline ? 'choose-baseline' : 'choose-candidate'),
      onPressed: _busy ? null : () => _pick(baseline: baseline),
      icon: const Icon(Icons.folder_open),
      label: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 240),
        child: Tooltip(
          message: path ?? '选择已保存的 JSON 报告',
          child: Text(
            name == null ? '选择$role报告' : '$role：$name',
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    return Scaffold(
      appBar: AppBar(title: const Text('处理报告对比')),
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  _fileButton(_baseline, baseline: true),
                  _fileButton(_candidate, baseline: false),
                  IconButton(
                    onPressed: _busy || _baseline == null || _candidate == null
                        ? null
                        : _swap,
                    tooltip: '交换基准与新版',
                    icon: const Icon(Icons.swap_horiz),
                  ),
                  FilledButton(
                    key: const ValueKey('run-report-comparison'),
                    onPressed: _busy || _baseline == null || _candidate == null
                        ? null
                        : () => _load(restart: true),
                    child: Text(result == null ? '开始对比' : '重新读取文件'),
                  ),
                ],
              ),
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 88),
                  child: SingleChildScrollView(
                    child: SelectableText(
                      '${_error!}${result == null ? '' : '\n下方仍为上次成功的结果。可重新读取文件。'}',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
                ),
              ),
            Expanded(
              child: result == null
                  ? const Center(
                      child: Padding(
                        padding: EdgeInsets.all(20),
                        child: Text('从“处理报告”导出两份 JSON 后，在这里查看变化。对比不会修改报告或项目。'),
                      ),
                    )
                  : DefaultTabController(
                      length: 4,
                      child: Column(
                        children: [
                          TabBar(
                            isScrollable: true,
                            tabs: const [
                              Tab(text: '概览'),
                              Tab(text: '页面'),
                              Tab(text: '阶段'),
                              Tab(text: '风险原因'),
                            ],
                          ),
                          Expanded(
                            child: TabBarView(
                              children: [
                                _overview(result),
                                _pages(result),
                                _stages(result),
                                _reasons(result),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _overview(ReportComparison result) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      const Text(
        '变化量 = 新版 − 基准。相同标题和项目编号不证明来源相同。'
        '耗时是最近一次尝试的累计值，不是整书实际耗时。',
      ),
      const SizedBox(height: 10),
      _ReportIdentityCard(label: '基准', identity: result.baseline),
      _ReportIdentityCard(label: '新版', identity: result.candidate),
      if (result.baseline.digest == result.candidate.digest)
        const Text('两个文件的字节内容相同。'),
      if (!result.sameScope) const Text('计时口径不同或无法识别，不计算耗时差。'),
      if (!result.sameCoverage) const Text('计时覆盖不匹配或未核对；不计算整书耗时差。'),
      if (!result.sameLabels)
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          value: _allowUnmatched,
          onChanged: _busy
              ? null
              : (value) => _load(allowUnmatched: value ?? false),
          title: const Text('仍按页码对比'),
          subtitle: const Text('标题、项目编号或页数不同。同一页码可能不是同一原页，只有确认后才启用。'),
        ),
      Align(
        alignment: Alignment.centerLeft,
        child: OutlinedButton.icon(
          onPressed: _busy ? null : () => _copy(result),
          icon: const Icon(Icons.copy),
          label: Text(_copied ? '已复制' : '复制概览和本批页面摘要'),
        ),
      ),
      const _DeltaLegend(),
      _DeltaRow(label: '累计计时', delta: result.seconds, seconds: true),
      _DeltaGroup(values: result.summary),
      const Divider(),
      _DeltaGroup(values: result.review),
    ],
  );

  Widget _pages(ReportComparison result) {
    if (!result.pageComparison) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text('页码对比未启用。请先在概览核对两份报告，再决定是否按页码比较。'),
        ),
      );
    }
    return ListView.builder(
      controller: _pageScroll,
      padding: const EdgeInsets.all(16),
      itemCount: result.pages.length + 2,
      itemBuilder: (context, index) {
        if (index == 0) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 16,
                runSpacing: 8,
                children: [
                  _option('筛选', _filter, {
                    for (final entry in _filters.entries)
                      entry.key:
                          '${entry.value}（${result.pageCounts[entry.key] ?? 0}）',
                  }, (value) => _load(filter: value)),
                  _option(
                    '排序',
                    _order,
                    _orders,
                    (value) => _load(order: value),
                  ),
                ],
              ),
              if (result.matched == 0)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 20),
                  child: Text('没有符合此筛选的页面。'),
                ),
            ],
          );
        }
        if (index == result.pages.length + 1) {
          if (result.matched == 0) return const SizedBox.shrink();
          return Wrap(
            spacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                '${result.offset + 1}–${result.offset + result.pages.length} / ${result.matched}',
              ),
              OutlinedButton(
                onPressed: _busy || result.offset == 0
                    ? null
                    : () => _load(
                        offset:
                            result.offset - ReportComparisonService.pageSize,
                      ),
                child: const Text('上一批'),
              ),
              OutlinedButton(
                onPressed: _busy || result.nextOffset == null
                    ? null
                    : () => _load(offset: result.nextOffset!),
                child: const Text('下一批'),
              ),
            ],
          );
        }
        final page = result.pages[index - 1];
        return _PageCard(
          key: ValueKey(
            '${result.baseline.digest}:${result.candidate.digest}:${page.index}',
          ),
          page: page,
        );
      },
    );
  }

  Widget _option(
    String label,
    String current,
    Map<String, String> options,
    ValueChanged<String> callback,
  ) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('$label：'),
        DropdownButton<String>(
          value: current,
          onChanged: _busy
              ? null
              : (value) {
                  if (value != null && value != current) callback(value);
                },
          items: [
            for (final option in options.entries)
              DropdownMenuItem(value: option.key, child: Text(option.value)),
          ],
        ),
      ],
    );
  }

  Widget _stages(ReportComparison result) => ListView.builder(
    padding: const EdgeInsets.all(16),
    itemCount: result.stages.length + 1,
    itemBuilder: (context, index) {
      if (index == 0) {
        return result.stages.isEmpty
            ? const Text('两份报告均无阶段记录。')
            : const _DeltaLegend();
      }
      final stage = result.stages[index - 1];
      return Card(
        child: ExpansionTile(
          title: Text(_label(stage.name)),
          subtitle: Text(
            '耗时差：${_number(stage.seconds.change, seconds: true, change: true)}',
          ),
          childrenPadding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
          children: [
            _DeltaRow(label: '累计计时', delta: stage.seconds, seconds: true),
            _DeltaGroup(values: stage.counts),
          ],
        ),
      );
    },
  );

  Widget _reasons(ReportComparison result) {
    final entries = result.reasons.entries.toList()
      ..sort((a, b) {
        final comparison = (b.value.change ?? 0).abs().compareTo(
          (a.value.change ?? 0).abs(),
        );
        return comparison != 0 ? comparison : a.key.compareTo(b.key);
      });
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: entries.length + 1,
      itemBuilder: (context, index) => index == 0
          ? entries.isEmpty
                ? const Text('两份报告均无风险原因记录。')
                : const _DeltaLegend()
          : _DeltaRow(
              label: entries[index - 1].key,
              delta: entries[index - 1].value,
            ),
    );
  }
}

class _ReportIdentityCard extends StatelessWidget {
  const _ReportIdentityCard({required this.label, required this.identity});

  final String label;
  final ReportIdentity identity;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$label：${identity.title}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          Text(
            '项目编号 ${identity.projectId} · 已计时 ${identity.measured} · 未计时 ${identity.unmeasured}',
          ),
          SelectableText('SHA-256：${identity.digest}'),
        ],
      ),
    ),
  );
}

class _PageCard extends StatelessWidget {
  const _PageCard({super.key, required this.page});

  final ReportPageDelta page;

  @override
  Widget build(BuildContext context) => Card(
    child: ExpansionTile(
      title: Text('第 ${page.index + 1} 页'),
      subtitle: Text(
        '${_transition(page.oldStatus, page.newStatus)}\n'
        '耗时差 ${_number(page.seconds.change, seconds: true, change: true)} · '
        '待复核差 ${_number(page.counts['open_pairs']?.change, change: true)}',
      ),
      childrenPadding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
      expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (page.coverageChanged) const Text('页缺失或计时阶段不同，不计算本页累计耗时差。'),
        if (page.categories.isNotEmpty)
          Text(
            page.categories
                .map((category) => _filters[category] ?? category)
                .join('、'),
          ),
        const _DeltaLegend(),
        _DeltaRow(label: '累计计时', delta: page.seconds, seconds: true),
        _DeltaGroup(values: page.counts),
        if (page.reasons.isNotEmpty) ...[
          const Divider(),
          const Text('风险原因'),
          _DeltaGroup(values: page.reasons),
        ],
        if (page.stages.isNotEmpty) const Divider(),
        for (final stage in page.stages) ...[
          Text(
            '${_label(stage.name)}：${_transition(stage.oldState, stage.newState)}',
          ),
          _DeltaRow(label: '耗时', delta: stage.seconds, seconds: true),
          _DeltaRow(label: '尝试次数', delta: stage.attempts),
        ],
      ],
    ),
  );
}

class _DeltaLegend extends StatelessWidget {
  const _DeltaLegend();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: 8),
    child: Text('基准 → 新版（变化量）；“—”表示未记录或不可比较。'),
  );
}

class _DeltaGroup extends StatelessWidget {
  const _DeltaGroup({required this.values});

  final Map<String, ReportDelta> values;

  @override
  Widget build(BuildContext context) => Column(
    children: [
      for (final entry in values.entries)
        _DeltaRow(label: _label(entry.key), delta: entry.value),
    ],
  );
}

class _DeltaRow extends StatelessWidget {
  const _DeltaRow({
    required this.label,
    required this.delta,
    this.seconds = false,
  });

  final String label;
  final ReportDelta delta;
  final bool seconds;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Wrap(
      spacing: 12,
      runSpacing: 2,
      children: [
        Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
        Text(
          '${_number(delta.before, seconds: seconds)} → '
          '${_number(delta.after, seconds: seconds)} '
          '（${_number(delta.change, seconds: seconds, change: true)}）',
        ),
      ],
    ),
  );
}
