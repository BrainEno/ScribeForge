import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_setup.dart';
import 'package:scribeforge_desktop/screens/runtime_setup_dialog.dart';

void main() {
  Future<void> showSetupDialog(
    WidgetTester tester, {
    required RuntimeSetupRunner runSetup,
    Future<void> Function()? cancelSetup,
    bool autoStart = false,
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
              onPressed: () => showDialog<bool>(
                context: context,
                barrierDismissible: false,
                builder: (_) => RuntimeSetupDialog(
                  runSetup: runSetup,
                  cancelSetup: cancelSetup,
                  autoStart: autoStart,
                ),
              ),
              child: const Text('打开配置'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('打开配置'));
    await tester.pumpAndSettle();
  }

  testWidgets('shows per-step progress and successful backend states', (
    tester,
  ) async {
    await showSetupDialog(
      tester,
      runSetup:
          ({
            required backend,
            required onMessage,
            required onDiagnostic,
          }) async {
            expect(backend, isNull);
            onMessage(
              RuntimeSetupMessage.fromJsonLine(
                '{"schema_version":1,"type":"progress","backend":"mineru",'
                '"phase":"install","state":"step_started","step":"environment",'
                '"completed_steps":1,"total_steps":4}',
              ),
            );
            onMessage(
              RuntimeSetupMessage.fromJsonLine(
                '{"schema_version":1,"type":"progress","backend":"mineru",'
                '"phase":"complete","state":"ready","step":null,'
                '"completed_steps":4,"total_steps":4}',
              ),
            );
            onMessage(
              RuntimeSetupMessage.fromJsonLine(
                '{"schema_version":1,"type":"result","action":"install",'
                '"success":true,"message":null,"backends":{'
                '"mineru":{"state":"ready","failed_step":null},'
                '"paddleocr":{"state":"ready","failed_step":null}}}',
              ),
            );
            return 0;
          },
    );

    expect(find.text('首次 OCR 运行时配置'), findsOneWidget);
    expect(find.text('MinerU'), findsOneWidget);
    expect(find.text('PaddleOCR'), findsOneWidget);
    await tester.tap(find.text('开始安装全部后端'));
    await tester.pumpAndSettle();

    expect(find.text('配置完成'), findsOneWidget);
    expect(find.text('就绪'), findsNWidgets(2));
    expect(find.text('只修复 MinerU'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('autoStart begins initialization without a second click', (
    tester,
  ) async {
    var calls = 0;
    await showSetupDialog(
      tester,
      autoStart: true,
      runSetup:
          ({
            required backend,
            required onMessage,
            required onDiagnostic,
          }) async {
            calls++;
            expect(backend, isNull);
            onMessage(
              RuntimeSetupMessage.fromJsonLine(
                '{"schema_version":1,"type":"result","action":"install",'
                '"success":true,"message":null,"backends":{'
                '"mineru":{"state":"ready","failed_step":null},'
                '"paddleocr":{"state":"ready","failed_step":null}}}',
              ),
            );
            return 0;
          },
    );

    expect(calls, 1);
    expect(find.text('配置完成'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('supports repairing one backend and explains the failed step', (
    tester,
  ) async {
    String? repairedBackend;
    await showSetupDialog(
      tester,
      runSetup: ({required backend, required onMessage, required onDiagnostic}) async {
        repairedBackend = backend;
        onMessage(
          RuntimeSetupMessage.fromJsonLine(
            '{"schema_version":1,"type":"progress","backend":"paddleocr",'
            '"phase":"models","state":"failed","step":"download",'
            '"completed_steps":2,"total_steps":5}',
          ),
        );
        onMessage(
          RuntimeSetupMessage.fromJsonLine(
            '{"schema_version":1,"type":"result","action":"repair",'
            '"success":false,"message":"network unavailable","backends":{'
            '"mineru":{"state":"ready","failed_step":null},'
            '"paddleocr":{"state":"failed","failed_step":"models:download"}}}',
          ),
        );
        return 1;
      },
    );

    await tester.tap(find.text('只修复 PaddleOCR'));
    await tester.pumpAndSettle();

    expect(repairedBackend, 'paddleocr');
    expect(find.textContaining('失败步骤：下载模型'), findsOneWidget);
    expect(find.text('network unavailable'), findsOneWidget);
    expect(find.text('配置未完成'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('cancels an active setup and keeps cancelled state retryable', (
    tester,
  ) async {
    final pending = Completer<int>();
    void Function(RuntimeSetupMessage message)? emitMessage;
    var cancelCalls = 0;
    await showSetupDialog(
      tester,
      cancelSetup: () async {
        cancelCalls++;
      },
      runSetup: ({required backend, required onMessage, required onDiagnostic}) {
        emitMessage = onMessage;
        return pending.future;
      },
    );

    await tester.tap(find.text('开始安装全部后端'));
    await tester.pump();
    expect(find.text('取消配置'), findsOneWidget);

    await tester.tap(find.text('取消配置'));
    await tester.pump();
    expect(cancelCalls, 1);
    expect(find.text('正在取消…'), findsOneWidget);

    // Complete only after the cancellation request has been observed.
    // The real executor finishes with 130 after the CLI kills its process tree.
    emitMessage!(
      RuntimeSetupMessage.fromJsonLine(
        '{"schema_version":1,"type":"result","action":"install",'
        '"success":false,"message":"processing cancelled","backends":{'
        '"mineru":{"state":"cancelled","failed_step":"models:download"},'
        '"paddleocr":{"state":"missing","failed_step":null}}}',
      ),
    );
    pending.complete(130);
    await tester.pumpAndSettle();

    expect(find.text('重新安装全部后端'), findsOneWidget);
    expect(find.text('已取消'), findsOneWidget);
    expect(find.text('取消配置'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
