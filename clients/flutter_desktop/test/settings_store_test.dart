import 'dart:convert';
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

  test('separate store instances serialize updates to the same file', () async {
    final stores = List.generate(
      4,
      (_) => DesktopSettingsStore(directory: temporary),
    );
    final results = await Future.wait(
      List.generate(120, (index) {
        return stores[index % stores.length].update((settings) {
          settings['field-$index'] = index;
        });
      }),
    );
    expect(results.every((result) => result.ok), isTrue);
    final content = await File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    ).readAsString();
    final decoded = jsonDecode(content) as Map<String, dynamic>;
    expect(decoded.length, 120);
    for (var index = 0; index < 120; index++) {
      expect(decoded['field-$index'], index);
    }
  });

  test('invalid UTF-8 is quarantined byte for byte', () async {
    final file = File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    );
    final bytes = <int>[123, 255, 125];
    await file.writeAsBytes(bytes);
    final store = DesktopSettingsStore(directory: temporary);
    expect(await store.loadCliExecutable(), 'scribeforge');
    final corruption = store.pendingCorruption!;
    expect(corruption.backedUp, isTrue);
    expect(await File(corruption.backupPath).readAsBytes(), bytes);
  });

  test(
    'failed quarantine blocks saves until the original is repaired',
    () async {
      final file = File(
        '${temporary.path}${Platform.pathSeparator}settings.json',
      );
      await file.writeAsString('{broken');
      final store = DesktopSettingsStore(
        directory: temporary,
        quarantineHook: (source, backup) async {
          throw FileSystemException('access denied', source.path);
        },
      );
      expect(await store.loadCliExecutable(), 'scribeforge');
      expect(store.pendingCorruption!.backedUp, isFalse);
      store.clearPendingCorruption();
      expect(store.pendingCorruption, isNotNull);
      final save = await store.saveBackendPython('/new/python');
      expect(save.ok, isFalse);
      expect(save.error, contains('保存已暂停'));
      expect(await file.readAsString(), '{broken');
      await file.writeAsString('{"cli_executable":"/repaired"}');
      expect((await store.saveBackendPython('/new/python')).ok, isTrue);
      expect(await store.loadCliExecutable(), '/repaired');
      expect(await store.loadBackendPython(), '/new/python');
    },
  );

  test(
    'repeated corruptions at the same timestamp preserve both backups',
    () async {
      final file = File(
        '${temporary.path}${Platform.pathSeparator}settings.json',
      );
      final store = DesktopSettingsStore(
        directory: temporary,
        clock: () => DateTime.utc(2026),
      );
      await file.writeAsString('{first');
      await store.loadCliExecutable();
      final first = store.pendingCorruption!.backupPath;
      await file.writeAsString('{second');
      await store.loadCliExecutable();
      final second = store.pendingCorruption!.backupPath;
      expect(first, isNot(second));
      expect(await File(first).readAsString(), '{first');
      expect(await File(second).readAsString(), '{second');
    },
  );

  test(
    'read I/O failures do not overwrite or quarantine settings and allow retry',
    () async {
      final file = File(
        '${temporary.path}${Platform.pathSeparator}settings.json',
      );
      await file.writeAsString(
        '{"cli_executable":"/existing","backend_python":"/python"}',
      );
      final original = await file.readAsBytes();
      var denied = true;
      final store = DesktopSettingsStore(
        directory: temporary,
        readBytes: (file) async {
          if (denied) {
            throw FileSystemException(
              'access denied',
              file.path,
              const OSError('denied', 13),
            );
          }
          return file.readAsBytes();
        },
      );
      await expectLater(
        store.loadCliExecutable(),
        throwsA(isA<FileSystemException>()),
      );
      final failed = await store.saveBackendPython('/new');
      expect(failed.ok, isFalse);
      expect(await file.readAsBytes(), original);
      expect(store.pendingCorruption, isNull);
      final lock = File(
        '${temporary.path}${Platform.pathSeparator}settings.lock',
      );
      expect(await lock.exists(), isTrue);
      expect(
        await temporary
            .list()
            .where((item) => item.path.contains('settings.corrupt-'))
            .isEmpty,
        isTrue,
      );
      denied = false;
      expect(await store.loadCliExecutable(), '/existing');
      expect((await store.saveBackendPython('/new')).ok, isTrue);
      expect(await store.loadCliExecutable(), '/existing');
      expect(await store.loadBackendPython(), '/new');
    },
  );

  test('non-missing directory failures do not become empty settings', () async {
    final store = DesktopSettingsStore(
      directory: temporary,
      readBytes: (file) async => throw FileSystemException(
        'I/O error',
        file.path,
        const OSError('I/O error', 5),
      ),
    );
    await expectLater(
      store.loadVlmSettings(),
      throwsA(isA<FileSystemException>()),
    );
    expect((await store.saveBackendPython('/new')).ok, isFalse);
    final settingsFile = File(
      '${temporary.path}${Platform.pathSeparator}settings.json',
    );
    expect(await settingsFile.exists(), isFalse);
  });
}
