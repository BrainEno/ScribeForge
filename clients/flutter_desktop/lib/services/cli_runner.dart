import 'dart:convert';
import 'dart:io';

class ScribeForgeCli {
  static const String defaultExecutable = 'scribeforge';

  static String defaultStorePath(String pdfPath) =>
      _siblingPath(pdfPath, '.scribeforge');

  static String candidateOutputDirectoryForPdf(String pdfPath) =>
      _siblingPath(pdfPath, '-comparison-candidates');

  static String candidateEpubPath(String outputDirectory, String engine) =>
      joinPath(outputDirectory, 'candidate-$engine.epub');

  static List<String> statusArguments({required String storePath}) =>
      <String>['status', '--store', storePath, '--json'];

  static List<String> processArguments({
    required String pdfPath,
    required String storePath,
  }) =>
      <String>['process', pdfPath, '--store', storePath];

  static List<String> exportCandidateArguments({
    required String storePath,
    required String outputDirectory,
  }) =>
      <String>[
        'export-candidates',
        '--store',
        storePath,
        '--out-dir',
        outputDirectory,
      ];

  static String joinPath(String parent, String child) {
    final normalized = '$parent/$child'.replaceAll(r'\', '/');
    return Platform.pathSeparator == '/'
        ? normalized
        : normalized.replaceAll('/', Platform.pathSeparator);
  }

  static String _siblingPath(String sourcePath, String suffix) {
    final normalized = sourcePath.replaceAll(r'\', '/');
    final slash = normalized.lastIndexOf('/');
    final filename = normalized.substring(slash + 1);
    final dot = filename.lastIndexOf('.');
    final stem = dot > 0 ? filename.substring(0, dot) : filename;
    final targetName = '$stem$suffix';
    if (slash < 0) return targetName;
    final parent = normalized.substring(0, slash);
    final result = parent.isEmpty ? '/$targetName' : '$parent/$targetName';
    return Platform.pathSeparator == '/'
        ? result
        : result.replaceAll('/', Platform.pathSeparator);
  }

  static Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    void Function(String chunk)? onError,
  }) async {
    final process = await Process.start(executable, arguments);
    final stdoutDone = process.stdout
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(onOutput);
    final stderrDone = process.stderr
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(onError ?? onOutput);
    final exitCode = await process.exitCode;
    await Future.wait<void>(<Future<void>>[stdoutDone, stderrDone]);
    return exitCode;
  }
}
