import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_location.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/runtime_location_service.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

class ScriptedCli implements ScribeForgeCliExecutor {
  final calls = <List<String>>[];
  final outputs = <(int, String)>[];

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
    final (code, output) = outputs.removeAt(0);
    final middle = output.length ~/ 2;
    onOutput(output.substring(0, middle));
    onOutput(output.substring(middle));
    return code;
  }
}

String checkOutput({
  bool ready = false,
  String path = '/other drive/runtime',
}) => jsonEncode(<String, Object>{
  'schema_version': 1,
  'ok': true,
  'runtime_root': path,
  'models_root': '/shared models',
  'external_models': true,
  'disk': <String, Object>{
    'sufficient': true,
    'required_bytes': 5300,
    'free_bytes': 6400,
  },
  'backends': <String, String>{
    'mineru': ready ? 'ready' : 'missing',
    'paddleocr': ready ? 'ready' : 'missing',
  },
  'checked_model_directories': <String>['/shared models/mineru'],
});

String setupOutput({bool success = true}) => jsonEncode(<String, Object>{
  'schema_version': 1,
  'type': 'result',
  'action': 'install',
  'success': success,
  'message': success ? '' : 'sample failed',
  'backends': <String, Object>{
    'mineru': <String, String>{'state': 'ready'},
    'paddleocr': <String, String>{'state': success ? 'ready' : 'failed'},
  },
});

void main() {
  const candidate = RuntimeLocation(
    runtimeRoot: '/other drive/runtime',
    modelsRoot: '/shared models',
  );

  test('profile arguments affect OCR but not evidence-only commands', () async {
    final cli = ScriptedCli()
      ..outputs.addAll(<(int, String)>[(0, ''), (0, '')]);
    final scoped = RuntimeLocationExecutor(cli, () => candidate);
    for (final command in <String>['process', 'export']) {
      await scoped.run(
        executable: 'sf',
        arguments: <String>[command, '--store', '/my book'],
        onOutput: (_) {},
        onError: (_) {},
      );
    }
    expect(cli.calls.first, <String>[
      '--location-profile',
      '--model-location',
      '/shared models',
      'process',
      '--store',
      '/my book',
      '--runtime-root',
      '/other drive/runtime',
    ]);
    expect(cli.calls.last, <String>['export', '--store', '/my book']);
  });

  test('preparation rechecks before and after successful setup', () async {
    final cli = ScriptedCli()
      ..outputs.addAll(<(int, String)>[
        (0, checkOutput()),
        (0, setupOutput()),
        (0, checkOutput(ready: true)),
      ]);
    final service = RuntimeLocationService(cli, 'sf');
    final result = await service.prepare(
      selection: candidate,
      expected: RuntimeLocationCheck.fromOutput(checkOutput()),
      onProgress: (_) {},
      isCancelled: () => false,
      activeRuntimeRoot: '/active',
    );
    expect(result.bothReady, isTrue);
    expect(cli.calls[1], containsAll(<String>['setup', '--cancelable']));
    expect(cli.calls.last, contains('runtime-location-check'));
  });

  test('does not set up a redirected or currently active target', () async {
    for (final changed in <bool>[false, true]) {
      final cli = ScriptedCli()
        ..outputs.add((
          0,
          checkOutput(path: changed ? '/redirect' : candidate.runtimeRoot),
        ));
      final service = RuntimeLocationService(cli, 'sf');
      await expectLater(
        service.prepare(
          selection: candidate,
          expected: RuntimeLocationCheck.fromOutput(checkOutput()),
          onProgress: (_) {},
          isCancelled: () => false,
          activeRuntimeRoot: candidate.runtimeRoot,
        ),
        throwsA(isA<RuntimeLocationCheckFailure>()),
      );
      expect(cli.calls, hasLength(1));
    }
  });

  test('failed sample or cancellation does not report verified', () async {
    for (final cancel in <bool>[false, true]) {
      final cli = ScriptedCli()
        ..outputs.addAll(<(int, String)>[
          (0, checkOutput()),
          (2, setupOutput(success: false)),
        ]);
      final service = RuntimeLocationService(cli, 'sf');
      await expectLater(
        service.prepare(
          selection: candidate,
          expected: RuntimeLocationCheck.fromOutput(checkOutput()),
          onProgress: (_) {},
          isCancelled: () => cancel && cli.calls.length == 2,
        ),
        throwsA(
          cancel
              ? isA<RuntimePreparationCancelled>()
              : isA<RuntimeLocationCheckFailure>(),
        ),
      );
      expect(cli.calls, hasLength(2));
    }
  });

  test('settings switch is atomic and retains previous location', () async {
    final directory = await Directory.systemTemp.createTemp('runtime-settings');
    addTearDown(() => directory.delete(recursive: true));
    var throwBeforeWrite = false;
    final store = DesktopSettingsStore(
      directory: directory,
      beforeRename: (_, _) async {
        if (throwBeforeWrite) throw const FileSystemException('disk full');
      },
    );
    const defaults = RuntimeLocation();
    expect(
      (await store.activateRuntimeLocation(
        expected: defaults,
        next: candidate,
      )).ok,
      isTrue,
    );
    expect((await store.loadRuntimeLocations()).previous, defaults);
    expect(
      (await store.activateRuntimeLocation(
        expected: defaults,
        next: defaults,
      )).ok,
      isFalse,
    );
    throwBeforeWrite = true;
    expect(
      (await store.activateRuntimeLocation(
        expected: candidate,
        next: defaults,
      )).ok,
      isFalse,
    );
    expect((await store.loadRuntimeLocations()).active, candidate);
  });
}
