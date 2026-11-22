import 'dart:io';

import '../../lib/services/settings_store.dart';

// Runs as a separate Dart VM. This is deliberately not a Flutter test isolate:
// the kernel must arbitrate locks between independent operating-system PIDs.
Future<void> main(List<String> args) async {
  final mode = args[0];
  final directory = Directory(args[1]);
  if (mode == 'write') {
    final prefix = args[2];
    final store = DesktopSettingsStore(
      directory: directory,
      lockTimeout: const Duration(seconds: 30),
      beforeRename: (_, __) async {
        await Future<void>.delayed(const Duration(milliseconds: 2));
      },
    );
    for (var i = 0; i < 60; i++) {
      final result = await store.update(
        (settings) => settings['$prefix-$i'] = i,
      );
      if (!result.ok) {
        stderr.writeln(result.error);
        exitCode = 1;
        return;
      }
    }
    return;
  }
  if (mode == 'hold') {
    final store = DesktopSettingsStore(
      directory: directory,
      beforeRename: (_, __) async {
        stdout.writeln('locked');
        await stdout.flush();
        await Future<void>.delayed(const Duration(minutes: 5));
      },
    );
    final result = await store.saveBackendPython('/unfinished');
    if (!result.ok) {
      stderr.writeln(result.error);
      exitCode = 1;
    }
    return;
  }
  stderr.writeln('unknown worker mode');
  exitCode = 2;
}
