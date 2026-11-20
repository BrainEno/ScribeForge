import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/review_history.dart';
import '../../models/review_triage.dart';
import '../../services/review_history_service.dart';

Future<void> showReviewHistory(
  BuildContext context, {
  required ReviewHistoryService service,
  ReviewHistoryFilter filter = const ReviewHistoryFilter(),
}) => Navigator.of(context).push<void>(
  MaterialPageRoute<void>(
    builder: (_) =>
        ReviewHistoryScreen(service: service, initialFilter: filter),
  ),
);

/// A separate route intentionally keeps decision history out of the editor.
class ReviewHistoryScreen extends StatefulWidget {
  const ReviewHistoryScreen({
    super.key,
    required this.service,
    this.initialFilter = const ReviewHistoryFilter(),
  });

  final ReviewHistoryService service;
  final ReviewHistoryFilter initialFilter;

  @override
  State<ReviewHistoryScreen> createState() => _ReviewHistoryScreenState();
}

class _ReviewHistoryScreenState extends State<ReviewHistoryScreen> {
  final _scroll = ScrollController();
  final _records = <ReviewHistoryRecord>[];
  late ReviewHistoryFilter _filter;
  ReviewHistoryBatch? _batch;
  bool _loading = false;
  String? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    _filter = widget.initialFilter;
    unawaited(_load());
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load({bool next = false}) async {
    if (next && (_loading || _batch?.nextBeforeId == null)) return;
    final prior = next ? _batch : null;
    final filter = _filter;
    final version = ++_request;
    setState(() {
      if (!next) {
        _batch = null;
        _records.clear();
      }
      _loading = true;
      _error = null;
    });
    if (!next && _scroll.hasClients) _scroll.jumpTo(0);
    try {
      final found = await widget.service.list(
        filter: filter,
        snapshotId: prior?.snapshotId,
        beforeId: prior?.nextBeforeId,
      );
      if (!mounted || version != _request) return;
      setState(() {
        _batch = found;
        _records.addAll(found.records);
      });
    } on Object catch (error) {
      if (!mounted || version != _request) return;
      setState(() => _error = '$error');
    } finally {
      if (mounted && version == _request) setState(() => _loading = false);
    }
  }

  Future<void> _editFilter() async {
    final result = await showDialog<ReviewHistoryFilter>(
      context: context,
      builder: (_) => _HistoryFilterDialog(initial: _filter),
    );
    if (!mounted || result == null) return;
    _filter = result;
    await _load();
  }

  void _openRecord(ReviewHistoryRecord record) {
    final snapshot = _batch?.snapshotId;
    if (snapshot == null) return;
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => _HistoryRecordScreen(
            preview: record,
            service: widget.service,
            snapshotId: snapshot,
          ),
        ),
      ),
    );
  }

  Widget _header(BuildContext context) {
    final batch = _batch;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          if (batch != null)
            Text(
              batch.projectTitle,
              style: Theme.of(context).textTheme.titleMedium,
            ),
          const Text('只读。时间线包含已被替代或引用旧核验的记录，不代表最终导出内容。'),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: <Widget>[
              OutlinedButton.icon(
                key: const ValueKey('history-filters'),
                onPressed: _editFilter,
                icon: const Icon(Icons.filter_alt_outlined),
                label: const Text('按页组或来源筛选'),
              ),
              if (!_filter.isEmpty)
                InputChip(
                  label: Text(_filter.description),
                  deleteButtonTooltipMessage: '清除历史筛选',
                  onDeleted: () {
                    _filter = const ReviewHistoryFilter();
                    unawaited(_load());
                  },
                ),
            ],
          ),
          if (_records.isNotEmpty)
            Text(
              '已加载 ${_records.length} 条 · 从新到旧',
              style: Theme.of(context).textTheme.bodySmall,
            ),
        ],
      ),
    );
  }

  Widget _footer() {
    final error = _error;
    if (_loading)
      return const Padding(
        padding: EdgeInsets.all(16),
        child: LinearProgressIndicator(),
      );
    if (error != null) {
      return _HistoryError(
        message: error,
        onRetry: () => _load(next: _records.isNotEmpty),
      );
    }
    if (_batch?.nextBeforeId != null) {
      return Padding(
        padding: const EdgeInsets.all(12),
        child: Center(
          child: OutlinedButton.icon(
            key: const ValueKey('history-next-page'),
            onPressed: () => _load(next: true),
            icon: const Icon(Icons.expand_more),
            label: const Text('加载更早的记录'),
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Text(
        _records.isEmpty
            ? (_filter.isEmpty ? '这本书还没有已写入的决策。' : '没有符合筛选的决策。')
            : '已到这份快照的最早记录。',
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('复核决策历史'),
      actions: <Widget>[
        IconButton(
          tooltip: '读取最新决策',
          onPressed: _loading ? null : () => _load(),
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: ListView.builder(
      controller: _scroll,
      itemCount: _records.length + 2,
      itemBuilder: (context, index) {
        if (index == 0) return _header(context);
        if (index > _records.length) return _footer();
        final record = _records[index - 1];
        return Card(
          margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: ListTile(
            key: ValueKey('history-decision-${record.id}'),
            title: Text('#${record.id} · ${record.location}'),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('${record.actorLabel} · ${record.binding.label}'),
                Text(
                  record.text.isEmpty ? '（空文字）' : record.text,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  record.orderLabel,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _openRecord(record),
          ),
        );
      },
    ),
  );
}

class _HistoryFilterDialog extends StatefulWidget {
  const _HistoryFilterDialog({required this.initial});

  final ReviewHistoryFilter initial;

  @override
  State<_HistoryFilterDialog> createState() => _HistoryFilterDialogState();
}

class _HistoryFilterDialogState extends State<_HistoryFilterDialog> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _page;
  late final TextEditingController _pair;
  ReviewHistoryActor? _actor;

  @override
  void initState() {
    super.initState();
    final page = widget.initial.pageIndex;
    final pair = widget.initial.pairIndex;
    _page = TextEditingController(text: page == null ? '' : '${page + 1}');
    _pair = TextEditingController(text: pair == null ? '' : '${pair + 1}');
    _actor = widget.initial.actor;
  }

  @override
  void dispose() {
    _page.dispose();
    _pair.dispose();
    super.dispose();
  }

  String? _validateNumber(String? raw) {
    final value = raw?.trim() ?? '';
    if (value.isEmpty) return null;
    final number = int.tryParse(value);
    return number == null || number <= 0 ? '请输入大于零的整数。' : null;
  }

  void _apply() {
    if (_form.currentState?.validate() != true) return;
    final page = int.tryParse(_page.text.trim());
    final pair = int.tryParse(_pair.text.trim());
    Navigator.of(context).pop(
      ReviewHistoryFilter(
        pageIndex: page == null ? null : page - 1,
        pairIndex: pair == null ? null : pair - 1,
        actor: _actor,
      ),
    );
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    scrollable: true,
    title: const Text('筛选决策历史'),
    content: SizedBox(
      width: 360,
      child: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            TextFormField(
              controller: _page,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '实体页码（可留空）'),
              validator: _validateNumber,
              onFieldSubmitted: (_) => _apply(),
            ),
            const SizedBox(height: 8),
            TextFormField(
              controller: _pair,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '组号（需同时填写页码）'),
              validator: (value) =>
                  (value?.trim().isNotEmpty ?? false) &&
                      _page.text.trim().isEmpty
                  ? '指定组号时也要指定页码。'
                  : _validateNumber(value),
              onFieldSubmitted: (_) => _apply(),
            ),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              initialValue: _actor?.value ?? '',
              isExpanded: true,
              decoration: const InputDecoration(labelText: '接受来源'),
              items: <DropdownMenuItem<String>>[
                const DropdownMenuItem(value: '', child: Text('所有来源')),
                for (final actor in ReviewHistoryActor.values)
                  DropdownMenuItem(
                    value: actor.value,
                    child: Text(actor.label),
                  ),
              ],
              onChanged: (value) => _actor = ReviewHistoryActor.values
                  .where((actor) => actor.value == value)
                  .firstOrNull,
            ),
          ],
        ),
      ),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      FilledButton(onPressed: _apply, child: const Text('应用历史筛选')),
    ],
  );
}

class _HistoryRecordScreen extends StatefulWidget {
  const _HistoryRecordScreen({
    required this.preview,
    required this.service,
    required this.snapshotId,
  });

  final ReviewHistoryRecord preview;
  final ReviewHistoryService service;
  final int snapshotId;

  @override
  State<_HistoryRecordScreen> createState() => _HistoryRecordScreenState();
}

class _HistoryRecordScreenState extends State<_HistoryRecordScreen> {
  ReviewHistoryRecord? _record;
  ReviewHistoryRecord? _previous;
  bool _loading = false;
  bool _loadingPrevious = false;
  String? _error;
  String? _comparisonError;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final entry = await widget.service.detail(
        widget.preview.id,
        snapshotId: widget.snapshotId,
      );
      if (!mounted) return;
      if (entry.pageIndex != widget.preview.pageIndex ||
          entry.pairIndex != widget.preview.pairIndex) {
        throw const FormatException('决策已不在原页组。');
      }
      setState(() => _record = entry);
    } on Object catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _compare() async {
    final record = _record;
    final id = record?.previousDecisionId;
    if (record == null || id == null || _loadingPrevious) return;
    setState(() {
      _loadingPrevious = true;
      _comparisonError = null;
    });
    try {
      final previous = await widget.service.detail(
        id,
        snapshotId: widget.snapshotId,
      );
      if (!mounted) return;
      if (!record.comparableTo(previous)) {
        throw const FormatException(
          '上一条记录引用另一核验批次，或无法确认引用。同一个组号可能代表不同内容，不作版本比较。',
        );
      }
      setState(() => _previous = previous);
    } on Object catch (error) {
      if (mounted) setState(() => _comparisonError = '$error');
    } finally {
      if (mounted) setState(() => _loadingPrevious = false);
    }
  }

  Future<void> _copy(ReviewHistoryRecord record) async {
    var message = '已复制当前决策摘要。';
    try {
      await Clipboard.setData(
        ClipboardData(text: record.describeForClipboard()),
      );
    } on PlatformException {
      message = '剪贴板不可用，请手动选择文字。';
    }
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  Widget _metadata(ReviewHistoryRecord record) => Card(
    child: Padding(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            '${record.location} · ${record.actorLabel}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          Text('记录时间：${record.createdAt}（数据库时间）'),
          Text(record.orderLabel),
          Text(record.binding.label),
          if (record.verificationId != null)
            Text('记录引用核验 #${record.verificationId}'),
          if (record.currentVerificationId != null)
            Text('读取时当前核验 #${record.currentVerificationId}'),
          if (record.riskScore != null)
            Text('接受时记录的风险：${(record.riskScore! * 100).round()}%'),
          if (record.riskReasons.isNotEmpty)
            Text(record.riskReasons.map(reviewReasonLabel).join('、')),
          const SizedBox(height: 8),
          const Text('保存在记录里的证据引用（不是文件完整性校验）：'),
          if (record.citationInvalid) const Text('引用字段无效或损坏。'),
          if (!record.citationInvalid && record.evidenceRefs.isEmpty)
            const Text('没有保存引用。'),
          for (final ref in record.evidenceRefs) SelectableText(ref),
        ],
      ),
    ),
  );

  Widget _textCard(ReviewHistoryRecord record, String title) => Card(
    child: Padding(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(
            '$title #${record.id}',
            style: Theme.of(context).textTheme.titleSmall,
          ),
          if (record.textTruncated) const Text('以下为节选，未显示后续内容。'),
          const SizedBox(height: 8),
          SelectableText(record.text.isEmpty ? '（空文字）' : record.text),
        ],
      ),
    ),
  );

  Widget _texts(ReviewHistoryRecord record) {
    final previous = _previous;
    if (previous == null) return _textCard(record, '记录的文字');
    final older = _textCard(previous, '上一条决策');
    final newer = _textCard(record, '选中的决策');
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < 700) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[older, newer],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(child: older),
            Expanded(child: newer),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final record = _record;
    final preview = record ?? widget.preview;
    return Scaffold(
      appBar: AppBar(
        title: Text('决策 #${preview.id}'),
        actions: <Widget>[
          IconButton(
            tooltip: '复制可见决策摘要',
            onPressed: record == null ? null : () => _copy(record),
            icon: const Icon(Icons.copy_outlined),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(12),
        children: <Widget>[
          _metadata(preview),
          _texts(preview),
          if (_loading || _loadingPrevious) const LinearProgressIndicator(),
          if (_error case final error?)
            _HistoryError(message: error, onRetry: _load),
          if (_comparisonError case final error?)
            Padding(
              padding: const EdgeInsets.all(8),
              child: SelectableText(error),
            ),
          if (record?.previousDecisionId != null && _previous == null)
            Align(
              alignment: Alignment.centerLeft,
              child: OutlinedButton.icon(
                onPressed: _loadingPrevious ? null : _compare,
                icon: const Icon(Icons.compare_arrows),
                label: const Text('查看同一核验批次的上一条决策'),
              ),
            ),
        ],
      ),
    );
  }
}

class _HistoryError extends StatelessWidget {
  const _HistoryError({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SelectableText(
          message,
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
        TextButton.icon(
          onPressed: onRetry,
          icon: const Icon(Icons.refresh),
          label: const Text('重试读取'),
        ),
      ],
    ),
  );
}
