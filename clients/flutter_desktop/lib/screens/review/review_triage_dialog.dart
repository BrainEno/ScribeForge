import 'package:flutter/material.dart';

import '../../models/review_queue.dart';
import '../../models/review_triage.dart';

Future<ReviewTriageQuery?> showReviewTriageDialog(
  BuildContext context, {
  required ReviewTriageQuery initial,
  required Map<String, int> reasonCounts,
  required int markedCount,
}) => showDialog<ReviewTriageQuery>(
  context: context,
  builder: (_) => _TriageDialog(
    initial: initial,
    reasonCounts: reasonCounts,
    markedCount: markedCount,
  ),
);

class _TriageDialog extends StatefulWidget {
  const _TriageDialog({
    required this.initial,
    required this.reasonCounts,
    required this.markedCount,
  });

  final ReviewTriageQuery initial;
  final Map<String, int> reasonCounts;
  final int markedCount;

  @override
  State<_TriageDialog> createState() => _TriageDialogState();
}

class _TriageDialogState extends State<_TriageDialog> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _search;
  late final TextEditingController _pages;
  late ReviewOrder _order;
  late ReviewEvidenceFilter _evidence;
  late String? _reason;
  late bool _markedOnly;
  late int _risk;

  @override
  void initState() {
    super.initState();
    final query = widget.initial;
    _search = TextEditingController(text: query.text);
    final range = query.pages;
    _pages = TextEditingController(
      text: range == null
          ? ''
          : range.first == range.last
          ? '${range.first}'
          : '${range.first ?? ''}–${range.last ?? ''}',
    );
    _order = query.order;
    _evidence = query.evidence;
    _reason = query.reason;
    _markedOnly = query.markedOnly;
    _risk = (query.minimumRisk * 100).round().clamp(0, 100).toInt();
  }

  @override
  void dispose() {
    _search.dispose();
    _pages.dispose();
    super.dispose();
  }

  void _apply() {
    if (_form.currentState?.validate() != true) return;
    Navigator.of(context).pop(
      ReviewTriageQuery(
        text: _search.text.trim(),
        pages: ReviewPageRange.parse(_pages.text),
        reason: _reason,
        minimumRisk: _risk / 100,
        evidence: _evidence,
        markedOnly: _markedOnly,
        order: _order,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final reasons = <String>{
      ...widget.reasonCounts.keys,
      if (_reason != null) _reason!,
    }.toList()..sort();
    return AlertDialog(
      scrollable: true,
      title: const Text('筛选与排序'),
      content: SizedBox(
        width: 440,
        child: Form(
          key: _form,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Text('这些操作只改变显示，不会确认、删除或改写任何候选。'),
              const SizedBox(height: 14),
              TextFormField(
                controller: _search,
                decoration: const InputDecoration(
                  labelText: '搜索候选原文',
                  prefixIcon: Icon(Icons.search),
                ),
                onFieldSubmitted: (_) => _apply(),
              ),
              const SizedBox(height: 10),
              TextFormField(
                controller: _pages,
                decoration: const InputDecoration(
                  labelText: '实体页范围',
                  hintText: '例如 12、12–30 或 12–',
                ),
                validator: (input) {
                  try {
                    ReviewPageRange.parse(input ?? '');
                    return null;
                  } on FormatException catch (error) {
                    return error.message;
                  }
                },
                onFieldSubmitted: (_) => _apply(),
              ),
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                initialValue: _reason ?? '',
                isExpanded: true,
                decoration: const InputDecoration(labelText: '风险原因'),
                items: <DropdownMenuItem<String>>[
                  const DropdownMenuItem(value: '', child: Text('所有原因')),
                  for (final reason in reasons)
                    DropdownMenuItem(
                      value: reason,
                      child: Text(
                        '${reviewReasonLabel(reason)} (${widget.reasonCounts[reason] ?? 0})',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: (reason) => setState(
                  () => _reason = reason?.isEmpty == true ? null : reason,
                ),
              ),
              const SizedBox(height: 10),
              DropdownButtonFormField<ReviewEvidenceFilter>(
                initialValue: _evidence,
                isExpanded: true,
                decoration: const InputDecoration(labelText: '证据特征'),
                items: const <DropdownMenuItem<ReviewEvidenceFilter>>[
                  DropdownMenuItem(
                    value: ReviewEvidenceFilter.any,
                    child: Text('不限'),
                  ),
                  DropdownMenuItem(
                    value: ReviewEvidenceFilter.crop,
                    child: Text('有候选区域裁切'),
                  ),
                  DropdownMenuItem(
                    value: ReviewEvidenceFilter.ambiguous,
                    child: Text('对齐歧义'),
                  ),
                  DropdownMenuItem(
                    value: ReviewEvidenceFilter.missingCandidate,
                    child: Text('至少一台 OCR 缺少候选'),
                  ),
                ],
                onChanged: (value) => setState(
                  () => _evidence = value ?? ReviewEvidenceFilter.any,
                ),
              ),
              const SizedBox(height: 10),
              DropdownButtonFormField<ReviewOrder>(
                initialValue: _order,
                isExpanded: true,
                decoration: const InputDecoration(labelText: '显示顺序'),
                items: const <DropdownMenuItem<ReviewOrder>>[
                  DropdownMenuItem(
                    value: ReviewOrder.queue,
                    child: Text('保持队列顺序'),
                  ),
                  DropdownMenuItem(
                    value: ReviewOrder.highestRisk,
                    child: Text('风险从高到低'),
                  ),
                  DropdownMenuItem(
                    value: ReviewOrder.physicalPage,
                    child: Text('从实体页第一页开始'),
                  ),
                ],
                onChanged: (value) =>
                    setState(() => _order = value ?? ReviewOrder.queue),
              ),
              const SizedBox(height: 10),
              Text('最低风险：$_risk%'),
              Slider(
                value: _risk.toDouble(),
                max: 100,
                divisions: 20,
                label: '$_risk%',
                onChanged: (value) => setState(() => _risk = value.round()),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: Text('仅看本次标记（${widget.markedCount} 项）'),
                subtitle: const Text('标记只在当前会话和项目内有效。'),
                value: _markedOnly,
                onChanged: (value) =>
                    setState(() => _markedOnly = value ?? false),
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
        TextButton(
          onPressed: () => Navigator.of(context).pop(const ReviewTriageQuery()),
          child: const Text('恢复默认'),
        ),
        FilledButton(onPressed: _apply, child: const Text('应用筛选')),
      ],
    );
  }
}

Future<ReviewItemKey?> showReviewQueueBrowser(
  BuildContext context, {
  required List<ReviewQueueItem> entries,
  required Set<ReviewItemKey> marked,
}) => showDialog<ReviewItemKey>(
  context: context,
  builder: (context) => AlertDialog(
    title: Text('浏览当前队列（${entries.length} 项）'),
    content: SizedBox(
      width: 550,
      height: (MediaQuery.sizeOf(context).height * 0.45)
          .clamp(140, 475)
          .toDouble(),
      child: ListView.separated(
        itemCount: entries.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final item = entries[index];
          final key = reviewItemKey(item);
          final sample =
              <String?>[item.primaryText, item.secondaryText, item.vlmText]
                  .whereType<String>()
                  .where((text) => text.trim().isNotEmpty)
                  .firstOrNull;
          return ListTile(
            leading: Icon(
              marked.contains(key) ? Icons.bookmark : Icons.fact_check_outlined,
            ),
            title: Text(
              '第 ${item.pageIndex + 1} 页 · 第 ${item.pairIndex + 1} 组',
            ),
            subtitle: Text(
              sample ?? '无文字候选',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: Text('${(item.score * 100).round()}%'),
            onTap: () => Navigator.of(context).pop(key),
          );
        },
      ),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('关闭'),
      ),
    ],
  ),
);
