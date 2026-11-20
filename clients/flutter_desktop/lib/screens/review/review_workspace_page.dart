import 'package:flutter/material.dart';

import '../../models/review_queue.dart';
import '../../models/review_triage.dart';
import '../../widgets/review_keyboard_shortcuts.dart';
import 'review_evidence_card.dart';
import 'review_triage_dialog.dart';

/// Keeps view-only triage state separate from the project coordinator. The
/// coordinator alone loads evidence and writes explicit human decisions.
class ReviewWorkspacePage extends StatefulWidget {
  const ReviewWorkspacePage({
    super.key,
    required this.storePath,
    required this.projectId,
    required this.queue,
    required this.selectedIndex,
    required this.busy,
    required this.loading,
    required this.includePageFurniture,
    required this.decisionFocusNode,
    required this.controllerFor,
    required this.onRecord,
    required this.onRefresh,
    required this.onSelect,
    required this.onIncludePageFurniture,
  });

  final String? storePath;
  final int? projectId;
  final List<ReviewQueueItem> queue;
  final int selectedIndex;
  final bool busy;
  final bool loading;
  final bool includePageFurniture;
  final FocusNode decisionFocusNode;
  final TextEditingController Function(ReviewQueueItem) controllerFor;
  final Future<void> Function(ReviewQueueItem, String) onRecord;
  final Future<void> Function() onRefresh;
  final ValueChanged<int> onSelect;
  final ValueChanged<bool> onIncludePageFurniture;

  @override
  State<ReviewWorkspacePage> createState() => _ReviewWorkspacePageState();
}

class _ReviewWorkspacePageState extends State<ReviewWorkspacePage> {
  final _jump = TextEditingController();
  final _jumpFocus = FocusNode(debugLabel: 'review-page-jump');
  final _evidenceScroll = ScrollController();
  final _marks = <ReviewItemKey>{};
  var _query = const ReviewTriageQuery();
  String? _jumpError;

  @override
  void didUpdateWidget(covariant ReviewWorkspacePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.storePath != widget.storePath) {
      _query = const ReviewTriageQuery();
      _marks.clear();
      _jump.clear();
      _jumpError = null;
      _scrollToEvidenceStart();
    }
  }

  @override
  void dispose() {
    _jump.dispose();
    _jumpFocus.dispose();
    _evidenceScroll.dispose();
    super.dispose();
  }

  void _scrollToEvidenceStart() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _evidenceScroll.hasClients) _evidenceScroll.jumpTo(0);
    });
  }

  void _select(int index, {bool focus = true}) {
    if (widget.loading ||
        widget.busy ||
        index < 0 ||
        index >= widget.queue.length) {
      return;
    }
    widget.onSelect(index);
    if (_jumpError != null) setState(() => _jumpError = null);
    _scrollToEvidenceStart();
    if (focus) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && widget.decisionFocusNode.canRequestFocus) {
          widget.decisionFocusNode.requestFocus();
        }
      });
    }
  }

  void _move(ReviewTriageResult result, int delta) {
    final index = result.move(widget.selectedIndex, delta);
    if (index != null && index != result.visibleIndex(widget.selectedIndex)) {
      _select(index);
    }
  }

  void _jumpToPage(ReviewTriageResult result, String raw) {
    final number = int.tryParse(raw.trim());
    final index = number == null ? null : result.firstOnPage(number);
    if (index == null) {
      final suffix = _query.filtersItems ? '；可清除筛选后再试。' : '。';
      setState(
        () => _jumpError = number == null || number < 1
            ? '请输入大于零的实体页码。'
            : '当前队列中找不到第 $number 页的待复核配对$suffix',
      );
      return;
    }
    _select(index);
  }

  Future<void> _editFilters(ReviewTriageResult result) async {
    final store = widget.storePath;
    final query = await showReviewTriageDialog(
      context,
      initial: _query,
      reasonCounts: result.reasonCounts,
      markedCount: result.markedCount,
    );
    if (!mounted || query == null || store != widget.storePath) return;
    setState(() {
      _query = query;
      _jumpError = null;
    });
    _scrollToEvidenceStart();
  }

  Future<void> _browse(ReviewTriageResult result) async {
    final store = widget.storePath;
    // The modal owns its snapshot. A background refresh can replace or remove
    // entries while it is open, so reconcile by identity on return.
    final entries = <ReviewQueueItem>[
      for (final index in result.indexes) widget.queue[index],
    ];
    final key = await showReviewQueueBrowser(
      context,
      entries: entries,
      marked: Set<ReviewItemKey>.of(_marks),
    );
    if (!mounted || key == null || store != widget.storePath) return;
    final index = widget.queue.indexWhere((item) => reviewItemKey(item) == key);
    if (index < 0) {
      setState(() => _jumpError = '这条记录已不在当前复核队列中，请重新选择。');
      return;
    }
    _select(index);
  }

  void _clearFilters() {
    setState(() {
      _query = const ReviewTriageQuery();
      _jumpError = null;
    });
    _scrollToEvidenceStart();
  }

  void _toggleMark(ReviewQueueItem item) {
    setState(() {
      final key = reviewItemKey(item);
      if (!_marks.remove(key)) _marks.add(key);
    });
  }

  Widget _heading(ReviewTriageResult result) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      const Text(
        '复核队列',
        style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 8),
      const Text('比较原始候选和图像。只有明确记录后，人工决策才会写入证据链。'),
      const SizedBox(height: 4),
      Text(
        '快捷键：Alt+←/→ 上一项/下一项；Ctrl+J 聚焦决策输入；Ctrl+G 聚焦跳页；Alt+1/2/3 填入候选；Ctrl+Enter 明确记录。',
        style: Theme.of(context).textTheme.bodySmall,
      ),
      const SizedBox(height: 14),
      Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        runSpacing: 6,
        children: <Widget>[
          ElevatedButton.icon(
            icon: Icon(widget.loading ? Icons.hourglass_empty : Icons.refresh),
            label: Text(widget.loading ? '刷新中…' : '刷新队列'),
            onPressed:
                widget.loading ||
                    widget.busy ||
                    widget.storePath == null ||
                    widget.projectId == null
                ? null
                : widget.onRefresh,
          ),
          FilterChip(
            key: const ValueKey('review-include-page-furniture'),
            label: const Text('显示页眉 / 页脚 / 页码'),
            selected: widget.includePageFurniture,
            onSelected: widget.loading || widget.busy
                ? null
                : widget.onIncludePageFurniture,
          ),
          OutlinedButton.icon(
            key: const ValueKey('review-triage-filters'),
            onPressed: widget.storePath == null || widget.loading
                ? null
                : () => _editFilters(result),
            icon: const Icon(Icons.filter_alt_outlined),
            label: const Text('筛选与排序'),
          ),
          if (!_query.isDefault)
            TextButton(onPressed: _clearFilters, child: const Text('清除筛选')),
        ],
      ),
      const SizedBox(height: 8),
      Text(
        _query.filtersItems
            ? '显示 ${result.indexes.length} / ${widget.queue.length} 项 · ${result.matchingPages} 页。未显示的项目仍需复核。'
            : '${widget.queue.length} 项当前队列 · ${result.matchingPages} 页',
        key: const ValueKey('review-triage-count'),
        style: Theme.of(context).textTheme.bodySmall,
      ),
    ],
  );

  Widget _navigation(ReviewTriageResult result, int index) {
    final position = result.indexes.indexOf(index);
    final item = widget.queue[index];
    final marked = _marks.contains(reviewItemKey(item));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Wrap(
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 6,
          runSpacing: 6,
          children: <Widget>[
            Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                IconButton(
                  tooltip: '上一项',
                  onPressed: !widget.busy && position > 0
                      ? () => _move(result, -1)
                      : null,
                  icon: const Icon(Icons.chevron_left),
                ),
                Text('${position + 1} / ${result.indexes.length}'),
                IconButton(
                  tooltip: '下一项',
                  onPressed:
                      !widget.busy && position + 1 < result.indexes.length
                      ? () => _move(result, 1)
                      : null,
                  icon: const Icon(Icons.chevron_right),
                ),
              ],
            ),
            SizedBox(
              width: 115,
              child: TextField(
                key: const ValueKey('review-page-jump'),
                controller: _jump,
                focusNode: _jumpFocus,
                keyboardType: TextInputType.number,
                textInputAction: TextInputAction.go,
                onSubmitted: (raw) => _jumpToPage(result, raw),
                decoration: const InputDecoration(
                  labelText: '跳转页码',
                  isDense: true,
                ),
              ),
            ),
            OutlinedButton(
              onPressed: () => _jumpToPage(result, _jump.text),
              child: const Text('跳转'),
            ),
            IconButton(
              tooltip: '浏览当前队列',
              onPressed: widget.busy ? null : () => _browse(result),
              icon: const Icon(Icons.view_list_outlined),
            ),
            IconButton(
              tooltip: marked ? '取消本次会话标记' : '标记为稍后查看（不记录决策）',
              isSelected: marked,
              selectedIcon: const Icon(Icons.bookmark),
              onPressed: widget.busy ? null : () => _toggleMark(item),
              icon: const Icon(Icons.bookmark_border),
            ),
          ],
        ),
        if (_jumpError case final error?)
          Text(
            error,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final result = _query.apply(widget.queue, _marks);
    final selected = result.visibleIndex(widget.selectedIndex);
    final item = selected == null ? null : widget.queue[selected];
    final status = widget.loading
        ? const LinearProgressIndicator()
        : widget.storePath == null
        ? const Text('请先导入书籍或打开一个项目。')
        : widget.queue.isEmpty
        ? const Text('暂无待处理配对。')
        : item == null
        ? const Text('没有符合条件的配对。请调整筛选；其他项目仍待复核。')
        : null;
    final navigation = selected == null || status != null
        ? null
        : _navigation(result, selected);
    final evidence = item == null || status != null
        ? null
        : ReviewEvidenceCard(
            key: ValueKey(reviewItemKey(item)),
            item: item,
            busy: widget.busy,
            projectId: widget.projectId,
            controller: widget.controllerFor(item),
            decisionFocusNode: widget.decisionFocusNode,
            onRecord: widget.onRecord,
          );
    final header = <Widget>[
      _heading(result),
      const SizedBox(height: 12),
      if (status != null) status,
      if (navigation != null) navigation,
    ];
    return ReviewKeyboardShortcuts(
      onPreviousPage: () => _move(result, -1),
      onNextPage: () => _move(result, 1),
      onNextDifference: () => _move(result, 1),
      onFocusDecisionInput: widget.decisionFocusNode.requestFocus,
      onFocusPageJump: _jumpFocus.requestFocus,
      child: LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxHeight < 550 || constraints.maxWidth < 530) {
            return SingleChildScrollView(
              key: const PageStorageKey('compact-review-scroll'),
              controller: _evidenceScroll,
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[...header, if (evidence != null) evidence],
              ),
            );
          }
          return Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                ...header,
                if (evidence != null) ...<Widget>[
                  const SizedBox(height: 8),
                  Expanded(
                    child: SingleChildScrollView(
                      key: const PageStorageKey('review-evidence-scroll'),
                      controller: _evidenceScroll,
                      child: evidence,
                    ),
                  ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}
