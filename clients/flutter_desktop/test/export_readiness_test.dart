import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/export_readiness.dart';
import 'package:scribeforge_desktop/screens/export/export_readiness_screen.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/export_readiness_service.dart';

void main() {
  testWidgets('shows blocked content and returns a scoped review destination', (
    tester,
  ) async {
    final fake = _ReadinessCli();
    ExportReadinessLink? destination;
    final service = ExportReadinessService(
      executor: fake,
      executable: 'scribeforge',
      storePath: '/local book',
      projectId: 1,
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) {
            return Scaffold(
              body: TextButton(
                onPressed: () async {
                  destination = await Navigator.of(context)
                      .push<ExportReadinessLink>(
                        MaterialPageRoute<ExportReadinessLink>(
                          builder: (_) => ExportReadinessScreen(
                            service: service,
                            options: const ExportReadinessOptions(
                              strict: true,
                              includePageFurniture: true,
                            ),
                          ),
                        ),
                      );
                },
                child: const Text('Open preflight'),
              ),
            );
          },
        ),
      ),
    );
    await tester.tap(find.text('Open preflight'));
    await tester.pumpAndSettle();
    expect(
      fake.calls.single,
      containsAll(['--strict-acceptance', '--include-page-furniture']),
    );
    expect(find.text('全书有 2 项内容阻塞'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('export-readiness-page-filter')),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField), '3');
    await tester.tap(find.text('检查该页明细'));
    await tester.pumpAndSettle();
    expect(fake.calls.last, containsAllInOrder(['--page', '2']));
    expect(find.text('全书有 2 项内容阻塞'), findsOneWidget);
    final review = find.text('前往复核');
    await tester.ensureVisible(review);
    await tester.tap(review);
    await tester.pumpAndSettle();
    expect(destination?.destination, ExportReadinessDestination.review);
    expect(destination?.page, 2);
    expect(destination?.pair, 4);
    expect(tester.takeException(), isNull);
  });
}

class _ReadinessCli implements ScribeForgeCliExecutor {
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
    expect(arguments.first, 'export-readiness');
    final index = arguments.indexOf('--page');
    final page = index < 0 ? null : int.parse(arguments[index + 1]);
    onOutput(
      jsonEncode(<String, Object?>{
        'schema_version': 1,
        'project_id': 1,
        'project_title': 'Book',
        'ready': false,
        'blocking_count': 2,
        'matching_count': page == null ? 2 : 1,
        'issue_counts': {'unresolved_pair': 1, 'unverified_page': 1},
        'options': {
          'strict_acceptance': true,
          'include_page_furniture': true,
          'page_index': page,
        },
        'issues': [
          if (page == null)
            {
              'kind': 'unverified_page',
              'blocking': true,
              'page_index': 0,
              'detail': '',
              'reasons': [],
              'characters': [],
            },
          {
            'kind': 'unresolved_pair',
            'blocking': true,
            'page_index': 2,
            'pair_index': 4,
            'detail': '',
            'reasons': ['text_disagreement'],
            'characters': [],
          },
        ],
        'summary': null,
      }),
    );
    return 0;
  }
}
