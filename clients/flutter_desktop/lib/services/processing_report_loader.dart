import 'cli_executor.dart';
import 'cli_runner.dart';
import '../models/processing_report.dart';

/// Loads the schema-v1 processing report for a project store by running
/// `scribeforge report --store <store> --json` (issue #219).
///
/// The executor is injectable so tests never spawn a real CLI. Failures are
/// surfaced as [ProcessingReportLoadException] with an actionable message —
/// the UI must show that message and offer a retry instead of stale data.
class ProcessingReportLoader {
  const ProcessingReportLoader({this.executor});

  final ScribeForgeCliExecutor? executor;

  Future<ProcessingReportSnapshot> load({
    required String executable,
    required String storePath,
    int? projectId,
  }) async {
    final run = executor ?? ProcessScribeForgeCliExecutor();
    final output = StringBuffer();
    final errors = StringBuffer();
    final int exitCode;
    try {
      exitCode = await run.run(
        executable: executable,
        arguments: ScribeForgeCli.reportArguments(
          storePath: storePath,
          projectId: projectId,
          json: true,
        ),
        onOutput: output.write,
        onError: errors.write,
      );
    } on Object catch (error) {
      throw ProcessingReportLoadException(
        '无法启动处理报告命令：$error。请确认命令行工具路径正确后重试。',
      );
    }
    final text = output.toString().trim();
    if (exitCode != 0) {
      final detail = errors.toString().trim();
      throw ProcessingReportLoadException(
        '读取处理报告失败（退出码 $exitCode）。'
        '${detail.isEmpty ? '请确认项目已处理过，或先运行一次处理。' : detail}',
      );
    }
    if (text.isEmpty) {
      throw const ProcessingReportLoadException(
        '命令行工具没有返回报告内容；请确认项目存储目录中有已处理的项目。',
      );
    }
    try {
      return ProcessingReportSnapshot.fromJsonText(text);
    } on FormatException catch (error) {
      throw ProcessingReportLoadException(
        '报告内容无法解析：${error.message}。可以尝试重新运行处理后再查看。',
      );
    }
  }
}

class ProcessingReportLoadException implements Exception {
  const ProcessingReportLoadException(this.message);

  final String message;

  @override
  String toString() => message;
}
