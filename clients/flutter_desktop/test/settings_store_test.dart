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
    expect(await store.loadBackendPython(), isEmpty);
    expect(await store.loadBackendSourcePath(), isEmpty);
    expect(await store.loadLastProjectStore(), isNull);
    expect(store.settingsDirectory.path, temporary.path);
    final vlm = await store.loadVlmSettings();
    expect(vlm.enabled, isFalse);
    expect(vlm.endpoint, isEmpty);
    expect(vlm.model, isEmpty);
    expect(vlm.apiKeyEnvironmentVariable, isEmpty);
    expect(vlm.timeoutSeconds, 120);
    expect(vlm.allowRemote, isFalse);
  });

  test('persists the last project store across loads', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveLastProjectStore('/books/Sample.scribeforge');

    expect(
      await DesktopSettingsStore(directory: temporary).loadLastProjectStore(),
      '/books/Sample.scribeforge',
    );

    // An empty value clears the memory instead of persisting junk.
    await store.saveLastProjectStore('   ');
    expect(await store.loadLastProjectStore(), isNull);
  });

  test('persists the configured executable across loads', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveCliExecutable(r'C:\Tools\ScribeForge\scribeforge.exe');

    expect(
      await DesktopSettingsStore(directory: temporary).loadCliExecutable(),
      r'C:\Tools\ScribeForge\scribeforge.exe',
    );
  });

  test('persists the backend environment settings across loads', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveBackendPython(r'C:\Python314\python.exe');
    await store.saveBackendSourcePath(r'D:\develop\python-projs\ScribeForge');

    final reloaded = DesktopSettingsStore(directory: temporary);
    expect(await reloaded.loadBackendPython(), r'C:\Python314\python.exe');
    expect(
      await reloaded.loadBackendSourcePath(),
      r'D:\develop\python-projs\ScribeForge',
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

  test('persists VLM settings, preserves the CLI path, and stores no API key value', () async {
    final store = DesktopSettingsStore(directory: temporary);
    await store.saveCliExecutable(r'C:\Tools\ScribeForge\scribeforge.exe');
    await store.saveVlmSettings(
      const DesktopVlmSettings(
        enabled: true,
        endpoint: 'http://127.0.0.1:1234/v1',
        model: 'local-vision-model',
        apiKeyEnvironmentVariable: 'SCRIBE_VLM_TOKEN',
        timeoutSeconds: 45,
        allowRemote: false,
      ),
    );

    final loadedStore = DesktopSettingsStore(directory: temporary);
    final vlm = await loadedStore.loadVlmSettings();
    expect(vlm.enabled, isTrue);
    expect(vlm.endpoint, 'http://127.0.0.1:1234/v1');
    expect(vlm.model, 'local-vision-model');
    expect(vlm.apiKeyEnvironmentVariable, 'SCRIBE_VLM_TOKEN');
    expect(vlm.timeoutSeconds, 45);
    expect(vlm.allowRemote, isFalse);
    expect(
      await loadedStore.loadCliExecutable(),
      r'C:\Tools\ScribeForge\scribeforge.exe',
    );

    final saved = await File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    ).readAsString();
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

  test('saves report success through SettingsSaveResult', () async {
    final store = DesktopSettingsStore(directory: temporary);
    final result = await store.saveCliExecutable('/opt/scribeforge');
    expect(result.ok, isTrue);
    expect(result.error, isNull);
  });

  test('100+ concurrent saves keep every field', () async {
    final store = DesktopSettingsStore(directory: temporary);
    final futures = <Future<SettingsSaveResult>>[];
    for (var i = 0; i < 120; i++) {
      futures.add(store.saveCliExecutable('/cli-$i'));
      futures.add(store.saveBackendPython('/python-$i'));
      futures.add(store.saveBackendSourcePath('/src-$i'));
      futures.add(store.saveLastProjectStore('/store-$i'));
      futures.add(
        store.saveVlmSettings(
          DesktopVlmSettings(
            enabled: true,
            endpoint: 'http://127.0.0.1:$i/v1',
            model: 'model-$i',
            timeoutSeconds: 30 + i.toDouble(),
          ),
        ),
      );
    }
    final results = await Future.wait(futures);
    expect(results.every((result) => result.ok), isTrue);

    // After the serialized queue drains, exactly one value per field
    // survives and the file is still valid JSON.
    final reloaded = DesktopSettingsStore(directory: temporary);
    expect(await reloaded.loadCliExecutable(), startsWith('/cli-'));
    expect(await reloaded.loadBackendPython(), startsWith('/python-'));
    expect(await reloaded.loadBackendSourcePath(), startsWith('/src-'));
    expect(await reloaded.loadLastProjectStore(), startsWith('/store-'));
    final vlm = await reloaded.loadVlmSettings();
    expect(vlm.enabled, isTrue);
    expect(vlm.model, startsWith('model-'));
    expect(vlm.endpoint, contains('/v1'));
  });

  test(
    'an interrupted write leaves the previous settings.json intact',
    () async {
      var crashNext = false;
      final store = DesktopSettingsStore(
        directory: temporary,
        beforeRename: (tmpFile, bytes) async {
          if (crashNext) {
            crashNext = false;
            throw StateError('simulated crash before rename');
          }
        },
      );
      final first = await store.saveCliExecutable('/good/cli');
      expect(first.ok, isTrue);

      crashNext = true;
      final crashed = await store.saveBackendPython('/broken/python');
      expect(crashed.ok, isFalse);
      expect(crashed.error, contains('simulated crash'));

      // The main file still parses and still holds the pre-crash value.
      final reloaded = DesktopSettingsStore(directory: temporary);
      expect(await reloaded.loadCliExecutable(), '/good/cli');
      expect(await reloaded.loadBackendPython(), isEmpty);

      // A later save succeeds and the queue is not wedged by the failure.
      final retry = await store.saveBackendPython('/fixed/python');
      expect(retry.ok, isTrue);
      expect(await reloaded.loadBackendPython(), '/fixed/python');
    },
  );

  test(
    'a corrupt settings.json is quarantined, backed up and reported',
    () async {
      final settingsFile = File(
        '${temporary.path}${Platform.pathSeparator}settings.json',
      );
      await settingsFile.writeAsString('{not json');
      SettingsCorruption? reported;
      final store = DesktopSettingsStore(
        directory: temporary,
        onCorruption: (corruption) => reported = corruption,
      );

      expect(await store.loadCliExecutable(), 'scribeforge');
      expect(store.pendingCorruption, isNotNull);
      expect(reported, isNotNull);
      expect(reported!.backupPath, contains('settings.corrupt-'));
      expect(await File(reported!.backupPath).exists(), isTrue);
      expect(await File(reported!.backupPath).readAsString(), '{not json');
      // The corrupt file no longer sits at the main path.
      expect(await settingsFile.exists(), isFalse);

      store.clearPendingCorruption();
      expect(store.pendingCorruption, isNull);

      // The store is usable again immediately after quarantine.
      final result = await store.saveCliExecutable('/recovered/cli');
      expect(result.ok, isTrue);
      expect(
        await DesktopSettingsStore(directory: temporary).loadCliExecutable(),
        '/recovered/cli',
      );
    },
  );

  test('a non-object JSON settings file is treated as corruption', () async {
    final settingsFile = File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    );
    await settingsFile.writeAsString('[1, 2, 3]');
    final store = DesktopSettingsStore(directory: temporary);
    expect(await store.loadLastProjectStore(), isNull);
    expect(store.pendingCorruption, isNotNull);
  });

  test('update() applies a mutation atomically and reports failures', () async {
    final store = DesktopSettingsStore(directory: temporary);
    final result = await store.update((settings) {
      settings['custom_key'] = 'custom-value';
      settings['cli_executable'] = '/cli';
    });
    expect(result.ok, isTrue);
    final raw = await File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    ).readAsString();
    expect(raw, contains('custom_key'));
    expect(raw, contains('/cli'));
  });
}
