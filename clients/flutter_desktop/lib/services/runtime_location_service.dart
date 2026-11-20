import '../models/runtime_location.dart';
import '../models/runtime_setup.dart';
import 'cli_executor.dart';
import 'cli_runner.dart';

/// Applies the active location to every OCR entry point, including those in
/// nested screens. Plain project operations and developer CLI probes keep their
/// original argument contract.
class RuntimeLocationExecutor implements ScribeForgeCliExecutor {
  const RuntimeLocationExecutor(this.delegate, this.current);

  final ScribeForgeCliExecutor delegate;
  final RuntimeLocation Function() current;

  @override
  Future<void> cancel() => delegate.cancel();

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) => delegate.run(
    executable: executable,
    arguments: current().argumentsFor(arguments),
    onOutput: onOutput,
    onError: onError,
  );
}

/// Candidate operations deliberately bypass [RuntimeLocationExecutor]. A
/// successful setup is not enough to persist a candidate: its canonical paths
/// and both ready states must be confirmed with a fresh report after setup.
class RuntimeLocationService {
  const RuntimeLocationService(this.executor, this.executable);

  final ScribeForgeCliExecutor executor;
  final String executable;

  Future<RuntimeLocationCheck> check(RuntimeLocation candidate) async {
    final output = _LimitedText(65536);
    final diagnostics = _LimitedText(8192);
    final code = await executor.run(
      executable: executable,
      arguments: candidate.argumentsFor(const <String>[
        'runtime-location-check',
      ], check: true),
      onOutput: output.add,
      onError: diagnostics.add,
    );
    if (output.truncated) {
      throw const FormatException('候选运行目录检查响应过长。');
    }
    try {
      final report = RuntimeLocationCheck.fromOutput(output.text);
      if (code != 0) {
        throw RuntimeLocationCheckFailure('exit_code', '检查退出码：$code');
      }
      return report;
    } on FormatException {
      if (code != 0 && diagnostics.text.trim().isNotEmpty) {
        throw RuntimeLocationCheckFailure('cli', diagnostics.text.trim());
      }
      rethrow;
    }
  }

  Future<void> cancel() => executor.cancel();

  Future<RuntimeLocationCheck> prepare({
    required RuntimeLocation selection,
    required RuntimeLocationCheck expected,
    required void Function(RuntimeSetupMessage) onProgress,
    required bool Function() isCancelled,
    String? activeRuntimeRoot,
  }) async {
    final candidate = expected.canonicalize(selection);
    final latest = await check(candidate);
    if (!latest.sameTarget(expected)) {
      throw const RuntimeLocationCheckFailure(
        'target_changed',
        '检查后目录指向已变化。请重新检查后再继续。',
      );
    }
    if (activeRuntimeRoot != null && latest.runtimeRoot == activeRuntimeRoot) {
      throw const RuntimeLocationCheckFailure(
        'active_directory',
        '此目录已在使用。请到现有环境设置检查或修复，不要从切换流程修改它。',
      );
    }
    if (!latest.disk.sufficient) {
      throw RuntimeLocationCheckFailure('disk_space', latest.disk.advice);
    }
    if (isCancelled()) throw const RuntimePreparationCancelled();
    final stderr = _LimitedText(12000);
    final stream = _SetupStream(onProgress);
    // Even the platform default is an explicit candidate; never reuse the
    // running profile wrapper or fall back to project-local environments.
    final explicit = RuntimeLocation(
      runtimeRoot: latest.runtimeRoot,
      modelsRoot: candidate.modelsRoot,
    );
    final code = await executor.run(
      executable: executable,
      arguments: explicit.argumentsFor(ScribeForgeCli.setupArguments()),
      onOutput: stream.add,
      onError: stderr.add,
    );
    stream.finish();
    if (isCancelled()) throw const RuntimePreparationCancelled();
    final result = stream.result;
    if (code != 0 ||
        stream.invalid ||
        result?.success != true ||
        !RuntimeLocationCheck.coreBackends.every(
          (name) => result!.backends[name]?.state == 'ready',
        )) {
      throw RuntimeLocationCheckFailure(
        'setup_failed',
        result?.message ??
            (stderr.text.trim().isEmpty
                ? '配置未返回两个引擎均已就绪的结果（退出码 $code）。'
                : stderr.text.trim()),
      );
    }
    final verified = await check(candidate);
    if (isCancelled()) throw const RuntimePreparationCancelled();
    if (!verified.sameTarget(expected) || !verified.bothReady) {
      throw const RuntimeLocationCheckFailure(
        'not_verified',
        '配置完成后未能确认原目录和两个引擎的就绪状态，未切换。',
      );
    }
    return verified;
  }
}

class RuntimePreparationCancelled implements Exception {
  const RuntimePreparationCancelled();
}

class _LimitedText {
  _LimitedText(this.limit);

  final int limit;
  final StringBuffer _buffer = StringBuffer();
  bool truncated = false;

  String get text => _buffer.toString();

  void add(String value) {
    final left = limit - _buffer.length;
    if (value.length > left) {
      truncated = true;
      _buffer.write(value.substring(0, left));
    } else {
      _buffer.write(value);
    }
  }
}

class _SetupStream {
  _SetupStream(this.onProgress);

  final void Function(RuntimeSetupMessage) onProgress;
  String _pending = '';
  RuntimeSetupMessage? result;
  bool invalid = false;

  void add(String chunk) {
    if (invalid) return;
    var offset = 0;
    while (offset < chunk.length) {
      final newline = chunk.indexOf('\n', offset);
      final end = newline < 0 ? chunk.length : newline;
      if (_pending.length + end - offset > 65536) {
        invalid = true;
        _pending = '';
        return;
      }
      _pending += chunk.substring(offset, end);
      if (newline < 0) return;
      finish();
      if (invalid) return;
      offset = newline + 1;
    }
  }

  void finish() {
    final line = _pending.trim();
    _pending = '';
    if (line.isEmpty || invalid) return;
    try {
      final message = RuntimeSetupMessage.fromJsonLine(line);
      if (result != null) {
        invalid = true;
      } else if (message.isProgress) {
        onProgress(message);
      } else {
        result = message;
      }
    } on FormatException {
      invalid = true;
    }
  }
}
