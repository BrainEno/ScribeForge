import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/processing_report.dart';
import 'package:scribeforge_desktop/screens/processing_report_sheet.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/processing_report_loader.dart';

const String _reportJson = '''
{"schema_version":1,"project_id":1,"title":"Demo","pages_total":2,
"ocr_complete_pages":1,"failed_pages":1,"running_pages":0,
"review":{"pairs":3,"accepted_automatic":1,"accepted_vlm":0,
"accepted_human":0,"open_pairs":2,"open_high_risk":1,"open_blocked":0,
"review_required_ratio":0.667},
"risk_reasons":{"low_confidence":1},
"risk_distribution":[{"low":2,"high":1,"count":3}],
"high_risk_pairs_total":1,
"timing":{"scope":"job wall-clock","observed_seconds":42.0,"timed_jobs":3,
"unavailable_jobs":0,"bottleneck_stage":"primary_ocr","engines":[
{"engine":"mineru","stage":"primary_ocr","timed_jobs":2,"unavailable_jobs":0,
"observed_seconds":30.0,"share":0.71,"mean_seconds":15.0}]},
"slowest_pages":[{"page_index":1,"observed_seconds":42.0}],
"stages":[],
"pages":[
{"page_index":0,"status":"done","aligned_pairs":1,"high_risk_pairs":0,
"open_pairs":0,"risk_reasons":{},"observed_seconds":10.0,"stage_timings":[]},
{"page_index":1,"status":"needs_review","aligned_pairs":2,"high_risk_pairs":1,
"open_pairs":2,"risk_reasons":{"low_confidence":1},"observed_seconds":null,
"stage_timings":[]}]}
''';

class _FakeExecutor implements ScribeForgeCliExecutor {
  _FakeExecutor(this.exitCode, {this.stdout = ''});

  final int exitCode;
  final String stdout;

  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    if (stdout.isNotEmpty) onOutput(stdout);
    return exitCode;
  }
}

typedef _ExportHandler = Future<String?> Function(
  ProcessingReportSnapshot report, {
  required bool markdown,
});

Future<void> showSheet(
  WidgetTester tester, {
  required ProcessingReportLoader loader,
  bool processing = false,
  _ExportHandler? exportHandler,
}) async {
  tester.view.physicalSize = const Size(1280, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => showModalBottomSheet<void>(
              context: context,
              isScrollControlled: true,
              builder: (_) => ProcessingReportSheet(
                executable: 'scribeforge',
                storePath: '/store',
                loader: loader,
                processing: processing,
                exportHandler: exportHandler,
              ),
            ),
            child: const Text('打开报告'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('打开报告'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows summary cards, engines, risk and reviewable pages', (
    tester,
  ) async {
    final loader = ProcessingReportLoader(
      executor: _FakeExecutor(0, stdout: _reportJson),
    );
    await showSheet(tester, loader: loader);

    expect(find.textContaining('处理报告：Demo'), findsOneWidget);
    expect(find.text('42.0 秒'), findsOneWidget);
    expect(find.text('primary_ocr'), findsOneWidget);
    expect(find.text('66.7%'), findsOneWidget);
    expect(find.text('mineru（primary_ocr）：30.0 秒，占比 71.0%'), findsOneWidget);
    expect(find.text('第 2 页：42.0 秒'), findsOneWidget);
    // The page without measured timing shows N/A, not 0.
    expect(find.text('N/A（未测量）'), findsOneWidget);

    // Toggle "只看需抽查页": only page 2 remains.
    await tester.tap(find.text('只看需抽查页'));
    await tester.pumpAndSettle();
    expect(find.text('needs_review'), findsOneWidget);
    expect(find.text('done'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows an actionable error with retry on load failure', (
    tester,
  ) async {
    var attempts = 0;
    final loader = _FailThenSucceedLoader(() => ++attempts);
    await showSheet(tester, loader: loader);

    expect(find.textContaining('读取处理报告失败'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('retry-report-button')));
    await tester.pumpAndSettle();
    expect(attempts, 2);
    expect(find.textContaining('处理报告：Demo'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('while processing, does not load or show stale data', (
    tester,
  ) async {
    var loads = 0;
    final loader = _FailThenSucceedLoader(() {
      loads++;
      return loads;
    });
    await showSheet(tester, loader: loader, processing: true);

    expect(loads, 0);
    expect(find.textContaining('处理正在进行中'), findsOneWidget);
    expect(find.textContaining('还没有可查看的报告'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('exports Markdown and JSON through the export handler', (
    tester,
  ) async {
    final loader = ProcessingReportLoader(
      executor: _FakeExecutor(0, stdout: _reportJson),
    );
    final requests = <bool>[]; // markdown flags, in call order
    await showSheet(
      tester,
      loader: loader,
      exportHandler: (report, {required bool markdown}) async {
        requests.add(markdown);
        return markdown ? '/tmp/report.md' : '/tmp/report.json';
      },
    );

    expect(
      find.byKey(const ValueKey('export-report-markdown-button')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('export-report-json-button')),
      findsOneWidget,
    );

    await tester.tap(
      find.byKey(const ValueKey('export-report-markdown-button')),
    );
    await tester.pumpAndSettle();
    expect(requests, [true]);
    expect(find.text('已导出到：/tmp/report.md'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('export-report-json-button')));
    await tester.pumpAndSettle();
    expect(requests, [true, false]);
    expect(find.text('已导出到：/tmp/report.json'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a cancelled export reports cancellation, not a path', (
    tester,
  ) async {
    final loader = ProcessingReportLoader(
      executor: _FakeExecutor(0, stdout: _reportJson),
    );
    await showSheet(
      tester,
      loader: loader,
      exportHandler: (report, {required bool markdown}) async => null,
    );

    await tester.tap(find.byKey(const ValueKey('export-report-json-button')));
    await tester.pumpAndSettle();
    expect(find.text('导出已取消。'), findsOneWidget);
    expect(find.textContaining('已导出到：'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('export buttons are hidden without an export handler', (
    tester,
  ) async {
    final loader = ProcessingReportLoader(
      executor: _FakeExecutor(0, stdout: _reportJson),
    );
    await showSheet(tester, loader: loader);

    expect(
      find.byKey(const ValueKey('export-report-markdown-button')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('export-report-json-button')),
      findsNothing,
    );
    // Copy stays available: it does not depend on the handler.
    expect(
      find.byKey(const ValueKey('copy-report-json-button')),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('copy puts the raw JSON and Markdown summary on the clipboard', (
    tester,
  ) async {
    final clipboard = <String?>[];
    final messenger = tester.binding.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        clipboard.add((call.arguments as Map)['text'] as String?);
      }
      return null;
    });
    addTearDown(() {
      messenger.setMockMethodCallHandler(SystemChannels.platform, null);
    });

    final loader = ProcessingReportLoader(
      executor: _FakeExecutor(0, stdout: _reportJson),
    );
    await showSheet(tester, loader: loader);

    await tester.tap(find.byKey(const ValueKey('copy-report-json-button')));
    await tester.pumpAndSettle();
    expect(clipboard, hasLength(1));
    expect(clipboard.single, _reportJson);
    expect(find.text('已复制 JSON 报告到剪贴板。'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('copy-report-markdown-button')));
    await tester.pumpAndSettle();
    expect(clipboard, hasLength(2));
    expect(clipboard.last, contains('# 处理报告：Demo'));
    expect(clipboard.last, contains('- 复核率：66.7%'));
    expect(find.text('已复制 Markdown 摘要到剪贴板。'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

class _FailThenSucceedLoader implements ProcessingReportLoader {
  _FailThenSucceedLoader(this._counter);

  final int Function() _counter;
  int _calls = 0;

  @override
  ScribeForgeCliExecutor? get executor => null;

  @override
  Future<ProcessingReportSnapshot> load({
    required String executable,
    required String storePath,
    int? projectId,
  }) async {
    _calls = _counter();
    if (_calls == 1) {
      throw const ProcessingReportLoadException('读取处理报告失败（退出码 2）。');
    }
    return ProcessingReportSnapshot.fromJsonText(_reportJson);
  }
}
