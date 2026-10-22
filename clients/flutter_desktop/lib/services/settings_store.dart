import 'dart:convert';
import 'dart:io';

class DesktopSettingsStore {
  DesktopSettingsStore({this.directory});

  static const String defaultCliExecutable = 'scribeforge';

  final Directory? directory;

  Future<String> loadCliExecutable() async {
    final file = _settingsFile;
    if (!await file.exists()) return defaultCliExecutable;
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is Map<String, dynamic>) {
        final value = decoded['cli_executable'];
        if (value is String && value.trim().isNotEmpty) return value.trim();
      }
    } on Object {
      // A corrupted settings file should not prevent the app from opening.
    }
    return defaultCliExecutable;
  }

  Future<void> saveCliExecutable(String executable) async {
    final value = executable.trim();
    if (value.isEmpty) {
      throw ArgumentError.value(executable, 'executable', 'must not be empty');
    }
    final file = _settingsFile;
    await file.parent.create(recursive: true);
    await file.writeAsString(
      jsonEncode(<String, String>{'cli_executable': value}),
      flush: true,
    );
  }

  File get _settingsFile {
    final settingsDirectory = directory ?? _defaultDirectory();
    return File('${settingsDirectory.path}${Platform.pathSeparator}settings.json');
  }

  Directory _defaultDirectory() {
    final env = Platform.environment;
    final home = env['USERPROFILE'] ?? env['HOME'] ?? Directory.current.path;
    if (Platform.isWindows) {
      final appData = env['APPDATA'];
      final base = appData == null || appData.isEmpty
          ? '${home}${Platform.pathSeparator}AppData${Platform.pathSeparator}Roaming'
          : appData;
      return Directory('${base}${Platform.pathSeparator}ScribeForge');
    }
    if (Platform.isMacOS) {
      return Directory(
        '${home}${Platform.pathSeparator}Library${Platform.pathSeparator}Application Support${Platform.pathSeparator}ScribeForge',
      );
    }
    final xdg = env['XDG_CONFIG_HOME'];
    final base = xdg != null && xdg.startsWith('/')
        ? xdg
        : '${home}${Platform.pathSeparator}.config';
    return Directory('${base}${Platform.pathSeparator}scribeforge');
  }
}
