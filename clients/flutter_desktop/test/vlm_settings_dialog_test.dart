import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/vlm_settings_dialog.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

class _FakeExecutor implements ScribeForgeCliExecutor {
  _FakeExecutor({this.exitCode = 0, this.stdout = ''});

  final int exitCode;
  final String stdout;
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
    if (stdout.isNotEmpty) onOutput(stdout);
    return exitCode;
  }
}

class _FakeFilePicker implements DesktopFilePicker {
  _FakeFilePicker(this.path);

  final String? path;
  bool called = false;

  @override
  Future<String?> pickDirectory({String? dialogTitle}) async => null;

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    List<String>? allowedExtensions,
  }) async {
    called = true;
    return path;
  }
}

Future<DesktopVlmSettings?> showDialogUnderTest(
  WidgetTester tester, {
  required _FakeExecutor executor,
  _FakeFilePicker? filePicker,
  Map<String, String>? environment,
}) async {
  DesktopVlmSettings? result;
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () async {
              result = await showDialog<DesktopVlmSettings>(
                context: context,
                builder: (_) => VlmSettingsDialog(
                  executable: 'scribeforge',
                  initial: const DesktopVlmSettings(
                    enabled: true,
                    endpoint: 'https://vlm.example.com/v1/chat/completions',
                    model: 'qwen-vl',
                    apiKeyEnvironmentVariable: 'MY_VLM_KEY',
                  ),
                  cliExecutor: executor,
                  filePicker: filePicker ?? _FakeFilePicker(null),
                  environment: environment ?? const <String, String>{},
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
  return result;
}

void main() {
  testWidgets('shows an env-var warning when the key variable is missing', (
    tester,
  ) async {
    await showDialogUnderTest(
      tester,
      executor: _FakeExecutor(),
      environment: const <String, String>{'OTHER': 'x'},
    );
    expect(find.textContaining('未检测到环境变量 MY_VLM_KEY'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows a positive hint when the key variable exists', (tester) async {
    await showDialogUnderTest(
      tester,
      executor: _FakeExecutor(),
      environment: const <String, String>{'MY_VLM_KEY': 'secret-value'},
    );
    expect(find.textContaining('已检测到环境变量 MY_VLM_KEY'), findsOneWidget);
    // The secret itself must never be rendered.
    expect(find.textContaining('secret-value'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('picker fills the image path; built-in sample writes a file', (
    tester,
  ) async {
    final picker = _FakeFilePicker('/tmp/picked.png');
    await showDialogUnderTest(tester, executor: _FakeExecutor(), filePicker: picker);
    await tester.tap(find.byKey(const ValueKey('pick-vlm-test-image-button')));
    await tester.pumpAndSettle();
    expect(picker.called, isTrue);
    expect(find.text('/tmp/picked.png'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('remote endpoint requires explicit confirmation before sending', (
    tester,
  ) async {
    final executor = _FakeExecutor();
    await showDialogUnderTest(tester, executor: executor);

    // Summary states file/host/model before sending.
    expect(find.textContaining('vlm.example.com'), findsWidgets);
    expect(find.byKey(const ValueKey('confirm-remote-vlm-send-checkbox')),
        findsOneWidget);

    final imageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.labelText == '测试图片路径',
    );
    expect(imageField, findsOneWidget);

    // Tap "用图片测试…" without confirming — the probe must not run.
    await tester.enterText(imageField, '/tmp/picked.png');
    await tester.tap(find.text('用图片测试…'));
    await tester.pumpAndSettle();
    expect(executor.lastArguments, isNull);
    expect(find.textContaining('请先勾选'), findsOneWidget);

    // Confirm and retry — the probe runs with vlm-check arguments.
    await tester.tap(find.byKey(const ValueKey('confirm-remote-vlm-send-checkbox')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('用图片测试…'));
    await tester.pumpAndSettle();
    expect(executor.lastArguments, isNotNull);
    expect(executor.lastArguments, contains('vlm-check'));
    expect(tester.takeException(), isNull);
  });

  testWidgets('loopback endpoint does not require remote confirmation', (
    tester,
  ) async {
    final executor = _FakeExecutor();
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showDialog<DesktopVlmSettings>(
                context: context,
                builder: (_) => VlmSettingsDialog(
                  executable: 'scribeforge',
                  initial: const DesktopVlmSettings(
                    enabled: true,
                    endpoint: 'http://127.0.0.1:8080/v1/chat/completions',
                    model: 'local-vl',
                  ),
                  cliExecutor: executor,
                  filePicker: _FakeFilePicker(null),
                  environment: const <String, String>{},
                ),
              ),
              child: const Text('打开'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('confirm-remote-vlm-send-checkbox')),
      findsNothing,
    );
    expect(tester.takeException(), isNull);
  });
}
