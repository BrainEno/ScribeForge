import 'dart:io';

import 'cli_runner.dart';

enum DesktopBackendOrigin {
  bundled,
  provisionedVenv,
  custom,
  developmentPath,
}

class DesktopBackendResolution {
  const DesktopBackendResolution({
    required this.executable,
    required this.origin,
  });

  final String executable;
  final DesktopBackendOrigin origin;

  bool get usesBundledBackend => origin == DesktopBackendOrigin.bundled;
}

typedef DesktopBackendFileExists = Future<bool> Function(String path);

/// Finds the frozen backend shipped with a desktop distribution.
///
/// A user-supplied path is authoritative. Otherwise the side-by-side backend
/// wins over PATH, which keeps end users independent from a developer's Python
/// installation. PATH remains a deliberate development fallback only.
class DesktopBackendResolver {
  const DesktopBackendResolver({
    this.applicationExecutablePath,
    this.operatingSystem,
    this.fileExists,
  });

  final String? applicationExecutablePath;
  final String? operatingSystem;
  final DesktopBackendFileExists? fileExists;

  Future<DesktopBackendResolution> resolve(
    String configuredExecutable, {
    String? venvCliPath,
  }) async {
    final configured = configuredExecutable.trim();
    if (configured.isNotEmpty &&
        configured != ScribeForgeCli.defaultExecutable) {
      return DesktopBackendResolution(
        executable: configured,
        origin: DesktopBackendOrigin.custom,
      );
    }

    for (final candidate in _bundledCandidates()) {
      if (await _fileExists(candidate)) {
        return DesktopBackendResolution(
          executable: candidate,
          origin: DesktopBackendOrigin.bundled,
        );
      }
    }
    // A previously provisioned backend venv is the next best thing to a
    // bundled backend: it exists precisely because the user ran 初始化环境,
    // even when `scribeforge` was never added to PATH.
    final venvCli = venvCliPath?.trim() ?? '';
    if (venvCli.isNotEmpty && await _fileExists(venvCli)) {
      return DesktopBackendResolution(
        executable: venvCli,
        origin: DesktopBackendOrigin.provisionedVenv,
      );
    }
    return DesktopBackendResolution(
      executable: configured.isEmpty
          ? ScribeForgeCli.defaultExecutable
          : configured,
      origin: DesktopBackendOrigin.developmentPath,
    );
  }

  Future<bool> _fileExists(String path) =>
      (fileExists ?? _defaultFileExists)(path);

  static Future<bool> _defaultFileExists(String path) =>
      Future<bool>.value(File(path).existsSync());

  List<String> _bundledCandidates() {
    final os = operatingSystem ?? Platform.operatingSystem;
    final separator = os == 'windows' ? r'\' : '/';
    final executableName = os == 'windows' ? 'scribeforge.exe' : 'scribeforge';
    final application =
        applicationExecutablePath ?? Platform.resolvedExecutable;
    final applicationDirectory = _dirname(application, separator);
    final backendDirectory = _join(applicationDirectory, 'backend', separator);
    return <String>[
      _join(backendDirectory, executableName, separator),
      if (os == 'macos')
        _join(
          _join(
            _dirname(applicationDirectory, separator),
            'Resources/backend',
            separator,
          ),
          executableName,
          separator,
        ),
    ];
  }

  static String _dirname(String path, String separator) {
    final normalized = separator == r'\' ? path.replaceAll('/', r'\') : path;
    final index = normalized.lastIndexOf(separator);
    if (index < 0) return '.';
    if (index == 0) return separator;
    return normalized.substring(0, index);
  }

  static String _join(String parent, String child, String separator) {
    final normalizedChild = separator == r'\'
        ? child.replaceAll('/', r'\')
        : child;
    if (parent.endsWith(separator)) return '$parent$normalizedChild';
    return '$parent$separator$normalizedChild';
  }
}
