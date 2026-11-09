import 'dart:convert';
import 'dart:io';

/// Joins [parts] with the separator of the given operating system.
///
/// Tests inject a foreign [operatingSystem] (for example 'windows' on a Linux
/// CI runner), so the separator must follow the injected OS, not the host.
String joinForOs(List<String> parts, String operatingSystem) {
  final separator = operatingSystem == 'windows' ? r'\' : '/';
  if (operatingSystem != 'windows') return parts.join(separator);
  return parts.map((part) => part.replaceAll('/', r'\')).join(separator);
}

/// Well-known executable locations inside a managed backend virtual environment.
class BackendVenvPaths {
  const BackendVenvPaths(this.venvDirectory, {this.operatingSystem});

  final String venvDirectory;
  final String? operatingSystem;

  String get _os => operatingSystem ?? Platform.operatingSystem;

  String get pythonExecutable => joinForOs(
    _os == 'windows'
        ? <String>[venvDirectory, 'Scripts', 'python.exe']
        : <String>[venvDirectory, 'bin', 'python'],
    _os,
  );

  String get cliExecutable => joinForOs(
    _os == 'windows'
        ? <String>[venvDirectory, 'Scripts', 'scribeforge.exe']
        : <String>[venvDirectory, 'bin', 'scribeforge'],
    _os,
  );
}

class BackendProvisionResult {
  const BackendProvisionResult({
    required this.ok,
    required this.cliExecutable,
    required this.log,
    this.error,
  });

  final bool ok;
  final String cliExecutable;
  final String log;
  final String? error;
}

typedef BackendProcessRunner = Future<int> Function(
  String executable,
  List<String> arguments, {
  void Function(String chunk)? onOutput,
});

typedef BackendFileExists = Future<bool> Function(String path);
typedef BackendFileReader = Future<String> Function(String path);

/// Creates a backend virtual environment from inside the desktop client.
///
/// The whole point of this service is that users never type venv/pip commands:
/// the client runs `python -m venv`, `pip install -e <source>` and a `--help`
/// smoke check itself, then points the CLI setting at the resulting
/// `scribeforge` executable. All process and file access is injectable so the
/// command sequence is covered by deterministic unit tests.
class BackendEnvironmentProvisioner {
  const BackendEnvironmentProvisioner({
    this.runProcess,
    this.fileExists,
    this.readFile,
    this.operatingSystem,
  });

  final BackendProcessRunner? runProcess;
  final BackendFileExists? fileExists;
  final BackendFileReader? readFile;
  final String? operatingSystem;

  String get _os => operatingSystem ?? Platform.operatingSystem;

  Future<bool> _exists(String path) =>
      (fileExists ?? _defaultFileExists)(path);

  Future<String> _read(String path) =>
      (readFile ?? _defaultReadFile)(path);

  static Future<bool> _defaultFileExists(String path) =>
      Future<bool>.value(File(path).existsSync());

  static Future<String> _defaultReadFile(String path) =>
      File(path).readAsString();

  BackendProcessRunner get _runner => runProcess ?? _defaultRunner;

  static Future<int> _defaultRunner(
    String executable,
    List<String> arguments, {
    void Function(String chunk)? onOutput,
  }) async {
    final process = await Process.start(executable, arguments);
    process.stdout.transform(utf8.decoder).listen(onOutput);
    process.stderr.transform(utf8.decoder).listen(onOutput);
    return process.exitCode;
  }

  /// Default venv location: a `backend-venv` folder inside the settings dir.
  String defaultVenvDirectory(String settingsDirectory) => joinForOs(
    <String>[settingsDirectory, 'backend-venv'],
    _os,
  );

  /// Walks up from [startDirectory] looking for the ScribeForge source
  /// checkout (a `pyproject.toml` declaring the `scribeforge` project).
  Future<String?> detectBackendSourcePath({
    String? startDirectory,
    int maxLevels = 8,
  }) async {
    var current = startDirectory ?? Directory.current.path;
    for (var level = 0; level < maxLevels; level++) {
      final pyproject = joinForOs(<String>[current, 'pyproject.toml'], _os);
      if (await _exists(pyproject)) {
        try {
          final content = await _read(pyproject);
          if (content.contains('name = "scribeforge"')) return current;
        } on Object {
          // Unreadable pyproject: keep walking up.
        }
      }
      final parent = _dirname(current);
      if (parent == current) return null;
      current = parent;
    }
    return null;
  }

  String _dirname(String path) {
    final separator = _os == 'windows' ? r'\' : '/';
    final normalized = _os == 'windows'
        ? path.replaceAll('/', r'\')
        : path;
    final index = normalized.lastIndexOf(separator);
    if (index < 0) return path;
    if (index == 0) return separator;
    return normalized.substring(0, index);
  }

  /// Creates (or reuses) the venv, installs the backend editable, and verifies
  /// the resulting CLI. Never throws: failures are reported in the result so
  /// the UI can show the captured log.
  Future<BackendProvisionResult> provision({
    required String pythonExecutable,
    required String backendSourcePath,
    required String venvDirectory,
    void Function(String chunk)? onLog,
  }) async {
    final buffer = StringBuffer();
    void log(String line) {
      buffer.writeln(line);
      onLog?.call(line);
    }

    final pyproject = joinForOs(
      <String>[backendSourcePath, 'pyproject.toml'],
      _os,
    );
    if (!await _exists(pyproject)) {
      const error =
          '所选目录不是 ScribeForge 源码仓库（缺少 pyproject.toml）。'
          '请选择仓库根目录（包含 pyproject.toml 的那一层）。';
      log(error);
      return BackendProvisionResult(
        ok: false,
        cliExecutable: '',
        log: buffer.toString(),
        error: error,
      );
    }

    final paths = BackendVenvPaths(venvDirectory, operatingSystem: _os);
    final runner = _runner;

    log(r'$ $pythonExecutable -m venv $venvDirectory');
    var code = await runner(
      pythonExecutable,
      <String>['-m', 'venv', venvDirectory],
      onOutput: log,
    );
    if (code != 0) {
      final error = '创建虚拟环境失败（退出码 $code），见上方日志。';
      return BackendProvisionResult(
        ok: false,
        cliExecutable: '',
        log: buffer.toString(),
        error: error,
      );
    }

    final pipArgs = <String>[
      '-m',
      'pip',
      'install',
      '--quiet',
      '-e',
      backendSourcePath,
    ];
    log(r'$ ${paths.pythonExecutable} ${pipArgs.join(' ')}');
    code = await runner(paths.pythonExecutable, pipArgs, onOutput: log);
    if (code != 0) {
      final error = '安装后端到虚拟环境失败（退出码 $code），见上方日志。';
      return BackendProvisionResult(
        ok: false,
        cliExecutable: '',
        log: buffer.toString(),
        error: error,
      );
    }

    if (!await _exists(paths.cliExecutable)) {
      final error =
          '安装完成但未找到 ${paths.cliExecutable}；请确认选择了正确的源码目录。';
      log(error);
      return BackendProvisionResult(
        ok: false,
        cliExecutable: '',
        log: buffer.toString(),
        error: error,
      );
    }

    log(r'$ ${paths.cliExecutable} --help');
    code = await runner(paths.cliExecutable, <String>['--help'], onOutput: log);
    if (code != 0) {
      final error = '后端已安装但 `--help` 冒烟检查失败（退出码 $code）。';
      return BackendProvisionResult(
        ok: false,
        cliExecutable: paths.cliExecutable,
        log: buffer.toString(),
        error: error,
      );
    }

    log('后端环境就绪。');
    return BackendProvisionResult(
      ok: true,
      cliExecutable: paths.cliExecutable,
      log: buffer.toString(),
    );
  }
}
