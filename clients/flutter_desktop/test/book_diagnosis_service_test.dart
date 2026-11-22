import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/book_diagnosis_service.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';

const payload = '''
{
  "schema_version": 1,
  "source_sha256_matches_project": true,
  "source_physical_pages": 562,
  "recorded_source_count_matches": null,
  "imported_pages": 22,
  "missing_imported_count": 540,
  "missing_imported_pages_1based": [23, 24],
  "import_state": "failed",
  "both_ocr_succeeded_pages": 20,
  "verified_pages": 19,
  "failed_jobs": 2,
  "epub": {
    "spine_documents": 22,
    "source_page_markers": 22,
    "missing_source_page_marker_count": 540,
    "missing_source_page_markers_1based": [23, 24],
    "evidence_styles": ["unrecognized_document"]
  }
}
''';

class FakeExecutor implements ScribeForgeCliExecutor {
  FakeExecutor({this.output = payload, this.error = '', this.code = 0});
  final String output;
  final String error;
  final int code;
  List<String>? arguments;

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    this.arguments = arguments;
    onOutput(output);
    onError(error);
    return code;
  }

  @override
  Future<void> cancel() async {}
}

void main() {
  test('compares counts without equating EPUB documents to reader pages', () {
    final report = BookDiagnosis.fromJsonText(payload);
    expect(report.sourcePages, 562);
    expect(report.importedPages, 22);
    expect(report.missingImported, 540);
    expect(report.missingImportedPages, <int>[23, 24]);
    expect(report.importState, 'failed');
    expect(report.spineDocuments, 22);
    expect(report.hasUnrecognizedDocuments, isTrue);
    expect(report.recordedCountMatches, isNull);
  });

  test('rejects unsupported, malformed and unsafe result values', () {
    for (final value in <String>[
      '{}',
      payload.replaceFirst('"schema_version": 1', '"schema_version": 2'),
      payload.replaceFirst(
        '"source_physical_pages": 562',
        '"source_physical_pages": 0',
      ),
      payload.replaceFirst('[23, 24]', '[0]'),
      payload.replaceFirst(
        '"source_sha256_matches_project": true',
        '"source_sha256_matches_project": false',
      ),
    ]) {
      expect(() => BookDiagnosis.fromJsonText(value), throwsFormatException);
    }
  });

  test('invokes backend with separate paths, not a shell command', () async {
    final executor = FakeExecutor();
    final report =
        await BookDiagnosisService(
          executor: executor,
          executable: 'backend',
        ).diagnose(
          storePath: 'my store',
          sourcePath: 'my book.pdf',
          epubPath: 'my book.epub',
        );
    expect(report.sourcePages, 562);
    expect(executor.arguments, <String>[
      'diagnose-book',
      '--store',
      'my store',
      '--source',
      'my book.pdf',
      '--epub',
      'my book.epub',
    ]);
  });

  test('reports command failure and does not parse stale output', () async {
    final executor = FakeExecutor(code: 2, error: 'source SHA-256 mismatch');
    expect(
      () => BookDiagnosisService(
        executor: executor,
        executable: 'backend',
      ).diagnose(storePath: 'store', sourcePath: 'source', epubPath: 'book'),
      throwsA(isA<BookDiagnosisException>()),
    );
  });

  test('rejects bad JSON from a successful backend', () async {
    final executor = FakeExecutor(output: '{}');
    expect(
      () => BookDiagnosisService(
        executor: executor,
        executable: 'backend',
      ).diagnose(storePath: 'store', sourcePath: 'source', epubPath: 'book'),
      throwsA(isA<BookDiagnosisException>()),
    );
  });
}
