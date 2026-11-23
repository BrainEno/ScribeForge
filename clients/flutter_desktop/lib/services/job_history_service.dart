import '../models/job_attempt_history.dart';
import 'cli_executor.dart';

/// Read-only, bounded cursor query. Never initializes or upgrades the store.
class JobHistoryService {
  const JobHistoryService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
  });
  final ScribeForgeCliExecutor executor;
  final String executable, storePath;
  final int projectId;

  Future<JobHistoryBatch> list({
    int? pageIndex,
    String? stage,
    int? before,
  }) async {
    if (pageIndex != null && pageIndex < 0 ||
        before != null && before <= 0 ||
        stage != null && !jobStageLabels.containsKey(stage)) {
      throw ArgumentError('无效的失败历史筛选条件。');
    }
    final output = StringBuffer(), diagnostic = StringBuffer();
    var oversized = false;
    final code = await executor.run(
      executable: executable,
      arguments: [
        'job-history',
        '--store',
        storePath,
        '--project-id',
        '$projectId',
        if (pageIndex != null) ...['--page-index', '$pageIndex'],
        if (stage != null) ...['--stage', stage],
        if (before != null) ...['--before', '$before'],
        '--limit',
        '25',
      ],
      onOutput: (chunk) {
        if (output.length + chunk.length > 1024 * 1024) {
          oversized = true;
          return;
        }
        if (!oversized) output.write(chunk);
      },
      onError: (chunk) {
        final remaining = 4000 - diagnostic.length;
        if (remaining > 0)
          diagnostic.write(
            chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
          );
      },
    );
    if (code != 0) throw JobHistoryFailure(code, diagnostic.toString().trim());
    if (oversized) throw const FormatException('失败历史响应超过读取上限。');
    final result = JobHistoryBatch.fromText(output.toString());
    if (result.projectId != projectId ||
        result.rows.length > 25 ||
        result.rows.any(
          (row) =>
              (before != null && row.id >= before) ||
              (pageIndex != null && row.pageIndex != pageIndex) ||
              (stage != null && row.stage != stage),
        )) {
      throw const FormatException('失败历史响应不符合所选项目或筛选条件。');
    }
    return result;
  }
}

class JobHistoryFailure implements Exception {
  const JobHistoryFailure(this.code, this.detail);
  final int code;
  final String detail;
  @override
  String toString() =>
      '读取失败历史失败（退出码 $code）${detail.isEmpty ? '，处理核心未提供详情。' : '：$detail'}';
}
