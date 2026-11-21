import '../models/report_comparison.dart';
import 'cli_executor.dart';

class ReportComparisonService {
  const ReportComparisonService({
    required this.executor,
    required this.executable,
  });

  static const pageSize = 15;
  static const _responseLimit = 4 * 1024 * 1024;
  final ScribeForgeCliExecutor executor;
  final String executable;

  Future<ReportComparison> compare({
    required String baseline,
    required String candidate,
    required String filter,
    required String order,
    required int offset,
    required bool allowUnmatched,
    ReportComparison? pinned,
  }) async {
    final output = StringBuffer();
    final errors = StringBuffer();
    var tooLarge = false;
    final code = await executor.run(
      executable: executable,
      arguments: [
        'compare-processing-reports',
        '--baseline=$baseline',
        '--candidate=$candidate',
        '--filter=$filter',
        '--order=$order',
        '--offset=$offset',
        '--limit=$pageSize',
        if (allowUnmatched) '--allow-unmatched',
        if (pinned != null) '--baseline-sha256=${pinned.baseline.digest}',
        if (pinned != null) '--candidate-sha256=${pinned.candidate.digest}',
      ],
      onOutput: (chunk) {
        if (output.length + chunk.length > _responseLimit) tooLarge = true;
        if (!tooLarge) output.write(chunk);
      },
      onError: (chunk) {
        final available = 3000 - errors.length;
        if (available > 0)
          errors.write(
            chunk.length <= available ? chunk : chunk.substring(0, available),
          );
      },
    );
    if (code != 0)
      throw ReportComparisonFailure(code, errors.toString().trim());
    if (tooLarge) throw const FormatException('对比响应超过读取限制。');
    final result = ReportComparison.decode(output.toString());
    if (result.offset != offset ||
        result.filter != filter ||
        result.order != order ||
        result.pageComparison != (result.sameLabels || allowUnmatched) ||
        result.pages.length > pageSize ||
        (pinned != null &&
            (result.baseline.digest != pinned.baseline.digest ||
                result.candidate.digest != pinned.candidate.digest))) {
      throw const FormatException('收到的对比结果与本次请求不一致。');
    }
    return result;
  }
}

class ReportComparisonFailure implements Exception {
  const ReportComparisonFailure(this.code, this.diagnostics);

  final int code;
  final String diagnostics;

  @override
  String toString() =>
      '无法完成对比（退出码 $code）${diagnostics.isEmpty ? '。' : '：$diagnostics'}';
}
