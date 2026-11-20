import 'dart:io';

import '../models/export_verification.dart';
import 'cli_executor.dart';

class ExportVerificationService {
  const ExportVerificationService({
    required this.executor,
    required this.executable,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;

  Future<ExportVerificationReport> verify(
    String path, {
    String? sourceDigest,
  }) async {
    final filename = Uri.file(
      path,
      windows: Platform.isWindows,
    ).pathSegments.last;
    final output = StringBuffer();
    final diagnostics = StringBuffer();
    const responseLimit = 256 * 1024;
    var oversized = false;
    final code = await executor.run(
      executable: executable,
      arguments: <String>[
        'verify-export',
        '--file=$path',
        if (sourceDigest != null) ...<String>['--source-sha256', sourceDigest],
      ],
      onOutput: (value) {
        if (oversized || output.length + value.length > responseLimit) {
          oversized = true;
        } else {
          output.write(value);
        }
      },
      onError: (value) {
        final left = 4000 - diagnostics.length;
        if (left > 0)
          diagnostics.write(
            value.length > left ? value.substring(0, left) : value,
          );
      },
    );
    if (code != 0)
      throw ExportVerificationFailure(code, diagnostics.toString().trim());
    if (oversized) throw const FormatException('导出校验响应超过读取限制。');
    return ExportVerificationReport.fromText(
      output.toString(),
      filename: filename,
      expectedSource: sourceDigest,
    );
  }
}

class ExportVerificationFailure implements Exception {
  const ExportVerificationFailure(this.exitCode, this.diagnostic);

  final int exitCode;
  final String diagnostic;

  @override
  String toString() => diagnostic.isEmpty
      ? '无法读取导出文件（退出码 $exitCode）。'
      : '无法读取导出文件（退出码 $exitCode）：$diagnostic';
}
