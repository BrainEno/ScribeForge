import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/runtime_location.dart';

/// Outcome of a single settings save attempt.
///
/// Saves never throw for I/O problems: the UI decides how to present
/// [error] and keeps the user's input so the save can be retried.
class SettingsSaveResult {
  const SettingsSaveResult._({required this.ok, this.error});

  factory SettingsSaveResult.ok() => const SettingsSaveResult._(ok: true);

  factory SettingsSaveResult.failure(String error) =>
      SettingsSaveResult._(ok: false, error: error);

  final bool ok;
  final String? error;
}

/// A `settings.json` that existed but could not be parsed.
///
/// The original bytes are preserved in [backupPath]; the app continues with
/// default settings instead of silently discarding user configuration.
class SettingsCorruption {
  const SettingsCorruption({
    required this.backupPath,
    required this.detectedAt,
  });

  final String backupPath;
  final DateTime detectedAt;

  String get message =>
      '配置文件 settings.json 已损坏，原始内容已备份为 '
      '$backupPath；本次启动使用默认设置。';
}

typedef SettingsClock = DateTime Function();

/// Test hook invoked after the temporary file is fully written and fsynced
/// but before it is renamed over `settings.json`; throwing simulates a crash
/// in the middle of a write.
typedef SettingsWriteHook =
    Future<void> Function(File tmpFile, List<int> bytes);

class DesktopVlmSettings {
  const DesktopVlmSettings({
    this.enabled = false,
    this.endpoint = '',
    this.model = '',
    this.apiKeyEnvironmentVariable = '',
    this.timeoutSeconds = 120,
    this.allowRemote = false,
  });

  final bool enabled;
  final String endpoint;
  final String model;
  final String apiKeyEnvironmentVariable;
  final double timeoutSeconds;
  final bool allowRemote;

  bool get isConfigured =>
      endpoint.trim().isNotEmpty && model.trim().isNotEmpty;

  factory DesktopVlmSettings.fromJson(Object? value) {
    if (value is! Map) return const DesktopVlmSettings();
    final json = Map<String, dynamic>.from(value);
    final rawTimeout = json['timeout_seconds'];
    final timeout = rawTimeout is num && rawTimeout.isFinite && rawTimeout > 0
        ? rawTimeout.toDouble()
        : 120.0;
    return DesktopVlmSettings(
      enabled: json['enabled'] == true,
      endpoint: _stringValue(json['endpoint']),
      model: _stringValue(json['model']),
      apiKeyEnvironmentVariable: _stringValue(
        json['api_key_environment_variable'],
      ),
      timeoutSeconds: timeout,
      allowRemote: json['allow_remote'] == true,
    );
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'enabled': enabled,
    'endpoint': endpoint.trim(),
    'model': model.trim(),
    'api_key_environment_variable': apiKeyEnvironmentVariable.trim(),
    'timeout_seconds': timeoutSeconds,
    'allow_remote': allowRemote,
  };

  static String _stringValue(Object? value) =>
      value is String ? value.trim() : '';
}

/// Single source of truth for the desktop app's persisted settings.
///
/// Persistence contract (issue #216/#218):
/// - All writes go through a serialized queue, so concurrent read-modify-
///   write cycles can never interleave and drop fields.
/// - Every write is atomic: the new JSON is written to `settings.json.tmp`,
///   fsynced, then renamed over `settings.json`. A crash mid-write leaves
///   either the old file or the tmp file — never a torn main file.
/// - A corrupt main file is quarantined to `settings.corrupt-<stamp>.json`
///   and surfaced via [pendingCorruption] / [onCorruption] so the UI can
///   show a visible recovery notice instead of silently resetting.
class DesktopSettingsStore {
  DesktopSettingsStore({
    this.directory,
    this.clock,
    this.onCorruption,
    this.beforeRename,
  });

  static const String defaultCliExecutable = 'scribeforge';
  static const String settingsFileName = 'settings.json';

  final Directory? directory;
  final SettingsClock? clock;
  final void Function(SettingsCorruption corruption)? onCorruption;
  final SettingsWriteHook? beforeRename;

  Future<void> _queue = Future<void>.value();
  SettingsCorruption? _pendingCorruption;

  /// The most recent corruption detected while reading, if any. The UI
  /// reads this once at startup to show a recovery banner.
  SettingsCorruption? get pendingCorruption => _pendingCorruption;

  void clearPendingCorruption() => _pendingCorruption = null;

  Future<String> loadCliExecutable() async {
    final settings = await _readSettings();
    final value = settings['cli_executable'];
    if (value is String && value.trim().isNotEmpty) return value.trim();
    return defaultCliExecutable;
  }

  Future<SettingsSaveResult> saveCliExecutable(String executable) async {
    final value = executable.trim();
    if (value.isEmpty) {
      throw ArgumentError.value(executable, 'executable', 'must not be empty');
    }
    return update((settings) => settings['cli_executable'] = value);
  }

  /// Directory that holds `settings.json`; the managed backend venv lives
  /// beside it as `backend-venv`.
  Directory get settingsDirectory => directory ?? _defaultDirectory();

  Future<String> loadBackendPython() async {
    final settings = await _readSettings();
    final value = settings['backend_python'];
    return value is String ? value.trim() : '';
  }

  Future<SettingsSaveResult> saveBackendPython(String pythonExecutable) {
    return update(
      (settings) => settings['backend_python'] = pythonExecutable.trim(),
    );
  }

  Future<String> loadBackendSourcePath() async {
    final settings = await _readSettings();
    final value = settings['backend_source_path'];
    return value is String ? value.trim() : '';
  }

  Future<SettingsSaveResult> saveBackendSourcePath(String sourcePath) {
    return update(
      (settings) => settings['backend_source_path'] = sourcePath.trim(),
    );
  }

  /// Last successfully opened project store directory, if any remembered.
  Future<String?> loadLastProjectStore() async {
    final settings = await _readSettings();
    final value = settings['last_project_store'];
    return value is String && value.trim().isNotEmpty ? value.trim() : null;
  }

  /// Remember [storePath] as the last opened project; an empty value clears
  /// the memory (used when the remembered store turns out to be gone).
  Future<SettingsSaveResult> saveLastProjectStore(String storePath) {
    final value = storePath.trim();
    return update((settings) {
      if (value.isEmpty) {
        settings.remove('last_project_store');
      } else {
        settings['last_project_store'] = value;
      }
    });
  }

  Future<RuntimeLocationPreferences> loadRuntimeLocations() async {
    final settings = await _readSettings();
    return RuntimeLocationPreferences.fromJson(settings['runtime_locations']);
  }

  Future<SettingsSaveResult> activateRuntimeLocation({
    required RuntimeLocation expected,
    required RuntimeLocation next,
  }) => update((settings) {
    final saved = RuntimeLocationPreferences.fromJson(
      settings['runtime_locations'],
    );
    if (saved.active != expected) {
      throw StateError('运行目录设置已变更，请重新打开位置设置。');
    }
    settings['runtime_locations'] = saved.activate(next).toJson();
  });

  Future<DesktopVlmSettings> loadVlmSettings() async {
    final settings = await _readSettings();
    return DesktopVlmSettings.fromJson(settings['vlm_settings']);
  }

  Future<SettingsSaveResult> saveVlmSettings(
    DesktopVlmSettings settings,
  ) async {
    if (!settings.timeoutSeconds.isFinite || settings.timeoutSeconds <= 0) {
      throw ArgumentError.value(
        settings.timeoutSeconds,
        'timeoutSeconds',
        'must be finite and greater than zero',
      );
    }
    if (settings.enabled && !settings.isConfigured) {
      throw ArgumentError(
        'VLM review requires both an endpoint and a model when enabled.',
      );
    }
    return update(
      (allSettings) => allSettings['vlm_settings'] = settings.toJson(),
    );
  }

  /// Serialized read-modify-write. [mutation] receives the current settings
  /// map (already queued behind any pending write) and may change it in
  /// place. I/O failures are reported as a failed [SettingsSaveResult]
  /// instead of throwing, so callers can keep the user's input and retry.
  Future<SettingsSaveResult> update(
    void Function(Map<String, dynamic> settings) mutation,
  ) {
    return _enqueue(() async {
      try {
        final current = await _readSettings();
        mutation(current);
        await _writeSettingsAtomic(current);
        return SettingsSaveResult.ok();
      } on Object catch (error) {
        return SettingsSaveResult.failure('$error');
      }
    });
  }

  Future<T> _enqueue<T>(Future<T> Function() task) {
    final result = _queue.then((_) => task());
    // The queue itself must survive a failed task.
    _queue = result.then((_) => null, onError: (_) => null);
    return result;
  }

  Future<Map<String, dynamic>> _readSettings() async {
    final file = _settingsFile;
    if (!await file.exists()) return <String, dynamic>{};
    final raw = await file.readAsString();
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
      throw const FormatException('settings.json must contain a JSON object');
    } on Object {
      // A corrupted settings file must not prevent the app from opening,
      // but it must not be silently discarded either: quarantine it.
      await _quarantineCorruptFile(file);
      return <String, dynamic>{};
    }
  }

  Future<void> _quarantineCorruptFile(File file) async {
    final now = (clock ?? DateTime.now)();
    final stamp = '${now.microsecondsSinceEpoch}';
    final backup = File(
      '${file.parent.path}${Platform.pathSeparator}'
      'settings.corrupt-$stamp.json',
    );
    try {
      await file.rename(backup.path);
    } on Object {
      try {
        await file.copy(backup.path);
        await file.delete();
      } on Object {
        // Even if quarantining fails the app stays usable with defaults.
        return;
      }
    }
    final corruption = SettingsCorruption(
      backupPath: backup.path,
      detectedAt: now,
    );
    _pendingCorruption = corruption;
    try {
      onCorruption?.call(corruption);
    } on Object {
      // A faulty listener must not break settings reads.
    }
  }

  Future<void> _writeSettingsAtomic(Map<String, dynamic> settings) async {
    final file = _settingsFile;
    await file.parent.create(recursive: true);
    final tmp = File('${file.path}.tmp');
    final bytes = utf8.encode(jsonEncode(settings));
    final raf = await tmp.open(mode: FileMode.write);
    try {
      await raf.writeFrom(bytes);
      // fsync: after the rename below, readers must never see a torn file.
      await raf.flush();
    } finally {
      await raf.close();
    }
    await beforeRename?.call(tmp, bytes);
    await tmp.rename(file.path);
  }

  File get _settingsFile {
    final directory = settingsDirectory;
    return File('${directory.path}${Platform.pathSeparator}$settingsFileName');
  }

  Directory _defaultDirectory() {
    final env = Platform.environment;
    final home = env['USERPROFILE'] ?? env['HOME'] ?? Directory.current.path;
    if (Platform.isWindows) {
      final appData = env['APPDATA'];
      final base = appData == null || appData.isEmpty
          ? '$home${Platform.pathSeparator}AppData${Platform.pathSeparator}Roaming'
          : appData;
      return Directory('$base${Platform.pathSeparator}ScribeForge');
    }
    if (Platform.isMacOS) {
      return Directory(
        '$home${Platform.pathSeparator}Library${Platform.pathSeparator}Application Support${Platform.pathSeparator}ScribeForge',
      );
    }
    final xdg = env['XDG_CONFIG_HOME'];
    final base = xdg != null && xdg.startsWith('/')
        ? xdg
        : '$home${Platform.pathSeparator}.config';
    return Directory('$base${Platform.pathSeparator}scribeforge');
  }
}
