import '../models/evidence_search.dart';
import 'cli_executor.dart';

/// A project-scoped read boundary. Query text never enters a shell or a log.
class EvidenceSearchService {
  const EvidenceSearchService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;
  final String storePath;
  final int projectId;

  Future<EvidenceSearchBatch> search(
    EvidenceSearchQuery query, {
    int? snapshot,
    int? before,
  }) => _request(query, snapshot: snapshot, before: before);

  Future<EvidenceSearchHit> detail(
    EvidenceSearchQuery query,
    EvidenceSearchHit preview, {
    required int snapshot,
  }) async {
    final result = await _request(
      query,
      snapshot: snapshot,
      record: preview.id,
    );
    final hit = result.hits.singleOrNull;
    if (hit == null || hit.id != preview.id || hit.page != preview.page) {
      throw const FormatException('详细记录与所选搜索结果不一致。');
    }
    return hit;
  }

  Future<EvidenceSearchBatch> _request(
    EvidenceSearchQuery query, {
    int? snapshot,
    int? before,
    int? record,
  }) async {
    final output = StringBuffer();
    final diagnostic = StringBuffer();
    const byteBudget = 1024 * 1024;
    var tooLarge = false;
    final code = await executor.run(
      executable: executable,
      arguments: <String>[
        'evidence-search',
        '--store',
        storePath,
        '--project-id',
        '$projectId',
        '--source',
        query.source.name,
        '--term=${query.term}',
        if (query.pageStart != null) ...<String>[
          '--page-start',
          '${query.pageStart}',
        ],
        if (query.pageEnd != null) ...<String>[
          '--page-end',
          '${query.pageEnd}',
        ],
        if (query.actor != null) ...<String>['--actor', query.actor!.value],
        if (snapshot != null) ...<String>['--snapshot-id', '$snapshot'],
        if (before != null) ...<String>['--before-id', '$before'],
        if (record != null) ...<String>['--record-id', '$record'],
        '--limit',
        '20',
      ],
      onOutput: (chunk) {
        if (tooLarge || output.length + chunk.length > byteBudget) {
          tooLarge = true;
          return;
        }
        output.write(chunk);
      },
      onError: (chunk) {
        final remaining = 3000 - diagnostic.length;
        if (remaining > 0) {
          diagnostic.write(
            chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
          );
        }
      },
    );
    if (code != 0)
      throw EvidenceSearchFailure(code, diagnostic.toString().trim());
    if (tooLarge) throw const FormatException('搜索响应超过读取上限。');
    return EvidenceSearchBatch.fromText(
      output.toString(),
      projectId: projectId,
      query: query,
      requestedSnapshot: snapshot,
      before: before,
    );
  }
}

class EvidenceSearchFailure implements Exception {
  const EvidenceSearchFailure(this.exitCode, this.diagnostic);

  final int exitCode;
  final String diagnostic;

  @override
  String toString() => diagnostic.isEmpty
      ? '搜索失败（退出码 $exitCode）。'
      : '搜索失败（退出码 $exitCode）：$diagnostic';
}
