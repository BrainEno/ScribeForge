import 'dart:async';
import 'dart:io';

import 'cli_runner.dart';

/// Replaceable boundary for invoking the ScribeForge command-line workflow.
///
/// The desktop UI depends on command arguments and streamed output, not on a
/// concrete child process. Widget/integration tests can provide a deterministic
/// executor without installing OCR runtimes or touching a user's project store.
abstract interface class ScribeForgeCliExecutor {
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  });

  /// Request cancellation of the active command, if it supports cancellation.
  Future<void> cancel();
}

class ProcessScribeForgeCliExecutor implements ScribeForgeCliExecutor {
  Process? _cancelableProcess;
  var _cancelRequested = false;
  var _cancelSent = false;

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    final cancelable = arguments.contains('--cancelable');
    if (cancelable) {
      if (_cancelableProcess != null) {
        throw StateError('A cancelable ScribeForge CLI process is already running.');
      }
      _cancelRequested = false;
      _cancelSent = false;
    }
    try {
      return await ScribeForgeCli.run(
        executable: executable,
        arguments: arguments,
        onOutput: onOutput,
        onError: onError,
        onProcessStarted: (process) {
          if (!cancelable) return;
          _cancelableProcess = process;
          if (_cancelRequested) unawaited(_sendCancelControl(process));
        },
      );
    } finally {
      if (cancelable) {
        _cancelableProcess = null;
        _cancelRequested = false;
        _cancelSent = false;
      }
    }
  }

  @override
  Future<void> cancel() async {
    _cancelRequested = true;
    final process = _cancelableProcess;
    if (process != null) await _sendCancelControl(process);
  }

  Future<void> _sendCancelControl(Process process) async {
    if (_cancelSent) return;
    _cancelSent = true;
    try {
      process.stdin.writeln(ScribeForgeCli.cancelControlMessage);
      await process.stdin.flush();
    } on IOException {
      // The CLI may have exited between the UI tap and writing to its pipe.
    } on StateError {
      // The process pipe can already be closed when cancellation races exit.
    }
  }
}
