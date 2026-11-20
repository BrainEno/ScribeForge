import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/evidence/evidence_search_screen.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/evidence_search_service.dart';

void main() {
  testWidgets('validates page ranges and opens a cited search result', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(520, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final fake = _SearchCli();
    await tester.pumpWidget(
      MaterialApp(
        home: EvidenceSearchScreen(
          service: EvidenceSearchService(
            executor: fake,
            executable: 'scribeforge',
            storePath: '/book store',
            projectId: 13,
          ),
        ),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('evidence-search-term')),
      '--𠀀%',
    );
    await tester.enterText(
      find.byKey(const ValueKey('evidence-search-first-page')),
      '3',
    );
    await tester.enterText(
      find.byKey(const ValueKey('evidence-search-last-page')),
      '2',
    );
    final submit = find.byKey(const ValueKey('run-evidence-search'));
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(find.text('结束页不能早于起始页。'), findsOneWidget);
    expect(fake.calls, isEmpty);
    await tester.enterText(
      find.byKey(const ValueKey('evidence-search-first-page')),
      '1',
    );
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(fake.calls.single, contains('--term=--𠀀%'));
    expect(
      fake.calls.single,
      containsAllInOrder(['--page-start', '0', '--page-end', '1']),
    );
    await tester.tap(find.byKey(const ValueKey('evidence-search-hit-4')));
    await tester.pumpAndSettle();
    expect(
      fake.calls.last,
      containsAllInOrder(['--snapshot-id', '4', '--record-id', '4']),
    );
    expect(find.textContaining('OCR #12'), findsOneWidget);
    expect(find.textContaining('归一化坐标'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class _SearchCli implements ScribeForgeCliExecutor {
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
    onOutput(
      jsonEncode(<String, Object?>{
        'schema_version': 1,
        'project_id': 13,
        'project_title': 'Book',
        'source': 'ocr',
        'term': '--𠀀%',
        'snapshot_id': 4,
        'next_before': null,
        'hits': [
          {
            'id': 4,
            'page_index': 0,
            'run_id': 12,
            'line_index': 0,
            'engine': 'paddle',
            'engine_version': '3',
            'role_in_latest_verification': 'primary',
            'box': [0.1, 0.2, 0.4, 0.3],
            'excerpt': {
              'before': '前',
              'match': '--𠀀%',
              'after': '後',
              'omitted_before': false,
              'omitted_after': false,
            },
          },
        ],
      }),
    );
    return 0;
  }
}
