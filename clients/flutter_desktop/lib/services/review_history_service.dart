import '../models/review_history.dart';
import 'cli_executor.dart';

/// Reads evidence history in bounded batches, without invoking a write command.
class ReviewHistoryService {
  const ReviewHistoryService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;
  final String storePath;
  final int projectId;

  Future<ReviewHistoryBatch> list({
    ReviewHistoryFilter filter = const ReviewHistoryFilter(),
    int? snapshotId,
    int? beforeId,
  }) => _request(filter: filter, snapshotId: snapshotId, beforeId: beforeId);

  Future<ReviewHistoryRecord> detail(int id, {required int snapshotId}) async {
    final response = await _request(decisionId: id, snapshotId: snapshotId);
    if (response.records.length != 1 || response.records.single.id != id) {
      throw const FormatException('核心返回了另一条决策记录。');
    }
    return response.records.single;
  }

  Future<ReviewHistoryBatch> _request({
    ReviewHistoryFilter filter = const ReviewHistoryFilter(),
    int? snapshotId,
    int? beforeId,
    int? decisionId,
  }) async {
    final output = StringBuffer();
    final diagnostics = StringBuffer();
    var responseTooLarge = false;
    final code = await executor.run(
      executable: executable,
      arguments: <String>[
        'review-history',
        '--store',
        storePath,
        '--project-id',
        '$projectId',
        if (filter.pageIndex != null) ...<String>[
          '--page',
          '${filter.pageIndex}',
        ],
        if (filter.pairIndex != null) ...<String>[
          '--pair',
          '${filter.pairIndex}',
        ],
        if (filter.actor != null) ...<String>['--actor', filter.actor!.value],
        if (snapshotId != null) ...<String>['--snapshot-id', '$snapshotId'],
        if (beforeId != null) ...<String>['--before-id', '$beforeId'],
        if (decisionId != null) ...<String>['--decision-id', '$decisionId'],
        '--limit',
        '25',
      ],
      onOutput: (chunk) {
        if (responseTooLarge || output.length + chunk.length > 1024 * 1024) {
          responseTooLarge = true;
        } else {
          output.write(chunk);
        }
      },
      onError: (chunk) {
        final remaining = 5000 - diagnostics.length;
        if (remaining > 0)
          diagnostics.write(
            chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
          );
      },
    );
    if (code != 0) {
      final message = diagnostics.toString().trim();
      throw ReviewHistoryFailure(
        code,
        message.isEmpty ? '处理核心未返回决策历史。' : message,
      );
    }
    if (responseTooLarge) throw const FormatException('决策历史响应超过读取上限。');
    final result = ReviewHistoryBatch.fromText(output.toString());
    if (result.projectId != projectId ||
        (snapshotId != null && result.snapshotId != snapshotId)) {
      throw const FormatException('项目或历史快照已变化，请重新读取。');
    }
    for (final row in result.records) {
      if ((beforeId != null && row.id >= beforeId) ||
          (filter.pageIndex != null && row.pageIndex != filter.pageIndex) ||
          (filter.pairIndex != null && row.pairIndex != filter.pairIndex) ||
          (filter.actor != null && row.actor != filter.actor!.value)) {
        throw const FormatException('决策历史响应不符合请求范围。');
      }
    }
    return result;
  }
}

class ReviewHistoryFailure implements Exception {
  const ReviewHistoryFailure(this.exitCode, this.message);

  final int exitCode;
  final String message;

  @override
  String toString() => '读取决策历史失败（退出码 $exitCode）：$message';
}
