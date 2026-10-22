import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/evaluation_store.dart';

void main() {
  group('EvaluationStore', () {
    late Directory temp;

    setUp(() async {
      temp = await Directory.systemTemp.createTemp('scribeforge-review-test-');
    });

    tearDown(() async {
      if (await temp.exists()) await temp.delete(recursive: true);
    });

    test('writes human choices and SHA-256 fingerprints to a sidecar', () async {
      final pdf = File('${temp.path}/original.pdf');
      final a = File('${temp.path}/candidate-a.epub');
      final b = File('${temp.path}/candidate-b.epub');
      await pdf.writeAsBytes([0, 1, 2, 3]);
      await a.writeAsBytes([4, 5, 6]);
      await b.writeAsBytes([7, 8, 9]);

      final path = await EvaluationStore.save(
        sourcePdfPath: pdf.path,
        candidateAPath: a.path,
        candidateBPath: b.path,
        candidateALabel: 'MinerU',
        candidateBLabel: 'PaddleOCR',
        pageDecisions: const {0: 'a', 4: 'tie'},
        overallWinner: 'b',
        notes: 'Candidate B preserves more punctuation.',
      );

      final document = jsonDecode(await File(path).readAsString()) as Map<String, dynamic>;
      expect(path, EvaluationStore.sidecarPath(pdf.path));
      expect((document['source_pdf'] as Map)['sha256'], hasLength(64));
      expect((document['candidate_a'] as Map)['sha256'], hasLength(64));
      expect((document['candidate_b'] as Map)['sha256'], hasLength(64));
      expect(document['page_decisions'], {'0': 'a', '4': 'tie'});
      expect(document['overall_winner'], 'b');
      expect(document['policy'], 'human_evaluation_only_no_automatic_quality_score');
    });

    test('ignores malformed or unsupported review sidecars', () async {
      final pdf = File('${temp.path}/original.pdf');
      await pdf.writeAsBytes([1]);
      await File(EvaluationStore.sidecarPath(pdf.path)).writeAsString('{broken');

      expect(await EvaluationStore.read(pdf.path), isNull);
    });
  });
}
