import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/ocr_evidence.dart';

/// Cursor-backed list used by the physical-page, raw-line and pair views.
/// Requests from a previous refresh never replace newer results.
class OcrEvidenceList<T> extends StatefulWidget {
  const OcrEvidenceList({
    super.key,
    required this.title,
    required this.description,
    required this.emptyMessage,
    required this.load,
    required this.itemBuilder,
    this.actions = const <Widget>[],
  });

  final String title;
  final Widget description;
  final String emptyMessage;
  final Future<OcrEvidenceBatch<T>> Function(int?) load;
  final Widget Function(BuildContext, T) itemBuilder;
  final List<Widget> actions;

  @override
  State<OcrEvidenceList<T>> createState() => _OcrEvidenceListState<T>();
}

class _OcrEvidenceListState<T> extends State<OcrEvidenceList<T>> {
  final _scroll = ScrollController();
  final _rows = <T>[];
  int? _next;
  var _generation = 0;
  var _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load({bool next = false}) async {
    final cursor = next ? _next : null;
    if (next && (_loading || cursor == null)) return;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
      if (!next) {
        _rows.clear();
        _next = null;
      }
    });
    if (!next && _scroll.hasClients) _scroll.jumpTo(0);
    try {
      final batch = await widget.load(cursor);
      if (!mounted || generation != _generation) return;
      setState(() {
        _rows.addAll(batch.rows);
        _next = batch.nextAfter;
      });
    } on Object catch (error) {
      if (mounted && generation == _generation)
        setState(() => _error = '$error');
    } finally {
      if (mounted && generation == _generation)
        setState(() => _loading = false);
    }
  }

  Widget _footer() {
    if (_loading) return const LinearProgressIndicator();
    if (_error case final error?) {
      return OcrEvidenceError(
        message: error,
        onRetry: () => _load(next: _rows.isNotEmpty),
      );
    }
    if (_next != null) {
      return Align(
        alignment: Alignment.center,
        child: OutlinedButton.icon(
          onPressed: () => _load(next: true),
          icon: const Icon(Icons.expand_more),
          label: const Text('加载下一批'),
        ),
      );
    }
    return Text(_rows.isEmpty ? widget.emptyMessage : '已显示全部记录。');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(widget.title),
      actions: <Widget>[
        ...widget.actions,
        IconButton(
          tooltip: '重新读取',
          onPressed: _loading ? null : () => _load(),
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.all(12),
      itemCount: _rows.length + 2,
      itemBuilder: (context, index) {
        if (index == 0) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(4, 4, 4, 12),
            child: widget.description,
          );
        }
        if (index > _rows.length) {
          return Padding(padding: const EdgeInsets.all(8), child: _footer());
        }
        return widget.itemBuilder(context, _rows[index - 1]);
      },
    ),
  );
}

class OcrEvidenceError extends StatelessWidget {
  const OcrEvidenceError({
    super.key,
    required this.message,
    required this.onRetry,
  });

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Column(
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
  );
}
