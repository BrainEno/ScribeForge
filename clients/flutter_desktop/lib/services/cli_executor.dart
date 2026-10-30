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
}

class ProcessScribeForgeCliExecutor implements ScribeForgeCliExecutor {
  const ProcessScribeForgeCliExecutor();

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) =>
      ScribeForgeCli.run(
        executable: executable,
        arguments: arguments,
        onOutput: onOutput,
        onError: onError,
      );
}
