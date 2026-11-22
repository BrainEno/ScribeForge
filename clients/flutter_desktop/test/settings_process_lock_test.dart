import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

const worker = 'test/helpers/settings_process_worker.dart';

Future<Process> startWorker(String mode, Directory dir, [String? prefix]) {
  final root = Platform.environment['FLUTTER_ROOT'];
  final executable = Platform.isWindows && root != null
      ? '$root${Platform.pathSeparator}bin${Platform.pathSeparator}cache'
            '${Platform.pathSeparator}dart-sdk${Platform.pathSeparator}bin'
            '${Platform.pathSeparator}dart.exe'
      : 'dart';
  return Process.start(executable, <String>[
    worker,
    mode,
    dir.path,
    if (prefix != null) prefix,
  ]);
}

void main() {
  late Directory temporary;
  setUp(() async {
    temporary = await Directory.systemTemp.createTemp('settings-lock-test-');
  });
  tearDown(() async {
    if (await temporary.exists()) await temporary.delete(recursive: true);
  });

  test('two processes retain 120 independent updates', () async {
    final corrupt = File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    );
    await corrupt.writeAsString('{broken-original');
    final a = await startWorker('write', temporary, 'first');
    final b = await startWorker('write', temporary, 'second');
    final aOut = a.stderr.transform(utf8.decoder).join();
    final bOut = b.stderr.transform(utf8.decoder).join();
    final exits = await Future.wait(<Future<int>>[a.exitCode, b.exitCode]);
    expect(exits, <int>[0, 0], reason: '${await aOut}\n${await bOut}');
    final file = File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    );
    final raw = await file.readAsString();
    final saved = jsonDecode(raw) as Map<String, dynamic>;
    final entries = await temporary.list().toList();
    final backups = entries.where((e) => e.path.contains('settings.corrupt-'));
    expect(backups.length, 1);
    final backup = File(backups.single.path);
    expect(await backup.readAsString(), '{broken-original');
    expect(saved.length, 120);
    for (final prefix in <String>['first', 'second']) {
      for (var i = 0; i < 60; i++) {
        expect(saved['$prefix-$i'], i);
      }
    }
  });

  test('contention, crash and retry', () async {
    final store = DesktopSettingsStore(directory: temporary);
    expect((await store.saveCliExecutable('/before')).ok, isTrue);
    final process = await startWorker('hold', temporary);
    final locked = process.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .first;
    try {
      expect(await locked.timeout(const Duration(seconds: 20)), 'locked');
      final waiting = DesktopSettingsStore(
        directory: temporary,
        lockTimeout: const Duration(milliseconds: 250),
      );
      final failed = await waiting.saveBackendSourcePath('/should-not-save');
      expect(failed.ok, isFalse);
      expect(failed.error, contains('其他 ScribeForge 实例'));
      final file = File(
        '${temporary.path}${Platform.pathSeparator}settings.json',
      );
      expect(jsonDecode(await file.readAsString()), <String, dynamic>{
        'cli_executable': '/before',
      });
      final other = await Directory.systemTemp.createTemp(
        'settings-independent-',
      );
      try {
        final otherStore = DesktopSettingsStore(directory: other);
        final otherResult = await otherStore.saveCliExecutable('/other');
        expect(otherResult.ok, isTrue);
      } finally {
        await other.delete(recursive: true);
      }
    } finally {
      process.kill();
      await process.exitCode.timeout(const Duration(seconds: 10));
    }
    expect((await store.saveBackendSourcePath('/after')).ok, isTrue);
    expect(await store.loadCliExecutable(), '/before');
    expect(await store.loadBackendSourcePath(), '/after');
    expect(await store.loadBackendPython(), isEmpty);
  });

  test('previous-version process still excludes new writers', () async {
    final process = await startWorker('legacy', temporary);
    final locked = process.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .first;
    try {
      expect(await locked.timeout(const Duration(seconds: 20)), 'locked');
      final store = DesktopSettingsStore(
        directory: temporary,
        lockTimeout: const Duration(milliseconds: 250),
      );
      expect((await store.saveCliExecutable('/blocked')).ok, isFalse);
      final file = File('${temporary.path}${Platform.pathSeparator}settings.json');
      expect(await file.exists(), isFalse);
    } finally {
      process.kill();
      await process.exitCode.timeout(const Duration(seconds: 10));
    }
    final store = DesktopSettingsStore(directory: temporary);
    expect((await store.saveCliExecutable('/after')).ok, isTrue);
  });
}
