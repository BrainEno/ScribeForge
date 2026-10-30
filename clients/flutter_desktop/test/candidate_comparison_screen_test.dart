import 'dart:collection';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/epub_candidate.dart';
import 'package:scribeforge_desktop/screens/candidate_comparison_screen.dart';
import 'package:scribeforge_desktop/services/candidate_comparison_store.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';

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
        home: CandidateComparisonScreen(
          sourcePdfPath: sourcePath,
          storePath: storePath,
          cliExecutable: cliExecutable,
          canGenerateCandidates: true,
          cliExecutor: cli,
          filePicker: filePicker,
          comparisonStore: comparisonStore,
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

    expect(find.text('Shared opening line'), findsWidgets);
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
    expect(find.text('Candidate A reading'), findsOneWidget);
    expect(find.text('Candidate B reading'), findsOneWidget);

    await tester.tap(find.text('候选 A 更好'));
    await pumpUntil(
      tester,
      find.text('第 3 页 / 3'),
      description: 'automatic advance to the next unassessed page',
    );

    expect(comparisonStore.savedPageDecisions, <int, String>{1: 'a'});
    expect(comparisonStore.saveCount, 1);
    expect(comparisonStore.savedSourcePdfPath, sourcePath);
    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'export-candidates',
    ]);
    expect(filePicker.pickFileCalls, 0);
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
  Map<int, String> savedPageDecisions = <int, String>{};
  String? savedSourcePdfPath;
  int saveCount = 0;

  @override
  Future<EpubCandidate> loadCandidate(String path) async {
    final candidate = candidates[path];
    if (candidate == null) throw FormatException('Missing fake candidate: $path');
    return candidate;
  }

  @override
  Future<Map<String, dynamic>?> readEvaluation(String sourcePdfPath) async => null;

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
  }) async {
    savedPageDecisions = Map<int, String>.from(pageDecisions);
    savedSourcePdfPath = sourcePdfPath;
    saveCount++;
    return '$sourcePdfPath.scribeforge-review.json';
  }
}

class _FakeDesktopFilePicker implements DesktopFilePicker {
  int pickFileCalls = 0;

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    pickFileCalls++;
    return null;
  }

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async => null;
}
