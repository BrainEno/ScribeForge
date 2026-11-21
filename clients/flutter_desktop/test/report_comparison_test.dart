import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/report_comparison.dart';
import 'package:scribeforge_desktop/screens/reports/report_comparison_screen.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/report_comparison_service.dart';

Map<String, Object?> _response({int offset = 0, bool aligned = true}) {
  Map<String, Object> identity(String digit) => {
    'sha256': List.filled(64, digit).join(),
    'title': 'Test book',
    'project_id': 1,
    'timing_scope': 'latest_attempt_timestamp_spans',
    'timed_jobs': 0,
    'unavailable_jobs': 1,
  };
  return {
    'schema_version': 1,
    'kind': 'processing_report_comparison',
    'baseline': identity('a'),
    'candidate': identity('b'),
    'same_labels': aligned,
    'page_comparison': aligned,
    'same_timing_scope': true,
    'same_timing_coverage': false,
    'summary': {},
    'review': {},
    'reasons': {},
    'stages': [],
    'seconds': {'old': 0, 'new': null, 'delta': null},
    'filter': 'changed',
    'order': 'page',
    'offset': offset,
    'matched': 0,
    'page_counts': {'changed': 0},
    'next_offset': null,
    'pages': [],
  };
}

class _Picker implements DesktopFilePicker {
  final _paths = ['before.json', 'after.json'];

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    expect(allowedExtensions, ['json']);
    return _paths.removeAt(0);
  }

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async => null;
}

class _Executor implements ScribeForgeCliExecutor {
  List<String> arguments = [];
  bool fail = false;

  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    this.arguments = arguments;
    if (fail) {
      onError('report bytes changed');
      return 2;
    }
    final payload = jsonEncode(_response());
    onOutput(payload.substring(0, 12));
    onOutput(payload.substring(12));
    return 0;
  }
}

void main() {
  test('missing time does not become zero; incoherent paging is rejected', () {
    final result = ReportComparison.decode(jsonEncode(_response()));
    expect(result.seconds.before, 0);
    expect(result.seconds.after, isNull);
    expect(result.seconds.change, isNull);
    expect(
      () => ReportComparison.decode(jsonEncode(_response(offset: 1))),
      throwsFormatException,
    );
  });

  test(
    'service binds pinned bytes and rejects an unrelated response',
    () async {
      final executor = _Executor();
      final service = ReportComparisonService(
        executor: executor,
        executable: 'scribeforge',
      );
      final pinned = ReportComparison.decode(jsonEncode(_response()));
      await expectLater(
        service.compare(
          baseline: 'before.json',
          candidate: 'after.json',
          filter: 'all',
          order: 'page',
          offset: 0,
          allowUnmatched: false,
          pinned: pinned,
        ),
        throwsFormatException,
      );
      expect(
        executor.arguments,
        contains('--baseline-sha256=${pinned.baseline.digest}'),
      );
      expect(
        executor.arguments,
        contains('--candidate-sha256=${pinned.candidate.digest}'),
      );
    },
  );

  testWidgets('opens two saved reports, shows no matching pages and retries', (
    tester,
  ) async {
    final executor = _Executor();
    await tester.pumpWidget(
      MaterialApp(
        home: ReportComparisonScreen(
          picker: _Picker(),
          service: ReportComparisonService(
            executor: executor,
            executable: 'scribeforge',
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('choose-baseline')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('choose-candidate')));
    await tester.pumpAndSettle();
    final compare = find.byKey(const ValueKey('run-report-comparison'));
    executor.fail = true;
    await tester.tap(compare);
    await tester.pumpAndSettle();
    expect(find.textContaining('report bytes changed'), findsOneWidget);
    executor.fail = false;
    await tester.tap(compare);
    await tester.pumpAndSettle();
    expect(
      executor.arguments,
      containsAll(['--baseline=before.json', '--candidate=after.json']),
    );
    await tester.tap(find.text('页面'));
    await tester.pumpAndSettle();
    expect(find.text('没有符合此筛选的页面。'), findsOneWidget);
  });
}
