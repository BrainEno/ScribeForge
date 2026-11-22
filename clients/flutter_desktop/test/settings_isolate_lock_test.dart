import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

Future<void> _writer((String, String, SendPort) args) async {
  final (path, prefix, output) = args;
  try {
    final store = DesktopSettingsStore(
      directory: Directory(path),
      lockTimeout: const Duration(seconds: 30),
      beforeRename: (_, __) =>
          Future<void>.delayed(const Duration(milliseconds: 5)),
    );
    for (var i = 0; i < 60; i++) {
      final result = await store.update(
        (settings) => settings['$prefix-$i'] = i,
      );
      if (!result.ok) throw StateError(result.error ?? 'save failed');
    }
    output.send('ok');
  } on Object catch (error) {
    output.send('$error');
  }
}

Future<void> _holder((String, SendPort) args) async {
  final (path, output) = args;
  final release = ReceivePort();
  final store = DesktopSettingsStore(
    directory: Directory(path),
    beforeRename: (_, __) async {
      output.send(release.sendPort);
      await release.first;
    },
  );
  final result = await store.saveCliExecutable('/holder');
  output.send(result.ok ? 'done' : result.error);
  release.close();
}

void main() {
  test('two isolates keep all 120 updates', () async {
    final directory = await Directory.systemTemp.createTemp(
      'settings-isolates-',
    );
    final first = ReceivePort();
    final second = ReceivePort();
    try {
      final a = await Isolate.spawn(_writer, (
        directory.path,
        'first',
        first.sendPort,
      ));
      final b = await Isolate.spawn(_writer, (
        directory.path,
        'second',
        second.sendPort,
      ));
      try {
        final messages = await Future.wait(<Future<dynamic>>[
          first.first,
          second.first,
        ]);
        expect(messages, <String>['ok', 'ok']);
      } finally {
        a.kill(priority: Isolate.immediate);
        b.kill(priority: Isolate.immediate);
      }
      final file = File(
        '${directory.path}${Platform.pathSeparator}settings.json',
      );
      final raw = await file.readAsString();
      final saved = jsonDecode(raw) as Map<String, dynamic>;
      expect(saved.length, 120);
      for (final prefix in <String>['first', 'second']) {
        for (var i = 0; i < 60; i++) {
          expect(saved['$prefix-$i'], i);
        }
      }
    } finally {
      first.close();
      second.close();
      await directory.delete(recursive: true);
    }
  });

  test('a held isolate lock times out and a later retry succeeds', () async {
    final directory = await Directory.systemTemp.createTemp(
      'settings-isolates-',
    );
    final messages = ReceivePort();
    final stream = messages.asBroadcastStream();
    final holder = await Isolate.spawn(_holder, (
      directory.path,
      messages.sendPort,
    ));
    try {
      final release =
          await stream.first.timeout(const Duration(seconds: 20)) as SendPort;
      final store = DesktopSettingsStore(
        directory: directory,
        lockTimeout: const Duration(milliseconds: 250),
      );
      final result = await store.saveBackendPython('/blocked');
      expect(result.ok, isFalse);
      expect(result.error, contains('其他 ScribeForge 实例'));
      final done = stream.first;
      release.send('release');
      expect(await done.timeout(const Duration(seconds: 20)), 'done');
      expect((await store.saveBackendPython('/after')).ok, isTrue);
      expect(await store.loadCliExecutable(), '/holder');
      expect(await store.loadBackendPython(), '/after');
    } finally {
      holder.kill(priority: Isolate.immediate);
      messages.close();
      await directory.delete(recursive: true);
    }
  });

  test('failed mutation releases the gate for a later save', () async {
    final directory = await Directory.systemTemp.createTemp(
      'settings-isolates-',
    );
    try {
      final store = DesktopSettingsStore(directory: directory);
      final result = await store.update(
        (settings) => throw StateError('failure'),
      );
      expect(result.ok, isFalse);
      expect((await store.saveCliExecutable('/after-error')).ok, isTrue);
      expect(await store.loadCliExecutable(), '/after-error');
    } finally {
      await directory.delete(recursive: true);
    }
  });
}
