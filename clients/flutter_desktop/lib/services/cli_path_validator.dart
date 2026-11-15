import 'dart:async';
import 'dart:convert';
import 'dart:io';

enum CliPathProbeStatus {
  ok,
  empty,
  notFound,
  isDirectory,
  notExecutable,
  launchFailed,
  timedOut,
  cancelled,
  rejected,
}

/// Result of probing a user-supplied CLI path before it is persisted.
class CliPathProbeResult {
  const CliPathProbeResult({
    required this.status,
    required this.path,
    this.exitCode,
    this.output = '',
    this.hint,
  });

  final CliPathProbeStatus status;
  final String path;
  final int? exitCode;
  final String output;

  /// Actionable, user-facing fix suggestion for the failing status.
  final String? hint;

  bool get ok => status == CliPathProbeStatus.ok;
}

/// Raw outcome of running the probe command.
class CliProcessOutcome {
  const CliProcessOutcome({
    required this.exitCode,
    required this.stdout,
    required this.stderr,
    this.timedOut = false,
    this.cancelled = false,
    this.launchError,
  });

  factory CliProcessOutcome.launchFailed(String message) => CliProcessOutcome(
    exitCode: -1,
    stdout: '',
    stderr: message,
    launchError: message,
  );

  final int exitCode;
  final String stdout;
  final String stderr;
  final bool timedOut;
  final bool cancelled;
  final String? launchError;

  bool get launchFailed => launchError != null;
}

typedef CliProcessRunner = Future<CliProcessOutcome> Function(
  String executable,
  List<String> arguments, {
  bool Function()? isCancelled,
});

/// Minimal file metadata the validator needs; avoids mocking `FileStat`.
class CliPathStatResult {
  const CliPathStatResult({required this.type, required this.permissions});

  final FileSystemEntityType type;

  /// POSIX permission bits (from `FileStat.mode`).
  final int permissions;
}

typedef CliPathStat = Future<CliPathStatResult?> Function(String path);

/// Validates a user-supplied ScribeForge CLI path *before* it is persisted
/// (issue #221): the path must point to an existing, executable file, and
/// running `<path> --help` must exit 0 within [timeout].
///
/// A bare command name (no path separator) is treated as a PATH lookup and
/// only the process probe decides. Every I/O primitive is injectable so
/// tests never spawn real processes.
class CliPathValidator {
  const CliPathValidator({
    this.timeout = const Duration(seconds: 10),
    this.processRunner,
    this.stat,
    this.operatingSystem,
    this.probeArguments = defaultProbeArguments,
  });

  final Duration timeout;
  final CliProcessRunner? processRunner;
  final CliPathStat? stat;
  final String? operatingSystem;

  /// Command used to prove the candidate actually runs. `--help` is handled
  /// by argparse for every CLI version we ship; interpreters use
  /// `--version`. Both exit 0 without touching the network or the store.
  final List<String> probeArguments;

  static const List<String> defaultProbeArguments = <String>['--help'];

  Future<CliPathProbeResult> probe(
    String candidate, {
    bool Function()? isCancelled,
  }) async {
    final path = candidate.trim();
    if (path.isEmpty) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.empty,
        path: path,
        hint: '请输入命令行工具路径，例如 C:\\ScribeForge\\backend\\scribeforge.exe。',
      );
    }
    final hasSeparator = path.contains('/') || path.contains(r'\');
    if (hasSeparator) {
      final fileStat = await _statOf(path);
      if (fileStat == null) {
        return CliPathProbeResult(
          status: CliPathProbeStatus.notFound,
          path: path,
          hint: '路径不存在：$path。请确认安装位置后重新输入。',
        );
      }
      if (fileStat.type == FileSystemEntityType.directory) {
        return CliPathProbeResult(
          status: CliPathProbeStatus.isDirectory,
          path: path,
          hint: '这是一个目录而不是可执行文件：$path。请指向 scribeforge 可执行文件本身。',
        );
      }
      if (!_looksExecutable(path, fileStat)) {
        return CliPathProbeResult(
          status: CliPathProbeStatus.notExecutable,
          path: path,
          hint: '该文件没有可执行权限：$path。请先赋予执行权限（或改用 .exe / py launcher）。',
        );
      }
    }
    return _runProbe(path, isCancelled: isCancelled);
  }

  Future<CliPathProbeResult> _runProbe(
    String path, {
    bool Function()? isCancelled,
  }) async {
    final runner = processRunner ?? _runProcess;
    final CliProcessOutcome outcome;
    try {
      outcome = await runner(path, probeArguments, isCancelled: isCancelled)
          .timeout(
            timeout,
            onTimeout: () => const CliProcessOutcome(
              exitCode: -1,
              stdout: '',
              stderr: '',
              timedOut: true,
            ),
          );
    } on ProcessException catch (error) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.launchFailed,
        path: path,
        output: '$error',
        hint: '无法启动该程序：$error。请确认路径指向可执行文件（Windows 上需 .exe）。',
      );
    }
    if (outcome.cancelled || (isCancelled?.call() ?? false)) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.cancelled,
        path: path,
        hint: '检测已取消；未保存任何配置。',
      );
    }
    if (outcome.timedOut) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.timedOut,
        path: path,
        hint: '运行 "$path --help" 超过 ${timeout.inSeconds} 秒未退出，已判定为不可用。',
      );
    }
    if (outcome.launchFailed) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.launchFailed,
        path: path,
        output: outcome.stderr,
        hint: '无法启动该程序：${outcome.launchError}。',
      );
    }
    final output = '${outcome.stdout}${outcome.stderr}'.trim();
    if (outcome.exitCode != 0) {
      return CliPathProbeResult(
        status: CliPathProbeStatus.rejected,
        path: path,
        exitCode: outcome.exitCode,
        output: output,
        hint:
            '"$path --help" 退出码为 ${outcome.exitCode}（应为 0）。'
            '该文件可能损坏或不是 ScribeForge CLI。',
      );
    }
    return CliPathProbeResult(
      status: CliPathProbeStatus.ok,
      path: path,
      exitCode: outcome.exitCode,
      output: output,
      hint: '已检测到可运行的 ScribeForge CLI（--help 退出码 0）。',
    );
  }

  Future<CliPathStatResult?> _statOf(String path) async {
    final stat = this.stat;
    if (stat != null) return stat(path);
    try {
      final file = File(path);
      if (!await file.exists()) return null;
      final fileStat = await file.stat();
      return CliPathStatResult(
        type: fileStat.type,
        permissions: fileStat.mode,
      );
    } on FileSystemException {
      return null;
    }
  }

  bool _looksExecutable(String path, CliPathStatResult fileStat) {
    final os = operatingSystem ?? Platform.operatingSystem;
    if (os == 'windows') {
      // On Windows executability is decided by the extension; the process
      // probe below rejects anything that cannot actually start.
      const executableExtensions = <String>['.exe', '.bat', '.cmd', '.com'];
      final lower = path.toLowerCase();
      return executableExtensions.any(lower.endsWith);
    }
    // POSIX: require at least one execute bit (0x49 == 0o111).
    return fileStat.permissions & 0x49 != 0;
  }

  Future<CliProcessOutcome> _runProcess(
    String executable,
    List<String> arguments, {
    bool Function()? isCancelled,
  }) async {
    final Process process;
    try {
      process = await Process.start(executable, arguments);
    } on ProcessException catch (error) {
      return CliProcessOutcome.launchFailed('$error');
    }
    final stdoutBuffer = StringBuffer();
    final stderrBuffer = StringBuffer();
    final stdoutDone = process.stdout
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(stdoutBuffer.write);
    final stderrDone = process.stderr
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(stderrBuffer.write);
    Timer? cancelTimer;
    if (isCancelled != null) {
      cancelTimer = Timer.periodic(const Duration(milliseconds: 100), (timer) {
        if (isCancelled()) {
          timer.cancel();
          process.kill();
        }
      });
    }
    var timedOut = false;
    final exitCode = await process.exitCode.timeout(
      timeout,
      onTimeout: () {
        timedOut = true;
        process.kill();
        return -1;
      },
    );
    cancelTimer?.cancel();
    await Future.wait<void>(<Future<void>>[stdoutDone, stderrDone]);
    await process.stdin.close();
    return CliProcessOutcome(
      exitCode: exitCode,
      stdout: stdoutBuffer.toString(),
      stderr: stderrBuffer.toString(),
      timedOut: timedOut,
      cancelled: isCancelled?.call() ?? false,
    );
  }
}
