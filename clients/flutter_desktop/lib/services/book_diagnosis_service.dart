import 'dart:convert';

import 'cli_executor.dart';

/// A read-only comparison, not a claim that the EPUB is correct or complete.
class BookDiagnosis {
  const BookDiagnosis({
    required this.sourcePages,
    required this.importedPages,
    required this.missingImported,
    required this.ocrPages,
    required this.verifiedPages,
    required this.failedJobs,
    required this.importState,
    required this.spineDocuments,
    required this.pageMarkers,
    required this.missingMarkers,
    required this.markerStyles,
    required this.missingImportedPages,
    required this.missingMarkerPages,
    required this.recordedCountMatches,
  });

  final int sourcePages;
  final int importedPages;
  final int missingImported;
  final int ocrPages;
  final int verifiedPages;
  final int failedJobs;
  final String? importState;
  final int spineDocuments;
  final int pageMarkers;
  final int missingMarkers;
  final List<String> markerStyles;
  final List<int> missingImportedPages;
  final List<int> missingMarkerPages;
  final bool? recordedCountMatches;

  bool get hasUnrecognizedDocuments =>
      markerStyles.contains('unrecognized_document');

  static BookDiagnosis fromJsonText(String raw) {
    final decoded = jsonDecode(raw);
    if (decoded is! Map || decoded['schema_version'] != 1) {
      throw const FormatException('不支持的诊断结果版本');
    }
    final data = Map<String, dynamic>.from(decoded);
    final epub = data['epub'];
    if (epub is! Map || data['source_sha256_matches_project'] != true) {
      throw const FormatException('诊断结果缺少 EPUB 或源文件校验信息');
    }
    final book = Map<String, dynamic>.from(epub);
    int number(Map<String, dynamic> map, String key, {bool positive = false}) {
      final value = map[key];
      if (value is! int || value < (positive ? 1 : 0)) {
        throw FormatException('诊断结果中的 $key 无效');
      }
      return value;
    }

    List<int> pageList(Map<String, dynamic> map, String key) {
      final value = map[key];
      if (value is! List || value.any((page) => page is! int || page < 1)) {
        throw FormatException('诊断结果中的 $key 无效');
      }
      return List<int>.unmodifiable(value.cast<int>());
    }

    final styles = book['evidence_styles'];
    final importState = data['import_state'];
    final countMatches = data['recorded_source_count_matches'];
    if (styles is! List ||
        styles.any((value) => value is! String) ||
        (importState != null && importState is! String) ||
        (countMatches != null && countMatches is! bool)) {
      throw const FormatException('诊断结果字段无效');
    }
    return BookDiagnosis(
      sourcePages: number(data, 'source_physical_pages', positive: true),
      importedPages: number(data, 'imported_pages'),
      missingImported: number(data, 'missing_imported_count'),
      ocrPages: number(data, 'both_ocr_succeeded_pages'),
      verifiedPages: number(data, 'verified_pages'),
      failedJobs: number(data, 'failed_jobs'),
      importState: importState as String?,
      spineDocuments: number(book, 'spine_documents', positive: true),
      pageMarkers: number(book, 'source_page_markers'),
      missingMarkers: number(book, 'missing_source_page_marker_count'),
      markerStyles: List<String>.unmodifiable(styles.cast<String>()),
      missingImportedPages: pageList(data, 'missing_imported_pages_1based'),
      missingMarkerPages: pageList(book, 'missing_source_page_markers_1based'),
      recordedCountMatches: countMatches as bool?,
    );
  }
}

class BookDiagnosisService {
  const BookDiagnosisService({
    required this.executor,
    required this.executable,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;

  Future<BookDiagnosis> diagnose({
    required String storePath,
    required String sourcePath,
    required String epubPath,
  }) async {
    final output = StringBuffer();
    final errors = StringBuffer();
    final int code;
    try {
      code = await executor.run(
        executable: executable,
        arguments: <String>[
          'diagnose-book',
          '--store',
          storePath,
          '--source',
          sourcePath,
          '--epub',
          epubPath,
        ],
        onOutput: output.write,
        onError: errors.write,
      );
    } on Object catch (error) {
      throw BookDiagnosisException('无法启动诊断：$error');
    }
    if (code != 0) {
      final detail = errors.toString().trim();
      throw BookDiagnosisException(
        '诊断失败（退出码 $code）。${detail.isEmpty ? '请检查项目和文件。' : detail}',
      );
    }
    try {
      return BookDiagnosis.fromJsonText(output.toString());
    } on FormatException catch (error) {
      throw BookDiagnosisException('诊断结果无法解析：${error.message}');
    }
  }
}

class BookDiagnosisException implements Exception {
  const BookDiagnosisException(this.message);
  final String message;
  @override
  String toString() => message;
}
