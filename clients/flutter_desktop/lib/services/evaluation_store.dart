import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

class EvaluationStore {
  static String sidecarPath(String sourcePdfPath) =>
      '$sourcePdfPath.scribeforge-review.json';

  static Future<Map<String, dynamic>?> read(String sourcePdfPath) async {
    final file = File(sidecarPath(sourcePdfPath));
    if (!await file.exists()) return null;
    try {
      final raw = jsonDecode(await file.readAsString());
      if (raw is! Map<String, dynamic> || raw['schema_version'] != 1) return null;
      return raw;
    } on FormatException {
      return null;
    } on FileSystemException {
      return null;
    }
  }

  static Future<String> fingerprint(String path) async {
    final file = File(path);
    if (!await file.exists()) throw FileSystemException('File no longer exists', path);
    final digest = await sha256.bind(file.openRead()).first;
    return digest.toString();
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
  }) async {
    final sourceFingerprint = await fingerprint(sourcePdfPath);
    final candidateAHash = candidateAPath == null ? null : await fingerprint(candidateAPath);
    final candidateBHash = candidateBPath == null ? null : await fingerprint(candidateBPath);
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
      'policy': 'human_evaluation_only_no_automatic_quality_score',
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
