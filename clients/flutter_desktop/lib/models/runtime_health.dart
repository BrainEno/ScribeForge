import 'dart:convert';

/// A single explicit check, not a cached installation status or a guarantee.
class RuntimeHealthItem {
  const RuntimeHealthItem(this.backend, this.state, this.detail);
  final String backend;
  final String state;
  final String? detail;

  String get label => backend == 'mineru' ? 'MinerU' : 'PaddleOCR';
  String get stateLabel => switch (state) {
    'passed' => '示例识别通过',
    'not_ready' => '环境未就绪，未运行示例',
    _ => '示例识别失败',
  };
}

class RuntimeHealthReport {
  const RuntimeHealthReport({
    required this.success,
    required this.cancelled,
    required this.items,
    this.error,
  });
  final bool success;
  final bool cancelled;
  final List<RuntimeHealthItem> items;
  final String? error;

  factory RuntimeHealthReport.fromJson(Map<String, dynamic> json) {
    final success = json['success'];
    final cancelled = json['cancelled'];
    final rows = json['results'];
    if (success is! bool ||
        cancelled is! bool ||
        rows is! List ||
        (json['error'] != null && json['error'] is! String)) {
      throw const FormatException('检查结果格式错误');
    }
    final items = <RuntimeHealthItem>[];
    for (final row in rows) {
      if (row is! Map ||
          !const ['mineru', 'paddleocr'].contains(row['backend']) ||
          !const ['passed', 'failed', 'not_ready'].contains(row['state']) ||
          (row['detail'] != null && row['detail'] is! String)) {
        throw const FormatException('引擎检查结果格式错误');
      }
      items.add(
        RuntimeHealthItem(
          row['backend'] as String,
          row['state'] as String,
          row['detail'] as String?,
        ),
      );
    }
    final names = items.map((item) => item.backend).toSet();
    if (names.length != items.length ||
        (items.isNotEmpty && (names.length != 2 || items.length != 2)) ||
        (success &&
            (cancelled ||
                items.length != 2 ||
                items.any((item) => item.state != 'passed'))) ||
        (!success &&
            items.length == 2 &&
            items.every((item) => item.state == 'passed'))) {
      throw const FormatException('检查结果不完整或自相矛盾');
    }
    return RuntimeHealthReport(
      success: success,
      cancelled: cancelled,
      items: List.unmodifiable(items),
      error: json['error'] as String?,
    );
  }
}

/// Strict, bounded newline-delimited protocol. A partial/malformed result is
/// never accepted as evidence that the runtime passed.
class RuntimeHealthStream {
  RuntimeHealthStream(this.onProgress);
  final void Function(String backend) onProgress;
  String _pending = '';
  RuntimeHealthReport? report;
  static const int _limit = 65536;

  void add(String chunk) {
    for (final part in chunk.split('\n').asMap().entries) {
      if (part.key > 0) _consume();
      if (_pending.length + part.value.length > _limit) {
        throw const FormatException('检查输出过长');
      }
      _pending += part.value;
    }
  }

  void finish() {
    if (_pending.trim().isNotEmpty) _consume();
    if (report == null) throw const FormatException('检查未返回最终结果');
  }

  void _consume() {
    final line = _pending.trim();
    _pending = '';
    if (line.isEmpty) return;
    final decoded = jsonDecode(line);
    if (decoded is! Map || decoded['schema_version'] != 1 || report != null) {
      throw const FormatException('检查输出格式错误');
    }
    if (decoded['type'] == 'progress' &&
        const ['mineru', 'paddleocr'].contains(decoded['backend'])) {
      onProgress(decoded['backend'] as String);
    } else if (decoded['type'] == 'result') {
      report = RuntimeHealthReport.fromJson(Map<String, dynamic>.from(decoded));
    } else {
      throw const FormatException('未知检查输出');
    }
  }
}
