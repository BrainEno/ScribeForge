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

  test('uses safe defaults when no settings file exists', () async {
    final store = DesktopSettingsStore(directory: temporary);
    expect(await store.loadCliExecutable(), 'scribeforge');
    final vlm = await store.loadVlmSettings();
    expect(vlm.enabled, isFalse);
    expect(vlm.endpoint, isEmpty);
    expect(vlm.model, isEmpty);
    expect(vlm.apiKeyEnvironmentVariable, isEmpty);
    expect(vlm.timeoutSeconds, 120);
    expect(vlm.allowRemote, isFalse);
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
    await File('${temporary.path}${Platform.pathSeparator}settings.json')
        .writeAsString('{not json');

    expect(
      await DesktopSettingsStore(directory: temporary).loadCliExecutable(),
      'scribeforge',
    );
  });

  test('persists VLM settings, preserves the CLI path, and stores no API key value',
      () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveCliExecutable(r'C:\Tools\ScribeForge\scribeforge.exe');
    await store.saveVlmSettings(const DesktopVlmSettings(
      enabled: true,
      endpoint: 'http://127.0.0.1:1234/v1',
      model: 'local-vision-model',
      apiKeyEnvironmentVariable: 'SCRIBE_VLM_TOKEN',
      timeoutSeconds: 45,
      allowRemote: false,
    ));

    final loadedStore = DesktopSettingsStore(directory: temporary);
    final vlm = await loadedStore.loadVlmSettings();
    expect(vlm.enabled, isTrue);
    expect(vlm.endpoint, 'http://127.0.0.1:1234/v1');
    expect(vlm.model, 'local-vision-model');
    expect(vlm.apiKeyEnvironmentVariable, 'SCRIBE_VLM_TOKEN');
    expect(vlm.timeoutSeconds, 45);
    expect(vlm.allowRemote, isFalse);
    expect(await loadedStore.loadCliExecutable(), r'C:\Tools\ScribeForge\scribeforge.exe');

    final saved = await File('${temporary.path}${Platform.pathSeparator}settings.json')
        .readAsString();
    expect(saved, contains('SCRIBE_VLM_TOKEN'));
    expect(saved, isNot(contains('"api_key"')));
    expect(saved, isNot(contains('actual-secret-value')));
  });

  test('refuses to enable VLM review without an endpoint and model', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await expectLater(
      store.saveVlmSettings(const DesktopVlmSettings(enabled: true)),
      throwsArgumentError,
    );
  });
}
