import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/processing_report_loader.dart';

class _FakeExecutor implements ScribeForgeCliExecutor {
  _FakeExecutor({required this.exitCode, this.stdout = '', this.stderr = '', this.throwing});

  final int exitCode;
  final String stdout;
  final String stderr;
  final Object? throwing;
  List<String>? lastArguments;

  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    lastArguments = arguments;
    final error = throwing;
    if (error != null) throw error;
    if (stdout.isNotEmpty) onOutput(stdout);
    if (stderr.isNotEmpty) onError(stderr);
    return exitCode;
  }
}

const String _okReport = '''
{"schema_version":1,"project_id":1,"title":"T","pages_total":1,
"ocr_complete_pages":1,"failed_pages":0,"running_pages":0,
"review":{"pairs":1,"accepted_automatic":1,"accepted_vlm":0,
"accepted_human":0,"open_pairs":0,"open_high_risk":0,"open_blocked":0,
"review_required_ratio":0.0},
"risk_reasons":{},"risk_distribution":[],"high_risk_pairs_total":0,
"timing":{"scope":"s","observed_seconds":1.0,"timed_jobs":1,
"unavailable_jobs":0,"bottleneck_stage":null,"engines":[]},
"slowest_pages":[],"stages":[],"pages":[]}
''';

void main() {
  group('ProcessingReportLoader', () {
    test('runs report --json and parses the snapshot', () async {
      final executor = _FakeExecutor(exitCode: 0, stdout: _okReport);
      final loader = ProcessingReportLoader(executor: executor);
      final report = await loader.load(
        executable: 'scribeforge',
        storePath: '/store',
        projectId: 3,
      );
      expect(report.title, 'T');
      expect(report.timing.observedSeconds, 1.0);
      expect(executor.lastArguments, contains('report'));
      expect(executor.lastArguments, contains('--store'));
      expect(executor.lastArguments, contains('/store'));
      expect(executor.lastArguments, contains('--json'));
      expect(executor.lastArguments, contains('--project'));
      expect(executor.lastArguments, contains('3'));
    });

    test('surfaces a non-zero exit with an actionable message', () async {
      final executor = _FakeExecutor(exitCode: 2, stderr: 'no such store');
      final loader = ProcessingReportLoader(executor: executor);
      expect(
        () => loader.load(executable: 'scribeforge', storePath: '/missing'),
        throwsA(
          isA<ProcessingReportLoadException>().having(
            (error) => error.message,
            'message',
            allOf(contains('退出码 2'), contains('no such store')),
          ),
        ),
      );
    });

    test('reports an empty report body', () async {
      final executor = _FakeExecutor(exitCode: 0);
      final loader = ProcessingReportLoader(executor: executor);
      expect(
        () => loader.load(executable: 'scribeforge', storePath: '/store'),
        throwsA(
          isA<ProcessingReportLoadException>().having(
            (error) => error.message,
            'message',
            contains('没有返回报告内容'),
          ),
        ),
      );
    });

    test('reports malformed JSON as a parse failure', () async {
      final executor = _FakeExecutor(exitCode: 0, stdout: '{nope');
      final loader = ProcessingReportLoader(executor: executor);
      expect(
        () => loader.load(executable: 'scribeforge', storePath: '/store'),
        throwsA(
          isA<ProcessingReportLoadException>().having(
            (error) => error.message,
            'message',
            contains('无法解析'),
          ),
        ),
      );
    });

    test('reports a launch failure without a raw ProcessException', () async {
      final executor = _FakeExecutor(
        exitCode: 0,
        throwing: const ProcessException('not found', <String>[]),
      );
      final loader = ProcessingReportLoader(executor: executor);
      expect(
        () => loader.load(executable: 'scribeforge', storePath: '/store'),
        throwsA(
          isA<ProcessingReportLoadException>().having(
            (error) => error.message,
            'message',
            contains('无法启动处理报告命令'),
          ),
        ),
      );
    });
  });
}
