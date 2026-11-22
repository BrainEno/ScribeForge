import '../models/ocr_evidence.dart';
import 'cli_executor.dart';

class OcrEvidenceService {
  const OcrEvidenceService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;
  final String storePath;
  final int projectId;

  Future<OcrEvidenceBatch<OcrEvidencePage>> pages({int? after}) async {
    final request = OcrEvidenceRequest(OcrEvidenceSection.pages, after: after);
    final payload = await _read(request);
    return payload.batch('page_index', request, OcrEvidencePage.new);
  }

  Future<OcrPageOverview> overview(int page) async {
    final payload = await _read(
      OcrEvidenceRequest(OcrEvidenceSection.page, page: page),
    );
    return OcrPageOverview(payload.data);
  }

  Future<OcrEvidenceBatch<OcrEvidenceLine>> lines(
    int page,
    int run, {
    int? after,
  }) async {
    final request = OcrEvidenceRequest(
      OcrEvidenceSection.lines,
      page: page,
      run: run,
      after: after,
    );
    final payload = await _read(request);
    return payload.batch('line_index', request, OcrEvidenceLine.new);
  }

  Future<OcrEvidenceBatch<OcrEvidenceBlock>> blocks(
    int page,
    int run, {
    int? after,
  }) async {
    final request = OcrEvidenceRequest(
      OcrEvidenceSection.blocks,
      page: page,
      run: run,
      after: after,
    );
    final payload = await _read(request);
    return payload.batch('block_index', request, OcrEvidenceBlock.new);
  }

  Future<OcrEvidenceBatch<OcrEvidencePair>> pairs(
    int page,
    int run, {
    int? after,
  }) async {
    final request = OcrEvidenceRequest(
      OcrEvidenceSection.pairs,
      page: page,
      run: run,
      after: after,
    );
    final payload = await _read(request);
    return payload.batch('pair_index', request, OcrEvidencePair.new);
  }

  Future<OcrEvidencePayload> _read(OcrEvidenceRequest request) async {
    final output = StringBuffer();
    final errors = StringBuffer();
    const maxResponse = 3 * 1024 * 1024;
    var oversized = false;
    final exitCode = await executor.run(
      executable: executable,
      arguments: <String>[
        'ocr-evidence',
        '--store',
        storePath,
        '--project-id',
        '$projectId',
        '--section',
        request.section.name,
        if (request.page != null) ...<String>['--page', '${request.page}'],
        if (request.run != null) ...<String>['--run', '${request.run}'],
        if (request.after != null) ...<String>['--after', '${request.after}'],
        '--limit',
        '20',
      ],
      onOutput: (chunk) {
        if (oversized || output.length + chunk.length > maxResponse) {
          oversized = true;
          return;
        }
        output.write(chunk);
      },
      onError: (chunk) {
        final remaining = 4500 - errors.length;
        if (remaining <= 0) return;
        errors.write(
          chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
        );
      },
    );
    if (exitCode != 0) {
      throw OcrEvidenceReadFailure(exitCode, errors.toString().trim());
    }
    if (oversized) throw const FormatException('证据响应超过读取上限。');
    return OcrEvidencePayload.fromText(output.toString(), projectId, request);
  }
}

class OcrEvidenceReadFailure implements Exception {
  const OcrEvidenceReadFailure(this.exitCode, this.diagnostic);

  final int exitCode;
  final String diagnostic;

  @override
  String toString() => diagnostic.isEmpty
      ? '无法读取 OCR 证据（退出码 $exitCode）。'
      : '无法读取 OCR 证据（退出码 $exitCode）：$diagnostic';
}
