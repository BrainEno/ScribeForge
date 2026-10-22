import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

void main() {
  late Directory temporary;

  setUp(() async {
    temporary = await Directory.systemTemp.createTemp('scribeforge-settings-');
  });

  tearDown(() async {
    if (await temporary.exists()) {
      await temporary.delete(recursive: true);
    }
  });

  test('uses a safe default when no settings file exists', () async {
    final store = DesktopSettingsStore(directory: temporary);
    expect(await store.loadCliExecutable(), 'scribeforge');
  });

  test('persists the configured executable across loads', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveCliExecutable(r'C:\Tools\ScribeForge\scribeforge.exe');

    expect(
      await DesktopSettingsStore(directory: temporary).loadCliExecutable(),
      r'C:\Tools\ScribeForge\scribeforge.exe',
    );
  });

  test('falls back to the default if settings JSON is malformed', () async {
    await File(temporary.path + Platform.pathSeparator + 'settings.json')
        .writeAsString('{not json');

    expect(
      await DesktopSettingsStore(directory: temporary).loadCliExecutable(),
      'scribeforge',
    );
  });
}
