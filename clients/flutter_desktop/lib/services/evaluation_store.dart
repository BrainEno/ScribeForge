import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

class EvaluationStore {
  static String sidecarPath(String sourcePdfPath) =>
      '$sourcePdfPath.scribeforge-review.json';

  static const _validDecisions = <String>{'a', 'b', 'tie', 'review'};
  static const _policy = 'human_evaluation_only_no_automatic_quality_score';

  static Future<Map<String, dynamic>?> read(String sourcePdfPath) async {
    final file = File(sidecarPath(sourcePdfPath));
    if (!await file.exists()) {
      return null;
    }
    try {
      final raw = jsonDecode(await file.readAsString());
      if (raw is! Map<String, dynamic> || !_isValidSidecar(raw)) {
        return null;
      }
      return raw;
    } on FormatException {
      return null;
    } on FileSystemException {
      return null;
    }
  }

  static bool _isValidSidecar(Map<String, dynamic> raw) {
    if (raw['schema_version'] != 1) return false;

    final updatedAt = raw['updated_at'];
    if (updatedAt is! String || DateTime.tryParse(updatedAt) == null) {
      return false;
    }

    final source = raw['source_pdf'];
    if (source is! Map ||
        !_hasText(source['path']) ||
        !_isSha256(source['sha256'])) {
      return false;
    }

    if (!_isValidCandidate(raw['candidate_a']) ||
        !_isValidCandidate(raw['candidate_b'])) {
      return false;
    }

    final decisions = raw['page_decisions'];
    if (decisions is! Map) return false;
    for (final entry in decisions.entries) {
      final key = entry.key;
      final value = entry.value;
      if (key is! String || value is! String) return false;
      final page = int.tryParse(key);
      if (page == null ||
          page < 0 ||
          page.toString() != key ||
          !_validDecisions.contains(value)) {
        return false;
      }
    }

    final winner = raw['overall_winner'];
    if (winner != null && !_validDecisions.contains(winner)) return false;
    if (raw['notes'] is! String || raw['policy'] != _policy) return false;
    return true;
  }

  static bool _isValidCandidate(Object? value) {
    if (value == null) return true;
    if (value is! Map) return false;
    return _hasText(value['path']) &&
        _isSha256(value['sha256']) &&
        value['label'] is String;
  }

  static bool _hasText(Object? value) =>
      value is String && value.trim().isNotEmpty;

  static bool _isSha256(Object? value) {
    if (value is! String || value.length != 64) return false;
    for (final codeUnit in value.codeUnits) {
      final isDigit = codeUnit >= 0x30 && codeUnit <= 0x39;
      final isLowerHex = codeUnit >= 0x61 && codeUnit <= 0x66;
      if (!isDigit && !isLowerHex) return false;
    }
    return true;
  }

  static Future<String> fingerprint(String path) async {
    final file = File(path);
    if (!await file.exists()) {
      throw FileSystemException('File no longer exists', path);
    }
    final digest = await sha256.bind(file.openRead()).first;
    return digest.toString();
  }

  static void _requireExpectedFingerprint(
    String label,
    String path,
    String actual,
    String? expected,
  ) {
    if (expected != null && expected != actual) {
      throw StateError('$label changed after it was loaded: $path');
    }
  }

  static Future<String> save({
    required String sourcePdfPath,
    required String? candidateAPath,
    required String? candidateBPath,
    required String candidateALabel,
    required String candidateBLabel,
    required Map<int, String> pageDecisions,
    required String? overallWinner,
    required String notes,
    String? expectedSourceSha256,
    String? expectedCandidateASha256,
    String? expectedCandidateBSha256,
  }) async {
    final sourceFingerprint = await fingerprint(sourcePdfPath);
    _requireExpectedFingerprint(
      'source PDF',
      sourcePdfPath,
      sourceFingerprint,
      expectedSourceSha256,
    );
    final candidateAHash = candidateAPath == null ? null : await fingerprint(candidateAPath);
    if (candidateAPath != null) {
      _requireExpectedFingerprint(
        'candidate A',
        candidateAPath,
        candidateAHash!,
        expectedCandidateASha256,
      );
    }
    final candidateBHash = candidateBPath == null ? null : await fingerprint(candidateBPath);
    if (candidateBPath != null) {
      _requireExpectedFingerprint(
        'candidate B',
        candidateBPath,
        candidateBHash!,
        expectedCandidateBSha256,
      );
    }
    final now = DateTime.now().toUtc();
    final payload = <String, Object?>{
      'schema_version': 1,
      'updated_at': now.toIso8601String(),
      'source_pdf': {
        'path': sourcePdfPath,
        'sha256': sourceFingerprint,
      },
      'candidate_a': candidateAPath == null
          ? null
          : {
              'path': candidateAPath,
              'sha256': candidateAHash,
              'label': candidateALabel,
            },
      'candidate_b': candidateBPath == null
          ? null
          : {
              'path': candidateBPath,
              'sha256': candidateBHash,
              'label': candidateBLabel,
            },
      'page_decisions': {
        for (final entry in pageDecisions.entries)
          entry.key.toString(): entry.value,
      },
      'overall_winner': overallWinner,
      'notes': notes,
      'policy': _policy,
    };

    final destination = File(sidecarPath(sourcePdfPath));
    await destination.parent.create(recursive: true);
    final temporary = File('${destination.path}.tmp');
    try {
      await temporary.writeAsString(
        '${const JsonEncoder.withIndent('  ').convert(payload)}\n',
        flush: true,
      );
      await temporary.rename(destination.path);
    } finally {
      if (await temporary.exists()) await temporary.delete();
    }
    return destination.path;
  }
}
