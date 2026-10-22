import 'dart:convert';
import 'dart:io';

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

  bool get isConfigured => endpoint.trim().isNotEmpty && model.trim().isNotEmpty;

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
      apiKeyEnvironmentVariable:
          _stringValue(json['api_key_environment_variable']),
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

class DesktopSettingsStore {
  DesktopSettingsStore({this.directory});

  static const String defaultCliExecutable = 'scribeforge';

  final Directory? directory;

  Future<String> loadCliExecutable() async {
    final settings = await _readSettings();
    final value = settings['cli_executable'];
    if (value is String && value.trim().isNotEmpty) return value.trim();
    return defaultCliExecutable;
  }

  Future<void> saveCliExecutable(String executable) async {
    final value = executable.trim();
    if (value.isEmpty) {
      throw ArgumentError.value(executable, 'executable', 'must not be empty');
    }
    final settings = await _readSettings();
    settings['cli_executable'] = value;
    await _writeSettings(settings);
  }

  Future<DesktopVlmSettings> loadVlmSettings() async {
    final settings = await _readSettings();
    return DesktopVlmSettings.fromJson(settings['vlm_settings']);
  }

  Future<void> saveVlmSettings(DesktopVlmSettings settings) async {
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
    final allSettings = await _readSettings();
    allSettings['vlm_settings'] = settings.toJson();
    await _writeSettings(allSettings);
  }

  Future<Map<String, dynamic>> _readSettings() async {
    final file = _settingsFile;
    if (!await file.exists()) return <String, dynamic>{};
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is Map<String, dynamic>) {
        return Map<String, dynamic>.from(decoded);
      }
    } on Object {
      // A corrupted settings file should not prevent the app from opening.
    }
    return <String, dynamic>{};
  }

  Future<void> _writeSettings(Map<String, dynamic> settings) async {
    final file = _settingsFile;
    await file.parent.create(recursive: true);
    await file.writeAsString(jsonEncode(settings), flush: true);
  }

  File get _settingsFile {
    final settingsDirectory = directory ?? _defaultDirectory();
    return File(
      '${settingsDirectory.path}${Platform.pathSeparator}settings.json',
    );
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
