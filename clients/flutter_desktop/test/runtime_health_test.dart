import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_health.dart';
import 'package:scribeforge_desktop/models/runtime_location.dart';
import 'package:scribeforge_desktop/screens/runtime_health_dialog.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';

const _progress = '{"schema_version":1,"type":"progress","backend":"mineru"}\n';
const _success =
    '{"schema_version":1,"type":"result","success":true,"cancelled":false,"results":[{"backend":"mineru","state":"passed","detail":null},{"backend":"paddleocr","state":"passed","detail":null}]}\n';
const _failure =
    '{"schema_version":1,"type":"result","success":false,"cancelled":false,"results":[{"backend":"mineru","state":"failed","detail":"fixture error"},{"backend":"paddleocr","state":"passed","detail":null}]}\n';
const _cancelled =
    '{"schema_version":1,"type":"result","success":false,"cancelled":true,"results":[]}\n';

void main() {
  test('strict stream accepts chunked output and validates both engines', () {
    final progress = <String>[];
    final stream = RuntimeHealthStream(progress.add);
    final input = '$_progress$_success';
    for (var i = 0; i < input.length; i += 7) {
      stream.add(input.substring(i, (i + 7).clamp(0, input.length)));
    }
    stream.finish();
    expect(progress, ['mineru']);
    expect(stream.report!.success, isTrue);
    expect(stream.report!.items.map((e) => e.backend), ['mineru', 'paddleocr']);
  });

  test('incomplete and contradictory reports cannot say success', () {
    final stream = RuntimeHealthStream((_) {});
    expect(() => stream.finish(), throwsFormatException);
    expect(
      () => RuntimeHealthStream((_) {}).add(
        '{"schema_version":1,"type":"result","success":true,"cancelled":false,"results":[]}\n',
      ),
      throwsFormatException,
    );
    expect(
      () => RuntimeHealthStream((_) {}).add('x' * 65537),
      throwsFormatException,
    );
  });

  test('active custom location is included in check arguments', () {
    const location = RuntimeLocation(
      runtimeRoot: '/run',
      modelsRoot: '/models',
    );
    expect(location.argumentsFor(['runtime-check', '--cancelable']), [
      '--location-profile',
      '--model-location',
      '/models',
      'runtime-check',
      '--cancelable',
      '--runtime-root',
      '/run',
    ]);
  });

  Future<void> showDialogUnderTest(
    WidgetTester tester,
    _Executor executor,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: RuntimeHealthDialog(executor: executor, executable: 'fake'),
        ),
      ),
    );
    await tester.pump();
  }

  testWidgets('requires explicit start and shows per-engine failure', (
    tester,
  ) async {
    final executor = _Executor(output: _failure, code: 1);
    await showDialogUnderTest(tester, executor);
    expect(executor.calls, 0);
    expect(find.textContaining('不读取或修改你的书籍'), findsOneWidget);
    await tester.tap(find.text('开始示例检查'));
    await tester.pumpAndSettle();
    expect(executor.calls, 1);
    expect(find.text('检查未全部通过'), findsOneWidget);
    expect(find.text('MinerU：示例识别失败'), findsOneWidget);
    expect(find.text('PaddleOCR：示例识别通过'), findsOneWidget);
    expect(find.text('fixture error'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('cancellation does not report a passed check', (tester) async {
    final executor = _Executor(output: _cancelled, code: 130, hold: true);
    await showDialogUnderTest(tester, executor);
    await tester.tap(find.text('开始示例检查'));
    await tester.pump();
    expect(find.textContaining('正在检查'), findsOneWidget);
    await tester.tap(find.text('取消检查'));
    await tester.pumpAndSettle();
    expect(executor.cancelled, isTrue);
    expect(find.text('检查已取消'), findsOneWidget);
    expect(find.text('两个引擎的示例识别均通过'), findsNothing);
  });

  testWidgets('invalid output is an error, not a success', (tester) async {
    final executor = _Executor(output: '{}\n', code: 0);
    await showDialogUnderTest(tester, executor);
    await tester.tap(find.text('开始示例检查'));
    await tester.pumpAndSettle();
    expect(find.textContaining('无法完成检查'), findsOneWidget);
    expect(find.text('两个引擎的示例识别均通过'), findsNothing);
  });
}

class _Executor implements ScribeForgeCliExecutor {
  _Executor({required this.output, required this.code, this.hold = false});
  final String output;
  final int code;
  final bool hold;
  final Completer<void> release = Completer<void>();
  int calls = 0;
  bool cancelled = false;
  @override
  Future<void> cancel() async {
    cancelled = true;
    if (!release.isCompleted) release.complete();
  }

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String) onOutput,
    required void Function(String) onError,
  }) async {
    calls++;
    expect(arguments, ['runtime-check', '--cancelable']);
    onOutput(_progress);
    if (hold) await release.future;
    onOutput(output);
    return code;
  }
}
