import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/ocr_evidence.dart';
import '../../services/ocr_evidence_service.dart';
import 'ocr_evidence_list.dart';

class OcrEvidenceBrowser extends StatelessWidget {
  const OcrEvidenceBrowser({super.key, required this.service});

  final OcrEvidenceService service;

  void _openPage(BuildContext context, int page) {
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => _PageOverview(service: service, page: page),
        ),
      ),
    );
  }

  Future<void> _jump(BuildContext context) async {
    final page = await showDialog<int>(
      context: context,
      builder: (_) => const _PageJump(),
    );
    if (context.mounted && page != null) _openPage(context, page);
  }

  @override
  Widget build(BuildContext context) => OcrEvidenceList<OcrEvidencePage>(
    title: 'OCR 证据浏览器',
    description: const Text(
      '只读：查看已保存的原始 OCR 和对齐证据。可查看已处理、未核验或历史批次。这里不会修改人工决策，也不代表导出文本。',
    ),
    emptyMessage: '项目尚未保存任何物理页。',
    load: (after) => service.pages(after: after),
    actions: <Widget>[
      IconButton(
        tooltip: '跳到物理页',
        onPressed: () => _jump(context),
        icon: const Icon(Icons.find_in_page_outlined),
      ),
    ],
    itemBuilder: (context, page) => Card(
      child: ListTile(
        key: ValueKey('ocr-evidence-page-${page.index}'),
        title: Text('第 ${page.index + 1} 页'),
        subtitle: Text(
          '${page.width} × ${page.height} 像素 · ${page.runCount} 个 OCR 批次 · ${page.statusLabel}',
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => _openPage(context, page.index),
      ),
    ),
  );
}

class _PageJump extends StatefulWidget {
  const _PageJump();

  @override
  State<_PageJump> createState() => _PageJumpState();
}

class _PageJumpState extends State<_PageJump> {
  final _form = GlobalKey<FormState>();
  String _value = '';

  int? _parse(String? value) => int.tryParse(value?.trim() ?? '');

  void _submit() {
    if (_form.currentState?.validate() != true) return;
    Navigator.of(context).pop(_parse(_value)! - 1);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('跳到物理页'),
    content: Form(
      key: _form,
      child: TextFormField(
        autofocus: true,
        keyboardType: TextInputType.number,
        decoration: const InputDecoration(labelText: '页码（从 1 开始）'),
        onChanged: (value) => _value = value,
        onFieldSubmitted: (_) => _submit(),
        validator: (value) => (_parse(value) ?? 0) > 0 ? null : '请输入大于零的整数。',
      ),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      FilledButton(onPressed: _submit, child: const Text('打开物理页')),
    ],
  );
}

class _PageOverview extends StatefulWidget {
  const _PageOverview({required this.service, required this.page});

  final OcrEvidenceService service;
  final int page;

  @override
  State<_PageOverview> createState() => _PageOverviewState();
}

class _PageOverviewState extends State<_PageOverview> {
  OcrPageOverview? _result;
  String? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.service.overview(widget.page);
      if (mounted) setState(() => _result = result);
    } on Object catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _lines(OcrEvidenceRun run) {
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) =>
              _RawLines(service: widget.service, page: widget.page, run: run),
        ),
      ),
    );
  }

  void _pairs(OcrVerificationRun run) {
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => _AlignedPairs(
            service: widget.service,
            page: widget.page,
            run: run,
          ),
        ),
      ),
    );
  }

  Iterable<Widget> _summaries(
    BuildContext context,
    OcrPageOverview result,
  ) sync* {
    final style = Theme.of(context).textTheme.titleMedium;
    yield Text('原页尺寸：${result.width} × ${result.height} 像素');
    yield const SizedBox(height: 12);
    yield Text('核验批次', style: style);
    if (result.verifications.isEmpty) yield const Text('这一页没有已保存的核验。');
    for (final (index, verification) in result.verifications.indexed) {
      yield Card(
        child: ListTile(
          title: Text(
            '核验 #${verification.id}${index == 0 ? ' · 读取时最新' : ' · 历史批次'}',
          ),
          subtitle: Text(
            'OCR #${verification.primary} ↔ #${verification.secondary} · ${verification.pairCount} 组\n${verification.createdAt}',
          ),
          isThreeLine: true,
          trailing: const Icon(Icons.compare_arrows),
          onTap: () => _pairs(verification),
        ),
      );
    }
    if (result.olderVerificationsOmitted) yield const Text('只显示最近 100 个核验批次。');
    yield const SizedBox(height: 14);
    yield Text('原始 OCR 批次', style: style);
    if (result.runs.isEmpty) yield const Text('这一页没有保存过 OCR。');
    for (final run in result.runs) {
      yield Card(
        child: ListTile(
          title: Text(run.title),
          subtitle: Text(
            '${run.lineCount} 行 · ${run.frameLabel}\n${run.createdAt}',
          ),
          isThreeLine: true,
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _lines(run),
        ),
      );
    }
    if (result.olderRunsOmitted) yield const Text('只显示最近 100 个 OCR 批次。');
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    return Scaffold(
      appBar: AppBar(
        title: Text('第 ${widget.page + 1} 页 · 批次'),
        actions: <Widget>[
          IconButton(
            tooltip: '重新读取批次',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(12),
        children: <Widget>[
          if (_loading) const LinearProgressIndicator(),
          if (_error case final error?)
            OcrEvidenceError(message: error, onRetry: _load),
          if (result != null) ..._summaries(context, result),
        ],
      ),
    );
  }
}

class _RawLines extends StatelessWidget {
  const _RawLines({
    required this.service,
    required this.page,
    required this.run,
  });

  final OcrEvidenceService service;
  final int page;
  final OcrEvidenceRun run;

  @override
  Widget build(BuildContext context) => OcrEvidenceList<OcrEvidenceLine>(
    title: '第 ${page + 1} 页 · OCR #${run.id}',
    description: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(run.title, style: Theme.of(context).textTheme.titleMedium),
        Text('${run.lineCount} 行 · ${run.frameLabel} · ${run.createdAt}'),
        if (run.frameWidth != null && run.frameHeight != null)
          Text('输入尺寸：${run.frameWidth} × ${run.frameHeight} 像素'),
        if (run.frameHash case final hash?)
          SelectableText('保存的输入 SHA-256：$hash'),
        const Text('以下按 OCR 批次的行索引排列，不是最终阅读顺序。坐标为批次输入的归一化 x、y、宽、高。'),
      ],
    ),
    emptyMessage: '该 OCR 批次没有保存文字行。',
    load: (after) => service.lines(page, run.id, after: after),
    itemBuilder: (context, line) => _EvidenceCard(
      title: '行索引 ${line.index}',
      children: <Widget>[
        _TextEvidence(text: line.text, truncated: line.textTruncated),
        SelectableText('坐标：${line.coordinates}'),
        Text(
          '词元 ${line.tokenCount} · 最低已报告置信度：${_percent(line.minConfidence)}',
        ),
      ],
    ),
  );
}

class _AlignedPairs extends StatelessWidget {
  const _AlignedPairs({
    required this.service,
    required this.page,
    required this.run,
  });

  final OcrEvidenceService service;
  final int page;
  final OcrVerificationRun run;

  @override
  Widget build(BuildContext context) => OcrEvidenceList<OcrEvidencePair>(
    title: '第 ${page + 1} 页 · 核验 #${run.id}',
    description: Text(
      'OCR #${run.primary} ↔ #${run.secondary} · ${run.pairCount} 组\n${run.createdAt}\n这里显示保存的行对齐和风险，不代表已接受文本。',
    ),
    emptyMessage: '该核验批次没有保存对齐组。',
    load: (after) => service.pairs(page, run.id, after: after),
    itemBuilder: (context, pair) {
      final primary = _PairReading(
        title: '主侧 · OCR #${run.primary}',
        side: pair.primary,
      );
      final secondary = _PairReading(
        title: '辅侧 · OCR #${run.secondary}',
        side: pair.secondary,
      );
      return _EvidenceCard(
        title: '组索引 ${pair.index}${pair.ambiguous ? ' · 对齐有歧义' : ''}',
        children: <Widget>[
          Text(
            '相似度 ${_percent(pair.similarity)} · 风险 ${_percent(pair.risk)} · ${pair.conflicts} 处冲突',
          ),
          if (pair.verticalDistance case final distance?)
            Text('纵向距离：${distance.toStringAsFixed(4)}'),
          const SizedBox(height: 8),
          LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth < 520) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[primary, secondary],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Expanded(child: primary),
                  const SizedBox(width: 8),
                  Expanded(child: secondary),
                ],
              );
            },
          ),
        ],
      );
    },
  );
}

class _PairReading extends StatelessWidget {
  const _PairReading({required this.title, required this.side});

  final String title;
  final OcrPairSide side;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(8),
    decoration: BoxDecoration(
      border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
      borderRadius: BorderRadius.circular(8),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(title, style: Theme.of(context).textTheme.titleSmall),
        if (side.lines.isEmpty) const Text('没有配对行。'),
        for (final line in side.lines) ...<Widget>[
          const Divider(),
          Text('行索引 ${line.index}'),
          if (line.text case final text?)
            _TextEvidence(text: text, truncated: line.truncated)
          else
            const Text('找不到记录所引用的文字行。'),
        ],
        if (side.additional) const Text('此处只预览前 8 个成员；可打开该批次查看原始行。'),
      ],
    ),
  );
}

class _TextEvidence extends StatelessWidget {
  const _TextEvidence({required this.text, required this.truncated});

  final String text;
  final bool truncated;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      SelectableText(text.isEmpty ? '（空文字）' : text),
      if (truncated) const Text('仅显示该行的开头，文字未完整展示。'),
    ],
  );
}

class _EvidenceCard extends StatelessWidget {
  const _EvidenceCard({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          ...children,
        ],
      ),
    ),
  );
}

String _percent(double? value) =>
    value == null ? '未记录' : '${(value * 100).toStringAsFixed(1)}%';
