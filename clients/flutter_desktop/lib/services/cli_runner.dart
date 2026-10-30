import 'dart:convert';
import 'dart:io';

import 'settings_store.dart';

class ScribeForgeCli {
  static const String defaultExecutable = 'scribeforge';
  static const String cancelControlMessage = '{"schema_version":1,"type":"cancel"}';

  static String defaultStorePath(String pdfPath) =>
      _siblingPath(pdfPath, '.scribeforge');

  static String candidateOutputDirectoryForPdf(String pdfPath) =>
      _siblingPath(pdfPath, '-comparison-candidates');

  static String candidateEpubPath(String outputDirectory, String engine) =>
      joinPath(outputDirectory, 'candidate-$engine.epub');

  static List<String> statusArguments({required String storePath}) =>
      <String>['status', '--store', storePath, '--json'];

  static List<String> auditArguments({required String storePath}) =>
      <String>['audit', '--store', storePath, '--json'];

  static List<String> runtimeStatusArguments({String? runtimeRoot}) =>
      <String>[
        'runtime-status',
        if (runtimeRoot != null && runtimeRoot.trim().isNotEmpty) ...[
          '--runtime-root',
          runtimeRoot.trim(),
        ],
        '--json',
      ];

  static List<String> setupArguments({String? runtimeRoot, String? backend}) =>
      <String>[
        'setup',
        if (runtimeRoot != null && runtimeRoot.trim().isNotEmpty) ...[
          '--runtime-root',
          runtimeRoot.trim(),
        ],
        if (backend != null && backend.trim().isNotEmpty) ...[
          '--backend',
          backend.trim(),
        ],
        '--json',
      ];

  static List<String> reviewArguments({required String storePath}) => <String>[
    'review',
    '--store',
    storePath,
    '--json',
  ];

  static List<String> resolveArguments({
    required String storePath,
    int? projectId,
    required int page,
    required int pair,
    required String text,
  }) => <String>[
    'resolve',
    '--store',
    storePath,
    if (projectId != null) ...['--project-id', projectId.toString()],
    '--page',
    page.toString(),
    '--pair',
    pair.toString(),
    '--text',
    text,
  ];

  static List<String> confirmArguments({
    required String storePath,
    required int page,
    required String kind,
    required String reason,
  }) => <String>[
    'confirm',
    '--store',
    storePath,
    '--page',
    page.toString(),
    '--kind',
    kind,
    '--reason',
    reason,
  ];

  static List<String> structureArguments({
    required String storePath,
    String engine = 'primary',
  }) => <String>[
    'structure',
    '--store',
    storePath,
    '--engine',
    engine,
    '--json',
  ];

  static List<String> backupArguments({
    required String storePath,
    required String outputPath,
    bool overwrite = false,
  }) =>
      <String>[
        'backup',
        '--store',
        storePath,
        '--out',
        outputPath,
        if (overwrite) '--overwrite',
        '--json',
      ];

  static List<String> exportArguments({
    required String storePath,
    required String outputPath,
    required String title,
    required String language,
    String? author,
    String? modified,
  }) =>
      <String>[
        'export',
        '--store',
        storePath,
        '--out',
        outputPath,
        '--language',
        language,
        '--title',
        title,
        if (author != null && author.trim().isNotEmpty) '--author',
        if (author != null && author.trim().isNotEmpty) author.trim(),
        if (modified != null && modified.trim().isNotEmpty) '--modified',
        if (modified != null && modified.trim().isNotEmpty) modified.trim(),
      ];

  static List<String> verifyBackupArguments({required String backupPath}) =>
      <String>['verify-backup', '--backup', backupPath, '--json'];

  static List<String> restoreBackupArguments({
    required String backupPath,
    required String targetPath,
  }) =>
      <String>[
        'restore',
        '--backup',
        backupPath,
        '--to',
        targetPath,
        '--json',
      ];

  static List<String> relinkSourceArguments({
    required String storePath,
    required String sourcePath,
  }) =>
      <String>[
        'relink-source',
        '--store',
        storePath,
        '--source',
        sourcePath,
      ];

  static List<String> processArguments({
    required String pdfPath,
    required String storePath,
    DesktopVlmSettings? vlmSettings,
    bool cancelable = false,
  }) {
    final arguments = <String>['process', pdfPath, '--store', storePath];
    if (cancelable) arguments.add('--cancelable');
    if (vlmSettings != null && vlmSettings.enabled) {
      arguments.addAll(_vlmArguments(vlmSettings));
    }
    return arguments;
  }

  static List<String> vlmCheckArguments({
    required String imagePath,
    required DesktopVlmSettings vlmSettings,
  }) =>
      <String>[
        'vlm-check',
        '--image',
        imagePath,
        ..._vlmArguments(vlmSettings),
        '--json',
      ];

  static List<String> _vlmArguments(DesktopVlmSettings settings) {
    if (!settings.isConfigured) {
      throw ArgumentError('VLM endpoint and model must both be configured.');
    }
    if (!settings.timeoutSeconds.isFinite || settings.timeoutSeconds <= 0) {
      throw ArgumentError('VLM timeout must be finite and greater than zero.');
    }
    return <String>[
      '--vlm-endpoint',
      settings.endpoint.trim(),
      '--vlm-model',
      settings.model.trim(),
      if (settings.apiKeyEnvironmentVariable.trim().isNotEmpty) ...[
        '--vlm-api-key-env',
        settings.apiKeyEnvironmentVariable.trim(),
      ],
      '--vlm-timeout',
      settings.timeoutSeconds.toString(),
      if (settings.allowRemote) '--allow-remote-vlm',
    ];
  }

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
    void Function(Process process)? onProcessStarted,
  }) async {
    final process = await Process.start(executable, arguments);
    try {
      onProcessStarted?.call(process);
    } catch (_) {
      process.kill();
      rethrow;
    }
    final stdoutDone = process.stdout
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(onOutput);
    final stderrDone = process.stderr
        .transform(const Utf8Decoder(allowMalformed: true))
        .forEach(onError ?? onOutput);
    final exitCode = await process.exitCode;
    await Future.wait<void>(<Future<void>>[stdoutDone, stderrDone]);
    await process.stdin.close();
    return exitCode;
  }
}
