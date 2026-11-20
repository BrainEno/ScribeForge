import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/evidence_search.dart';
import '../../models/ocr_evidence.dart';
import '../../models/review_history.dart';
import '../../services/evidence_search_service.dart';
import 'ocr_evidence_list.dart';

class EvidenceSearchScreen extends StatefulWidget {
  const EvidenceSearchScreen({super.key, required this.service});

  final EvidenceSearchService service;

  @override
  State<EvidenceSearchScreen> createState() => _EvidenceSearchScreenState();
}

class _EvidenceSearchScreenState extends State<EvidenceSearchScreen> {
  final _form = GlobalKey<FormState>();
  final _term = TextEditingController();
  final _firstPage = TextEditingController();
  final _lastPage = TextEditingController();
  var _source = EvidenceSearchSource.ocr;
  ReviewHistoryActor? _actor;

  @override
  void dispose() {
    _term.dispose();
    _firstPage.dispose();
    _lastPage.dispose();
    super.dispose();
  }

  int? _pageIndex(String text) {
    final parsed = int.tryParse(text.trim());
    return parsed != null && parsed > 0 ? parsed - 1 : null;
  }

  String? _validatePage(String? text) {
    if (text == null || text.trim().isEmpty) return null;
    return _pageIndex(text) == null ? '请输入大于零的页码。' : null;
  }

  String? _validateLastPage(String? text) {
    final error = _validatePage(text);
    if (error != null) return error;
    final first = _pageIndex(_firstPage.text);
    final last = _pageIndex(text ?? '');
    return first != null && last != null && last < first ? '结束页不能早于起始页。' : null;
  }

  String? _validateTerm(String? text) {
    if (text == null || text.trim().isEmpty) return '请输入要查找的文字。';
    if (text.contains('\u0000')) return '查找文字不能包含空字符。';
    if (text.runes.length > EvidenceSearchQuery.maximumTermLength) {
      return '查找文字最多 160 个 Unicode 字符。';
    }
    return null;
  }

  void _search() {
    if (_form.currentState?.validate() != true) return;
    final query = EvidenceSearchQuery(
      term: _term.text,
      source: _source,
      pageStart: _pageIndex(_firstPage.text),
      pageEnd: _pageIndex(_lastPage.text),
      actor: _source == EvidenceSearchSource.decisions ? _actor : null,
    );
    FocusScope.of(context).unfocus();
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) =>
              _EvidenceSearchResults(service: widget.service, query: query),
        ),
      ),
    );
  }

  Widget _range(BuildContext context) {
    final first = TextFormField(
      key: const ValueKey('evidence-search-first-page'),
      controller: _firstPage,
      keyboardType: TextInputType.number,
      decoration: const InputDecoration(labelText: '起始物理页（可选）'),
      validator: _validatePage,
      onFieldSubmitted: (_) => _search(),
    );
    final last = TextFormField(
      key: const ValueKey('evidence-search-last-page'),
      controller: _lastPage,
      keyboardType: TextInputType.number,
      decoration: const InputDecoration(labelText: '结束物理页（可选）'),
      validator: _validateLastPage,
      onFieldSubmitted: (_) => _search(),
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < 480) {
          return Column(
            children: <Widget>[first, const SizedBox(height: 8), last],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(child: first),
            const SizedBox(width: 12),
            Expanded(child: last),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('全书证据检索')),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        const Text('在当前项目中查找连续原文。区分大小写、异体字和标点；不跨 OCR 行或决策记录。'),
        const SizedBox(height: 8),
        const Text('使用本地项目数据库，不调用 OCR 或远程模型。历史记录不等同于最终导出内容。'),
        const SizedBox(height: 12),
        Form(
          key: _form,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              TextFormField(
                key: const ValueKey('evidence-search-term'),
                controller: _term,
                autofocus: true,
                decoration: const InputDecoration(
                  labelText: '按原文查找',
                  hintText: '例如：𠀀、校勘记、%',
                ),
                textInputAction: TextInputAction.search,
                validator: _validateTerm,
                onFieldSubmitted: (_) => _search(),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<EvidenceSearchSource>(
                key: const ValueKey('evidence-search-source'),
                initialValue: _source,
                isExpanded: true,
                decoration: const InputDecoration(labelText: '检索来源'),
                items: <DropdownMenuItem<EvidenceSearchSource>>[
                  for (final source in EvidenceSearchSource.values)
                    DropdownMenuItem(value: source, child: Text(source.label)),
                ],
                onChanged: (source) {
                  if (source != null) setState(() => _source = source);
                },
              ),
              const SizedBox(height: 12),
              _range(context),
              if (_source == EvidenceSearchSource.decisions) ...<Widget>[
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: _actor?.value ?? '',
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: '决策来源'),
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
              const SizedBox(height: 16),
              Align(
                alignment: Alignment.centerLeft,
                child: FilledButton.icon(
                  key: const ValueKey('run-evidence-search'),
                  onPressed: _search,
                  icon: const Icon(Icons.search),
                  label: const Text('搜索证据'),
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

class _EvidenceSearchResults extends StatefulWidget {
  const _EvidenceSearchResults({required this.service, required this.query});

  final EvidenceSearchService service;
  final EvidenceSearchQuery query;

  @override
  State<_EvidenceSearchResults> createState() => _EvidenceSearchResultsState();
}

class _EvidenceSearchResultsState extends State<_EvidenceSearchResults> {
  int? _snapshot;

  Future<OcrEvidenceBatch<EvidenceSearchHit>> _load(int? before) async {
    final batch = await widget.service.search(
      widget.query,
      snapshot: before == null ? null : _snapshot,
      before: before,
    );
    _snapshot = batch.snapshot;
    return OcrEvidenceBatch<EvidenceSearchHit>(batch.hits, batch.nextBefore);
  }

  void _open(EvidenceSearchHit hit) {
    final snapshot = _snapshot;
    if (snapshot == null) return;
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => _EvidenceHitScreen(
            service: widget.service,
            query: widget.query,
            preview: hit,
            snapshot: snapshot,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => OcrEvidenceList<EvidenceSearchHit>(
    title: '证据搜索结果',
    description: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          widget.query.description,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        SelectableText('搜索原文：${widget.query.term}'),
        const Text('按保存记录从新到旧排列，每条记录标出第一次命中。刷新会重新搜索最新记录。'),
      ],
    ),
    emptyMessage: '没有逐字匹配的记录。可缩短查找词或扩大页码范围。',
    load: _load,
    itemBuilder: (context, hit) => Card(
      child: ListTile(
        key: ValueKey('evidence-search-hit-${hit.id}'),
        title: Text(hit.location),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(hit.summary),
            _HighlightedExcerpt(excerpt: hit.excerpt, selectable: false),
          ],
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => _open(hit),
      ),
    ),
  );
}

class _EvidenceHitScreen extends StatefulWidget {
  const _EvidenceHitScreen({
    required this.service,
    required this.query,
    required this.preview,
    required this.snapshot,
  });

  final EvidenceSearchService service;
  final EvidenceSearchQuery query;
  final EvidenceSearchHit preview;
  final int snapshot;

  @override
  State<_EvidenceHitScreen> createState() => _EvidenceHitScreenState();
}

class _EvidenceHitScreenState extends State<_EvidenceHitScreen> {
  EvidenceSearchHit? _detail;
  bool _loading = false;
  String? _error;

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
      final hit = await widget.service.detail(
        widget.query,
        widget.preview,
        snapshot: widget.snapshot,
      );
      if (mounted) setState(() => _detail = hit);
    } on Object catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _copy(EvidenceSearchHit hit) async {
    var message = '已复制原文及来源。';
    try {
      await Clipboard.setData(
        ClipboardData(
          text: <String>[
            hit.location,
            hit.summary,
            hit.excerpt.truncated ? '以下仅为可见节选：' : '原始存储文字：',
            hit.excerpt.text,
          ].join('\n'),
        ),
      );
    } on PlatformException {
      message = '无法访问剪贴板，可手动选择原文。';
    }
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  Iterable<Widget> _metadata(EvidenceSearchHit hit) sync* {
    yield Text(hit.summary, style: Theme.of(context).textTheme.titleMedium);
    switch (hit) {
      case OcrSearchHit():
        yield const Text('原始 OCR，不是已接受文本。');
        yield SelectableText('批次归一化坐标 x、y、宽、高：${hit.coordinates}');
      case DecisionSearchHit():
        yield Text('记录时间：${hit.createdAt}（数据库时间）');
        yield Text(
          hit.latestForPair ? '在本次快照中是此页组的最后一条记录。' : '在本次快照中此页组还有更新记录。',
        );
        if (hit.verification != null)
          yield Text('保存的核验引用：#${hit.verification}');
        if (hit.citationsIncomplete) yield const Text('部分引用未显示或无法解析。');
        if (hit.citations.isEmpty && !hit.citationsIncomplete)
          yield const Text('未保存证据引用。');
        for (final ref in hit.citations) {
          yield SelectableText(ref);
        }
    }
  }

  @override
  Widget build(BuildContext context) {
    final hit = _detail ?? widget.preview;
    return Scaffold(
      appBar: AppBar(
        title: Text(hit.location),
        actions: <Widget>[
          IconButton(
            tooltip: '复制原文及来源',
            onPressed: () => _copy(hit),
            icon: const Icon(Icons.copy_outlined),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          ..._metadata(hit),
          const Divider(),
          if (_loading) const LinearProgressIndicator(),
          if (_error case final error?)
            OcrEvidenceError(message: error, onRetry: _load),
          if (hit.excerpt.truncated) const Text('仅显示命中附近的节选。'),
          _HighlightedExcerpt(excerpt: hit.excerpt, selectable: true),
        ],
      ),
    );
  }
}

class _HighlightedExcerpt extends StatelessWidget {
  const _HighlightedExcerpt({required this.excerpt, required this.selectable});

  final SearchExcerpt excerpt;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final span = TextSpan(
      children: <TextSpan>[
        if (excerpt.omittedBefore) const TextSpan(text: '…'),
        TextSpan(text: excerpt.before),
        TextSpan(
          text: excerpt.match,
          style: TextStyle(
            fontWeight: FontWeight.bold,
            backgroundColor: colors.tertiaryContainer,
            color: colors.onTertiaryContainer,
          ),
        ),
        TextSpan(text: excerpt.after),
        if (excerpt.omittedAfter) const TextSpan(text: '…'),
      ],
    );
    return selectable
        ? SelectableText.rich(span)
        : Text.rich(span, maxLines: 5, overflow: TextOverflow.ellipsis);
  }
}
