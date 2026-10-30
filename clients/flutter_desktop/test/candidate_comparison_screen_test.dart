import 'dart:collection';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/epub_candidate.dart';
import 'package:scribeforge_desktop/screens/candidate_comparison_screen.dart';
import 'package:scribeforge_desktop/services/candidate_comparison_store.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';

void main() {
  const sourcePath = '/books/Source.pdf';
  const storePath = '/books/Source.scribeforge';
  const cliExecutable = '/tools/scribeforge';

  late _FakeCliExecutor cli;
  late _FakeCandidateComparisonStore comparisonStore;
  late _FakeDesktopFilePicker filePicker;
  late String outputDirectory;

  Future<void> pumpScreen(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1440, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CandidateComparisonScreen(
            sourcePdfPath: sourcePath,
            storePath: storePath,
            cliExecutable: cliExecutable,
            canGenerateCandidates: true,
            cliExecutor: cli,
            filePicker: filePicker,
            comparisonStore: comparisonStore,
          ),
        ),
      ),
    );
    await tester.pump();
  }

  Future<void> pumpUntil(
    WidgetTester tester,
    Finder finder, {
    required String description,
  }) async {
    for (var attempt = 0; attempt < 100; attempt++) {
      await tester.pump(const Duration(milliseconds: 50));
      if (finder.evaluate().isNotEmpty) return;
    }
    fail('Timed out waiting for $description.');
  }

  setUp(() {
    outputDirectory = ScribeForgeCli.candidateOutputDirectoryForPdf(sourcePath);
    final candidateAPath = ScribeForgeCli.candidateEpubPath(
      outputDirectory,
      'mineru',
    );
    final candidateBPath = ScribeForgeCli.candidateEpubPath(
      outputDirectory,
      'paddleocr',
    );
    cli = _FakeCliExecutor();
    filePicker = _FakeDesktopFilePicker();
    comparisonStore = _FakeCandidateComparisonStore(
      candidates: <String, EpubCandidate>{
        candidateAPath: _candidate(
          candidateAPath,
          title: 'Source — unverified (mineru)',
          engine: 'mineru',
          pageTexts: const <String>[
            'Shared opening line',
            'Candidate A reading',
            'Shared closing line',
          ],
        ),
        candidateBPath: _candidate(
          candidateBPath,
          title: 'Source — unverified (paddleocr)',
          engine: 'paddleocr',
          pageTexts: const <String>[
            'Shared opening line',
            'Candidate B reading',
            'Shared closing line',
          ],
        ),
      },
    );
  });

  testWidgets(
      'compares generated candidates, saves separate page evaluations, and jumps to differences',
      (tester) async {
    await pumpScreen(tester);

    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the first source page comparison',
    );

    expect(find.text('Shared opening line', findRichText: true), findsWidgets);
    expect(find.textContaining('A/B 评测不会核准 OCR 内容'), findsOneWidget);
    expect(
      cli.calls.single,
      ScribeForgeCli.exportCandidateArguments(
        storePath: storePath,
        outputDirectory: outputDirectory,
      ),
    );

    await tester.tap(find.byTooltip('下一处差异'));
    await tester.pump();
    expect(find.text('第 2 页 / 3'), findsOneWidget);
    expect(find.text('Candidate A reading', findRichText: true), findsOneWidget);
    expect(find.text('Candidate B reading', findRichText: true), findsOneWidget);

    await tester.tap(find.text('候选 A 更好'));
    await pumpUntil(
      tester,
      find.text('第 3 页 / 3'),
      description: 'automatic advance to the next unassessed page',
    );

    expect(comparisonStore.savedPageDecisions, <int, String>{1: 'a'});
    expect(comparisonStore.saveCount, 1);
    expect(comparisonStore.savedExpectedSourceSha256,
        List<String>.filled(64, 'c').join());
    expect(comparisonStore.savedExpectedCandidateASha256,
        comparisonStore.candidates.values.first.sha256);
    expect(comparisonStore.savedExpectedCandidateBSha256,
        comparisonStore.candidates.values.elementAt(1).sha256);

    await tester.tap(find.text('候选 B 更好'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'wraparound to the first page after the second explicit rating',
    );
    expect(comparisonStore.savedPageDecisions, <int, String>{1: 'a', 2: 'b'});
    expect(comparisonStore.saveCount, 2);
    expect(comparisonStore.savedSourcePdfPath, sourcePath);
    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'export-candidates',
    ]);
    expect(filePicker.pickFileCalls, 0);
  });

  testWidgets('persists whole-book evaluation and notes in the sidecar',
      (tester) async {
    await pumpScreen(tester);

    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the first source page comparison',
    );
    await tester.ensureVisible(find.text('保存整本书评价与备注'));
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('候选 A 更优').last);
    await tester.pump();
    await tester.enterText(
      find.byKey(const ValueKey('candidate-evaluation-notes')),
      'Preserves punctuation on the title page.',
    );
    await tester.tap(find.text('保存整本书评价与备注'));
    await pumpUntil(
      tester,
      find.textContaining('候选评测已保存'),
      description: 'the saved whole-book evaluation',
    );

    expect(comparisonStore.savedOverallWinner, 'a');
    expect(comparisonStore.savedNotes, 'Preserves punctuation on the title page.');
    expect(comparisonStore.savedPageDecisions, isEmpty);
  });

  testWidgets('supports tie and deferred ratings without accepting OCR text',
      (tester) async {
    await pumpScreen(tester);

    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the first source page comparison',
    );

    await tester.tap(find.text('两者相当'));
    await pumpUntil(
      tester,
      find.text('第 2 页 / 3'),
      description: 'advance after an explicit tie rating',
    );
    expect(comparisonStore.savedPageDecisions, <int, String>{0: 'tie'});

    await tester.tap(find.text('稍后再评'));
    await pumpUntil(
      tester,
      find.text('第 3 页 / 3'),
      description: 'advance after deferring the page',
    );
    expect(comparisonStore.savedPageDecisions, <int, String>{
      0: 'tie',
      1: 'review',
    });
    expect(find.text('1 / 3 页已完成 · 1 页暂缓'), findsOneWidget);
    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'export-candidates',
    ]);
  });

  testWidgets('restores only evaluations bound to the current source and candidates',
      (tester) async {
    final candidateAPath = comparisonStore.candidates.keys.first;
    final candidateBPath = comparisonStore.candidates.keys.elementAt(1);
    final candidateA = comparisonStore.candidates[candidateAPath]!;
    final candidateB = comparisonStore.candidates[candidateBPath]!;
    comparisonStore.evaluation = <String, dynamic>{
      'source_pdf': <String, String>{
        'path': sourcePath,
        'sha256': List<String>.filled(64, 'c').join(),
      },
      'candidate_a': <String, String>{
        'path': candidateAPath,
        'sha256': candidateA.sha256,
      },
      'candidate_b': <String, String>{
        'path': candidateBPath,
        'sha256': candidateB.sha256,
      },
      'page_decisions': <String, String>{'1': 'b'},
      'overall_winner': 'a',
      'notes': 'Existing comparison notes',
    };
    await pumpScreen(tester);

    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the first source page comparison',
    );
    await tester.tap(find.byTooltip('下一处差异'));
    await tester.pump();

    expect(find.text('本页：候选 B 更好'), findsOneWidget);
    expect(find.text('Existing comparison notes'), findsOneWidget);
    expect(find.text('候选 A 更优'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    (comparisonStore.evaluation!['candidate_a'] as Map<String, dynamic>)
        ['sha256'] = List<String>.filled(64, '0').join();
    await pumpScreen(tester);
    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the comparison with a changed candidate fingerprint',
    );
    await tester.tap(find.byTooltip('下一处差异'));
    await tester.pump();
    expect(find.text('本页：候选 B 更好'), findsNothing);
    expect(find.text('Existing comparison notes'), findsNothing);
  });

  testWidgets('manually loads two EPUB candidates without invoking the CLI',
      (tester) async {
    final paths = comparisonStore.candidates.keys.toList(growable: false);
    filePicker.selections.addAll(paths);
    await pumpScreen(tester);

    await tester.tap(find.text('选择候选 A'));
    await pumpUntil(
      tester,
      find.textContaining('Source — unverified (mineru)'),
      description: 'the manually loaded first candidate',
    );
    await tester.tap(find.text('选择候选 B'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the manually loaded candidate comparison',
    );

    expect(filePicker.pickFileCalls, 2);
    expect(cli.calls, isEmpty);
  });

  testWidgets('candidate export failures remain visible and can be retried',
      (tester) async {
    cli.results.addAll(<int>[2, 0]);
    await pumpScreen(tester);

    await tester.tap(find.text('生成 OCR 候选 EPUB'));
    await pumpUntil(
      tester,
      find.textContaining('simulated candidate export failure'),
      description: 'the candidate export failure',
    );
    expect(find.text('重试生成候选'), findsOneWidget);

    await tester.tap(find.text('重试生成候选'));
    await pumpUntil(
      tester,
      find.text('第 1 页 / 3'),
      description: 'the candidate comparison after retry',
    );

    expect(
      cli.calls.where((arguments) => arguments.first == 'export-candidates'),
      hasLength(2),
    );
  });
}

EpubCandidate _candidate(
  String path, {
  required String title,
  required String engine,
  required List<String> pageTexts,
}) =>
    EpubCandidate(
      path: path,
      title: title,
      creator: engine,
      pages: List<EpubPage>.generate(
        pageTexts.length,
        (index) => EpubPage(
          spineIndex: index,
          sourcePageIndex: index,
          title: 'Page ${index + 1}',
          text: pageTexts[index],
        ),
      ),
      isSourcePageAligned: true,
      sha256: List<String>.filled(64, engine == 'mineru' ? 'a' : 'b').join(),
    );

class _FakeCliExecutor implements ScribeForgeCliExecutor {
  final Queue<int> results = Queue<int>();
  final List<List<String>> calls = <List<String>>[];

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    calls.add(List<String>.unmodifiable(arguments));
    final result = results.isEmpty ? 0 : results.removeFirst();
    if (result == 0) {
      onOutput('candidate export complete');
    } else {
      onError('simulated candidate export failure');
    }
    return result;
  }
}

class _FakeCandidateComparisonStore implements CandidateComparisonStore {
  _FakeCandidateComparisonStore({required this.candidates});

  final Map<String, EpubCandidate> candidates;
  Map<String, dynamic>? evaluation;
  Map<int, String> savedPageDecisions = <int, String>{};
  String? savedSourcePdfPath;
  String? savedOverallWinner;
  String? savedNotes;
  String? savedExpectedSourceSha256;
  String? savedExpectedCandidateASha256;
  String? savedExpectedCandidateBSha256;
  int saveCount = 0;

  @override
  Future<EpubCandidate> loadCandidate(String path) async {
    final candidate = candidates[path];
    if (candidate == null) throw FormatException('Missing fake candidate: $path');
    return candidate;
  }

  @override
  Future<Map<String, dynamic>?> readEvaluation(String sourcePdfPath) async =>
      evaluation;

  @override
  Future<String> fingerprint(String path) async =>
      List<String>.filled(64, 'c').join();

  @override
  Future<String> saveEvaluation({
    required String sourcePdfPath,
    required String candidateAPath,
    required String candidateBPath,
    required String candidateALabel,
    required String candidateBLabel,
    required Map<int, String> pageDecisions,
    required String? overallWinner,
    required String notes,
    required String expectedSourceSha256,
    required String expectedCandidateASha256,
    required String expectedCandidateBSha256,
  }) async {
    savedPageDecisions = Map<int, String>.from(pageDecisions);
    savedSourcePdfPath = sourcePdfPath;
    savedOverallWinner = overallWinner;
    savedNotes = notes;
    savedExpectedSourceSha256 = expectedSourceSha256;
    savedExpectedCandidateASha256 = expectedCandidateASha256;
    savedExpectedCandidateBSha256 = expectedCandidateBSha256;
    saveCount++;
    return '$sourcePdfPath.scribeforge-review.json';
  }
}

class _FakeDesktopFilePicker implements DesktopFilePicker {
  final Queue<String?> selections = Queue<String?>();
  int pickFileCalls = 0;

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    pickFileCalls++;
    return selections.isEmpty ? null : selections.removeFirst();
  }

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async => null;
}
