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

/// Exit code produced by the Windows Microsoft Store app-execution alias
/// stub when a real Python is missing.
const int storeAliasExitCode = 9009;

/// Actionable hint when venv creation fails because the chosen interpreter is
/// the Windows Store placeholder or lacks the venv module.
String venvFailureHint(int exitCode, String log) {
  final storeStub =
      exitCode == storeAliasExitCode || log.contains('Microsoft Store');
  if (storeStub) {
    return '当前 Python 指向了 Windows Microsoft Store 占位符（退出码 $exitCode），'
        '它无法创建虚拟环境。请任选其一：\n'
        '• 重新安装 Python（python.org，勾选 “Add python.exe to PATH”），或\n'
        '• 使用 Windows 的 py 启动器（本应用会自动尝试 `py -3`），或\n'
        '• 前往「设置 → 应用 → 高级应用设置 → 应用执行别名」，关闭 python / python3 的别名开关。';
  }
  return '该解释器缺少 venv/ensurepip 模块（常见于未安装 python3-venv 的 Linux）。'
      '请安装完整 Python 或 python3-venv 后重试。';
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

  Future<bool> _exists(String path) => (fileExists ?? _defaultFileExists)(path);

  Future<String> _read(String path) => (readFile ?? _defaultReadFile)(path);

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
  String defaultVenvDirectory(String settingsDirectory) =>
      joinForOs(<String>[settingsDirectory, 'backend-venv'], _os);

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
    final normalized = _os == 'windows' ? path.replaceAll('/', r'\') : path;
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
    List<String> pythonArgs = const <String>[],
    void Function(String chunk)? onLog,
  }) async {
    final buffer = StringBuffer();
    void log(String line) {
      buffer.writeln(line);
      onLog?.call(line);
    }

    final pyproject = joinForOs(<String>[
      backendSourcePath,
      'pyproject.toml',
    ], _os);
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

    // Fail fast when the interpreter cannot create venvs at all (Store alias
    // stub, or a system Python without venv/ensurepip) instead of failing
    // later with a confusing exit code.
    final capabilityArgs = <String>[
      ...pythonArgs,
      '-c',
      'import venv, ensurepip',
    ];
    log('\$ $pythonExecutable ${capabilityArgs.join(' ')}');
    var code = await runner(pythonExecutable, capabilityArgs, onOutput: log);
    if (code != 0) {
      final error =
          '所选 Python 无法创建虚拟环境（退出码 $code）。\n${venvFailureHint(code, buffer.toString())}';
      return BackendProvisionResult(
        ok: false,
        cliExecutable: '',
        log: buffer.toString(),
        error: error,
      );
    }

    final venvCommand = <String>[...pythonArgs, '-m', 'venv', venvDirectory];
    log('\$ $pythonExecutable ${venvCommand.join(' ')}');
    code = await runner(pythonExecutable, venvCommand, onOutput: log);
    if (code != 0) {
      final error =
          '创建虚拟环境失败（退出码 $code）。\n${venvFailureHint(code, buffer.toString())}';
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
    log(
      r'$ ${paths.pythonExecutable} ${pipArgs.join('
      ')}',
    );
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
      final error = '安装完成但未找到 ${paths.cliExecutable}；请确认选择了正确的源码目录。';
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
