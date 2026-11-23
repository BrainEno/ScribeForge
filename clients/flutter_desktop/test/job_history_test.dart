import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/job_attempt_history.dart';
import 'package:scribeforge_desktop/models/physical_page_selection.dart';
import 'package:scribeforge_desktop/screens/jobs/job_history_screen.dart';
import 'package:scribeforge_desktop/screens/jobs/scoped_retry_dialog.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';
import 'package:scribeforge_desktop/services/job_history_service.dart';

Map<String, dynamic> row(
  int id, {
  int? page = 17,
  String stage = 'primary_ocr',
  String outcome = 'failed',
  String state = 'failed',
  String origin = 'observed',
}) => {
  'id': id,
  'job_id': id + 100,
  'attempt': 2,
  'outcome': outcome,
  'origin': origin,
  'stage': stage,
  'current_state': state,
  'page_index': page,
  'error_preview': outcome == 'failed' ? 'worker failure $id' : null,
  'error_truncated': false,
  'started_at': null,
  'finished_at': null,
  'recorded_at': '2026-11-20 12:00:00',
};
String batch(
  List<Map<String, dynamic>> rows, {
  int? before,
  bool available = true,
  int project = 1,
}) => jsonEncode({
  'schema_version': 1,
  'project_id': project,
  'history_available': available,
  'rows': rows,
  'next_before': before,
  'notice': 'private',
});

void main() {
  test(
    'physical page parsing converts one-based numbers and rejects ambiguities',
    () {
      expect(parsePhysicalPages('18, 374，1', sourcePages: 562), [17, 373, 0]);
      for (final input in [
        '',
        '0',
        '-1',
        '1-3',
        'x',
        '3,3',
        '1,563',
        '999999999999999999999',
      ]) {
        expect(
          () => parsePhysicalPages(input, sourcePages: 562),
          throwsFormatException,
          reason: input,
        );
      }
      expect(
        () =>
            parsePhysicalPages(List.generate(101, (i) => '${i + 1}').join(',')),
        throwsFormatException,
      );
      expect(parsePhysicalPages('18、374'), [17, 373]);
    },
  );

  test('scoped arguments carry only requested zero-based page indices', () {
    expect(
      ScribeForgeCli.processArguments(
        pdfPath: '/a.pdf',
        storePath: '/a.scribeforge',
        pageIndexes: [17, 373],
      ),
      containsAllInOrder(['--page-index', '17', '--page-index', '373']),
    );
    for (final bad in <List<int>>[
      [],
      [-1],
      [1, 1],
      List.generate(101, (i) => i),
    ]) {
      expect(
        () => ScribeForgeCli.processArguments(
          pdfPath: 'x',
          storePath: 'y',
          pageIndexes: bad,
        ),
        throwsArgumentError,
      );
    }
    expect(
      ScribeForgeCli.processArguments(pdfPath: 'x', storePath: 'y'),
      isNot(contains('--page-index')),
    );
  });

  test(
    'history parser validates ordering, cursor, state and legacy availability',
    () {
      final result = JobHistoryBatch.fromText(
        batch([row(12), row(9)], before: 9),
      );
      expect(result.rows.first.location, '物理第 18 页');
      expect(result.nextBefore, 9);
      expect(
        JobHistoryBatch.fromText(batch([], available: false)).available,
        isFalse,
      );
      expect(
        () => JobHistoryBatch.fromText(batch([row(9), row(12)])),
        throwsFormatException,
      );
      expect(
        () => JobHistoryBatch.fromText(batch([row(12)], before: 10)),
        throwsFormatException,
      );
      expect(
        () => JobHistoryBatch.fromText(batch([row(12)], available: false)),
        throwsFormatException,
      );
      final invalid = row(12)..['page_index'] = -1;
      expect(
        () => JobHistoryBatch.fromText(batch([invalid])),
        throwsFormatException,
      );
      final invalidFailure = row(12)..['error_preview'] = null;
      expect(
        () => JobHistoryBatch.fromText(batch([invalidFailure])),
        throwsFormatException,
      );
    },
  );

  test(
    'service sends read-only filter and enforces project and cursor scope',
    () async {
      final fake = _Executor([
        batch([row(12), row(9)], before: 9),
        batch([row(4)]),
      ]);
      final service = JobHistoryService(
        executor: fake,
        executable: 'core',
        storePath: '/book',
        projectId: 1,
      );
      final first = await service.list(pageIndex: 17, stage: 'primary_ocr');
      final second = await service.list(
        pageIndex: 17,
        stage: 'primary_ocr',
        before: first.nextBefore,
      );
      expect(second.rows.single.id, 4);
      expect(fake.calls.first, [
        'job-history',
        '--store',
        '/book',
        '--project-id',
        '1',
        '--page-index',
        '17',
        '--stage',
        'primary_ocr',
        '--limit',
        '25',
      ]);
      expect(fake.calls.last, containsAllInOrder(['--before', '9']));
      final bad = JobHistoryService(
        executor: _Executor([
          batch([row(12)], project: 2),
        ]),
        executable: 'core',
        storePath: '/book',
        projectId: 1,
      );
      await expectLater(bad.list(), throwsFormatException);
      final wrongPage = JobHistoryService(
        executor: _Executor([
          batch([row(1, page: 5)]),
        ]),
        executable: 'core',
        storePath: '/book',
        projectId: 1,
      );
      await expectLater(wrongPage.list(pageIndex: 17), throwsFormatException);
      final wrongCursor = JobHistoryService(
        executor: _Executor([
          batch([row(10)]),
        ]),
        executable: 'core',
        storePath: '/book',
        projectId: 1,
      );
      await expectLater(wrongCursor.list(before: 10), throwsFormatException);
    },
  );

  test('service rejects errors and oversized output', () async {
    final failed = JobHistoryService(
      executor: _Executor(['db unavailable'], code: 2),
      executable: 'core',
      storePath: '/book',
      projectId: 1,
    );
    await expectLater(failed.list(), throwsA(isA<JobHistoryFailure>()));
    final oversized = JobHistoryService(
      executor: _Executor(['x' * (1024 * 1024 + 1)]),
      executable: 'core',
      storePath: '/book',
      projectId: 1,
    );
    await expectLater(oversized.list(), throwsFormatException);
  });

  testWidgets(
    'history shows errors and pages safely, with explicit selection',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final fake = _Executor([
        batch([row(12, origin: 'legacy_snapshot')], before: 12),
        batch([row(8, outcome: 'interrupted')]),
      ]);
      final service = JobHistoryService(
        executor: fake,
        executable: 'core',
        storePath: '/book',
        projectId: 1,
      );
      int? selected;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  selected = await Navigator.push<int>(
                    context,
                    MaterialPageRoute(
                      builder: (_) => JobHistoryScreen(
                        service: service,
                        canSelectPage: true,
                      ),
                    ),
                  );
                },
                child: const Text('打开'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('打开'));
      await tester.pumpAndSettle();
      expect(find.textContaining('物理第 18 页'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('job-attempt-12')));
      await tester.pumpAndSettle();
      expect(find.text('worker failure 12'), findsOneWidget);
      expect(find.textContaining('旧版升级时保留下来'), findsOneWidget);
      await tester.ensureVisible(
        find.byKey(const ValueKey('job-history-more')),
      );
      await tester.tap(find.byKey(const ValueKey('job-history-more')));
      await tester.pumpAndSettle();
      expect(find.textContaining('中断后被重新接管'), findsOneWidget);
      await tester.ensureVisible(
        find.byKey(const ValueKey('select-retry-page-12')),
      );
      await tester.tap(find.byKey(const ValueKey('select-retry-page-12')));
      await tester.pumpAndSettle();
      expect(selected, 17);
      expect(fake.calls.every((args) => args.first == 'job-history'), isTrue);
    },
  );

  testWidgets('old history is unavailable and no retry selection is implied', (
    tester,
  ) async {
    final fake = _Executor([batch([], available: false)]);
    await tester.pumpWidget(
      MaterialApp(
        home: JobHistoryScreen(
          service: JobHistoryService(
            executor: fake,
            executable: 'core',
            storePath: 'book',
            projectId: 1,
          ),
          canSelectPage: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('旧版项目没有尝试历史'), findsOneWidget);
    expect(find.text('选择此物理页准备续跑'), findsNothing);
  });

  testWidgets(
    'retry dialog requires valid physical pages and explicit confirmation',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      List<int>? result;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  result = await showDialog<List<int>>(
                    context: context,
                    builder: (_) => const ScopedRetryDialog(
                      initialPages: [17],
                      sourcePages: 562,
                      remoteVlmEnabled: true,
                    ),
                  );
                },
                child: const Text('打开'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('打开'));
      await tester.pumpAndSettle();
      expect(find.textContaining('可能按配置发送复核裁切图'), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('scoped-retry-pages')),
        '18, 999',
      );
      await tester.tap(find.byKey(const ValueKey('confirm-scoped-retry')));
      await tester.pumpAndSettle();
      expect(find.textContaining('超过原书'), findsOneWidget);
      expect(result, isNull);
      await tester.enterText(
        find.byKey(const ValueKey('scoped-retry-pages')),
        '18, 374',
      );
      await tester.tap(find.byKey(const ValueKey('confirm-scoped-retry')));
      await tester.pumpAndSettle();
      expect(result, [17, 373]);
    },
  );
}

class _Executor implements ScribeForgeCliExecutor {
  _Executor(this.responses, {this.code = 0});
  final List<String> responses;
  final int code;
  final calls = <List<String>>[];
  @override
  Future<void> cancel() async {}
  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String) onOutput,
    required void Function(String) onError,
  }) async {
    calls.add(arguments);
    final output = responses.removeAt(0);
    if (code == 0) {
      onOutput(output);
    } else {
      onError(output);
    }
    return code;
  }
}
