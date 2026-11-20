import '../models/export_readiness.dart';
import 'cli_executor.dart';

class ExportReadinessService {
  const ExportReadinessService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;
  final String storePath;
  final int projectId;

  Future<ExportReadinessReport> check(ExportReadinessOptions options) async {
    final output = StringBuffer();
    final diagnostics = StringBuffer();
    const responseLimit = 2 * 1024 * 1024;
    var oversized = false;
    final result = await executor.run(
      executable: executable,
      arguments: <String>[
        'export-readiness',
        '--store',
        storePath,
        if (options.strict) '--strict-acceptance',
        if (options.includePageFurniture) '--include-page-furniture',
        if (options.page != null) ...<String>['--page', '${options.page}'],
        '--limit',
        '120',
      ],
      onOutput: (chunk) {
        if (oversized || output.length + chunk.length > responseLimit) {
          oversized = true;
          return;
        }
        output.write(chunk);
      },
      onError: (chunk) {
        final remaining = 4500 - diagnostics.length;
        if (remaining > 0)
          diagnostics.write(
            chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
          );
      },
    );
    if (result != 0)
      throw ExportReadinessFailure(result, diagnostics.toString().trim());
    if (oversized) throw const FormatException('导出检查结果超过读取上限。');
    return ExportReadinessReport.fromText(
      output.toString(),
      projectId: projectId,
      expected: options,
    );
  }
}

class ExportReadinessFailure implements Exception {
  const ExportReadinessFailure(this.exitCode, this.diagnostic);

  final int exitCode;
  final String diagnostic;

  @override
  String toString() => diagnostic.isEmpty
      ? '无法完成导出检查（退出码 $exitCode）。'
      : '无法完成导出检查（退出码 $exitCode）：$diagnostic';
}
