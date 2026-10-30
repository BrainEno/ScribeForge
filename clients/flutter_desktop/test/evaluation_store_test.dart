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
      expect(await EvaluationStore.read(pdf.path), isNotNull);
    });

    test('refuses to save ratings when candidate bytes changed after loading',
        () async {
      final pdf = File('${temp.path}/original.pdf');
      final a = File('${temp.path}/candidate-a.epub');
      final b = File('${temp.path}/candidate-b.epub');
      await pdf.writeAsBytes([0, 1, 2, 3]);
      await a.writeAsBytes([4, 5, 6]);
      await b.writeAsBytes([7, 8, 9]);

      await expectLater(
        EvaluationStore.save(
          sourcePdfPath: pdf.path,
          candidateAPath: a.path,
          candidateBPath: b.path,
          candidateALabel: 'MinerU',
          candidateBLabel: 'PaddleOCR',
          pageDecisions: const {0: 'a'},
          overallWinner: null,
          notes: '',
          expectedSourceSha256: await EvaluationStore.fingerprint(pdf.path),
          expectedCandidateASha256: List<String>.filled(64, '0').join(),
          expectedCandidateBSha256: await EvaluationStore.fingerprint(b.path),
        ),
        throwsA(isA<StateError>().having(
          (error) => error.message,
          'message',
          contains('candidate A changed'),
        )),
      );
      expect(await File(EvaluationStore.sidecarPath(pdf.path)).exists(), isFalse);
    });

    test('ignores malformed or unsupported review sidecars', () async {
      final pdf = File('${temp.path}/original.pdf');
      await pdf.writeAsBytes([1]);
      await File(EvaluationStore.sidecarPath(pdf.path)).writeAsString('{broken');

      expect(await EvaluationStore.read(pdf.path), isNull);
    });

    test('rejects valid JSON with invalid review schema fields', () async {
      final pdf = File('${temp.path}/original.pdf');
      await pdf.writeAsBytes([1, 2, 3]);
      final sidecarPath = await EvaluationStore.save(
        sourcePdfPath: pdf.path,
        candidateAPath: null,
        candidateBPath: null,
        candidateALabel: 'Candidate A',
        candidateBLabel: 'Candidate B',
        pageDecisions: const {0: 'a'},
        overallWinner: 'a',
        notes: 'Looks good.',
      );
      final file = File(sidecarPath);
      final document =
          jsonDecode(await file.readAsString()) as Map<String, dynamic>;

      (document['page_decisions'] as Map<String, dynamic>)['0'] =
          'unknown-choice';
      await file.writeAsString(jsonEncode(document));
      expect(await EvaluationStore.read(pdf.path), isNull);

      (document['page_decisions'] as Map<String, dynamic>)['0'] = 'a';
      document['notes'] = 7;
      await file.writeAsString(jsonEncode(document));
      expect(await EvaluationStore.read(pdf.path), isNull);

      document['notes'] = 'Looks good.';
      (document['source_pdf'] as Map<String, dynamic>)['sha256'] = 'bad-hash';
      await file.writeAsString(jsonEncode(document));
      expect(await EvaluationStore.read(pdf.path), isNull);
    });
  });
}
