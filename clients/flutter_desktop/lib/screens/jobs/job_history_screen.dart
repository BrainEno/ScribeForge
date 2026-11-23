import 'dart:async';
import 'package:flutter/material.dart';
import '../../models/job_attempt_history.dart';
import '../../models/physical_page_selection.dart';
import '../../services/job_history_service.dart';

/// Read-only investigation. Selecting a page returns it to the workspace;
/// processing can only begin after a separate confirmation and safety checks.
class JobHistoryScreen extends StatefulWidget {
  const JobHistoryScreen({
    super.key,
    required this.service,
    required this.canSelectPage,
  });
  final JobHistoryService service;
  final bool canSelectPage;
  @override
  State<JobHistoryScreen> createState() => _JobHistoryScreenState();
}

class _JobHistoryScreenState extends State<JobHistoryScreen> {
  final page = TextEditingController();
  final records = <JobAttempt>[];
  String? stage;
  String? appliedStage;
  int? pageIndex;
  JobHistoryBatch? batch;
  bool loading = false;
  String? error;
  int version = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    page.dispose();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    if (more && (loading || batch?.nextBefore == null)) return;
    final before = more ? batch!.nextBefore : null;
    final request = ++version;
    setState(() {
      loading = true;
      error = null;
      if (!more) {
        batch = null;
        records.clear();
      }
    });
    try {
      final response = await widget.service.list(
        pageIndex: pageIndex,
        stage: appliedStage,
        before: before,
      );
      if (!mounted || version != request) return;
      setState(() {
        batch = response;
        records.addAll(response.rows);
      });
    } on Object catch (exc) {
      if (!mounted || version != request) return;
      setState(() => error = '$exc');
    } finally {
      if (mounted && version == request) setState(() => loading = false);
    }
  }

  void _applyFilter() {
    int? parsed;
    try {
      if (page.text.trim().isNotEmpty) {
        final selected = parsePhysicalPages(page.text);
        if (selected.length != 1) throw const FormatException('筛选时只能选择一页。');
        parsed = selected.single;
      }
    } on FormatException catch (exc) {
      setState(() => error = exc.message);
      return;
    }
    pageIndex = parsed;
    appliedStage = stage;
    unawaited(_load());
  }

  Widget _record(JobAttempt item) => Card(
    margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    child: ExpansionTile(
      key: ValueKey('job-attempt-${item.id}'),
      title: Text(
        '${item.location} · ${item.stageLabel} · ${item.outcomeLabel}',
      ),
      subtitle: Text(
        '第 ${item.attempt} 次尝试 · 当前任务${item.currentLabel} · 记录于 ${item.recordedAt}',
      ),
      childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      expandedCrossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (item.origin == 'legacy_snapshot')
          const Text('旧版升级时保留下来的最后一次失败；更早错误可能已丢失。'),
        if (item.outcome == 'interrupted')
          const Text('新进程获取了锁并接管运行中的任务；这不能说明中断原因。'),
        if (item.startedAt != null) Text('开始：${item.startedAt}'),
        if (item.finishedAt != null) Text('结束：${item.finishedAt}'),
        if (item.error != null) ...[
          const SizedBox(height: 8),
          const Text('诊断内容（分享前请检查隐私）：'),
          SelectableText(item.error!),
          if (item.errorTruncated) const Text('此处仅显示前 4096 个字符；完整记录保存在项目中。'),
        ],
        if (item.pageIndex != null && widget.canSelectPage) ...[
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: ValueKey('select-retry-page-${item.id}'),
            onPressed: () => Navigator.of(context).pop(item.pageIndex),
            icon: const Icon(Icons.playlist_add_check),
            label: const Text('选择此物理页准备续跑'),
          ),
        ],
      ],
    ),
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('失败与中断历史'),
      actions: [
        IconButton(
          tooltip: '刷新历史',
          onPressed: loading ? null : () => _load(),
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: ListView(
      children: [
        const Padding(
          padding: EdgeInsets.all(16),
          child: Text(
            '只读历史：包含记录到的失败和重新接管的中断，不是全部尝试。旧版已丢失的错误无法恢复。'
            '诊断可能含书籍内容、路径或其他私人信息；分享前请检查。',
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SizedBox(
                width: 160,
                child: TextField(
                  controller: page,
                  key: const ValueKey('job-history-page-filter'),
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'PDF 物理页码（选填）'),
                ),
              ),
              SizedBox(
                width: 180,
                child: DropdownButtonFormField<String>(
                  key: const ValueKey('job-history-stage-filter'),
                  initialValue: stage,
                  decoration: const InputDecoration(labelText: '处理阶段'),
                  items: [
                    const DropdownMenuItem(value: null, child: Text('全部阶段')),
                    for (final entry in jobStageLabels.entries)
                      DropdownMenuItem(
                        value: entry.key,
                        child: Text(entry.value),
                      ),
                  ],
                  onChanged: (value) => setState(() => stage = value),
                ),
              ),
              FilledButton(
                onPressed: loading ? null : _applyFilter,
                child: const Text('筛选'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        if (batch?.available == false)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text('旧版项目没有尝试历史；当前失败仍可在项目状态中查看。'),
          )
        else
          ...records.map(_record),
        if (loading)
          const Padding(
            padding: EdgeInsets.all(16),
            child: LinearProgressIndicator(),
          )
        else if (error != null)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SelectableText(error!),
                TextButton(
                  onPressed: () => _load(
                    more: records.isNotEmpty && batch?.nextBefore != null,
                  ),
                  child: const Text('重试读取'),
                ),
              ],
            ),
          ),
        if (!loading &&
            error == null &&
            batch?.available == true &&
            records.isEmpty)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text('没有符合条件的失败或中断记录。'),
          ),
        if (!loading && error == null && batch?.nextBefore != null)
          Padding(
            padding: const EdgeInsets.all(16),
            child: OutlinedButton(
              key: const ValueKey('job-history-more'),
              onPressed: () => _load(more: true),
              child: const Text('加载更早记录'),
            ),
          ),
        if (!loading &&
            error == null &&
            records.isNotEmpty &&
            batch?.nextBefore == null)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text('已加载到最早记录；刷新可查看新发生的失败。'),
          ),
      ],
    ),
  );
}
