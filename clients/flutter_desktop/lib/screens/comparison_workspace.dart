import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:pdfrx/pdfrx.dart';

import '../models/epub_candidate.dart';
import '../models/project_status.dart';
import '../models/text_diff.dart';
import '../services/cli_runner.dart';
import '../services/evaluation_store.dart';
import '../services/settings_store.dart';

class ComparisonWorkspace extends StatefulWidget {
  const ComparisonWorkspace({super.key});

  @override
  State<ComparisonWorkspace> createState() => _ComparisonWorkspaceState();
}

class _ComparisonWorkspaceState extends State<ComparisonWorkspace> {
  String? _pdfPath;
  String? _projectStorePath;
  bool _projectStoreExplicit = false;
  bool _projectSourceMatches = true;
  String? _projectSourceMismatchMessage;
  int? _pdfPageCount;
  int _page = 0;
  EpubCandidate? _a;
  EpubCandidate? _b;
  String? _aPath;
  String? _bPath;
  String? _winner;
  final Map<int, String> _decisions = <int, String>{};
  final _labelA = TextEditingController(text: 'Candidate A');
  final _labelB = TextEditingController(text: 'Candidate B');
  final _notes = TextEditingController();
  final _settingsStore = DesktopSettingsStore();
  String _cliExecutable = ScribeForgeCli.defaultExecutable;
  ProjectStatusSnapshot? _projectStatus;
  String? _projectStatusMessage;
  bool _statusLoading = false;
  int _statusRequestVersion = 0;
  bool _busy = false;
  String? _notice;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final executable = await _settingsStore.loadCliExecutable();
    if (!mounted) return;
    setState(() => _cliExecutable = executable);
  }

  @override
  void dispose() {
    _labelA.dispose();
    _labelB.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<String?> _pick(String title, List<String> extensions) async {
    final file = await FilePicker.pickFile(
      dialogTitle: title,
      type: FileType.custom,
      allowedExtensions: extensions,
    );
    if (file == null) {
      return null;
    }
    final path = file.path;
    if (path == null || path.isEmpty) {
      _message('The file picker did not return a local path.');
      return null;
    }
    return path;
  }

  Future<void> _openPdf() async {
    try {
      final path = await _pick('Open original PDF', const ['pdf']);
      if (path == null || !mounted) return;
      final keepSelectedStore = _projectStoreExplicit && _projectStorePath != null;
      await _loadSourcePdf(
        path,
        storePath: keepSelectedStore
            ? _projectStorePath!
            : ScribeForgeCli.defaultStorePath(path),
        explicitStore: keepSelectedStore,
      );
    } on Object catch (error) {
      if (mounted) _message('Could not open the PDF: $error');
    }
  }

  Future<void> _loadSourcePdf(
    String path, {
    required String storePath,
    required bool explicitStore,
  }) async {
    ++_statusRequestVersion;
    setState(() {
      _busy = true;
      _notice = null;
      _pdfPath = path;
      _projectStorePath = storePath;
      _projectStoreExplicit = explicitStore;
      _projectStatus = null;
      _projectStatusMessage = 'Checking the project against this PDF…';
      _projectSourceMatches = true;
      _projectSourceMismatchMessage = null;
      _pdfPageCount = null;
      _page = 0;
      _a = null;
      _b = null;
      _aPath = null;
      _bPath = null;
      _labelA.text = 'Candidate A';
      _labelB.text = 'Candidate B';
      _notes.clear();
      _decisions.clear();
      _winner = null;
    });
    try {
      final saved = await EvaluationStore.read(path);
      if (mounted && saved != null) {
        final source = saved['source_pdf'];
        final sourceMap = source is Map ? Map<String, dynamic>.from(source) : null;
        final currentHash = await EvaluationStore.fingerprint(path);
        if (sourceMap?['path'] != path || sourceMap?['sha256'] != currentHash) {
          setState(() =>
              _notice = 'Previous evaluation does not match this PDF; ratings were not restored.');
        } else {
          final rawA = saved['candidate_a'];
          final rawB = saved['candidate_b'];
          final recordA = rawA is Map ? Map<String, dynamic>.from(rawA) : null;
          final recordB = rawB is Map ? Map<String, dynamic>.from(rawB) : null;
          final candidateA = await _restoreCandidate(recordA);
          final candidateB = await _restoreCandidate(recordB);
          if (mounted &&
              !((recordA != null && candidateA == null) ||
                  (recordB != null && candidateB == null))) {
            final decisions = <int, String>{};
            final rawDecisions = saved['page_decisions'];
            if (rawDecisions is Map) {
              for (final item in rawDecisions.entries) {
                final key = int.tryParse(item.key.toString());
                if (key != null && item.value is String) {
                  decisions[key] = item.value as String;
                }
              }
            }
            _labelA.text = recordA?['label'] as String? ?? 'Candidate A';
            _labelB.text = recordB?['label'] as String? ?? 'Candidate B';
            _notes.text = saved['notes'] as String? ?? '';
            setState(() {
              _a = candidateA;
              _b = candidateB;
              _aPath = recordA?['path'] as String?;
              _bPath = recordB?['path'] as String?;
              _winner = saved['overall_winner'] as String?;
              _decisions
                ..clear()
                ..addAll(decisions);
              _notice = 'Restored the previous human evaluation for unchanged input files.';
            });
          } else if (mounted) {
            setState(() => _notice =
                'A candidate EPUB is missing or changed; old ratings were not restored.');
          }
        }
      }
    } on Object catch (error) {
      if (mounted) {
        _message('Could not restore the previous evaluation: $error');
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
    await _refreshProjectStatus();
  }

  Future<void> _openProjectStore() async {
    final directoryPath = await FilePicker.getDirectoryPath(
      dialogTitle: 'Open ScribeForge project folder',
    );
    if (directoryPath == null || !mounted) {
      return;
    }
    setState(() {
      _statusLoading = true;
      _projectStatusMessage = 'Reading the selected project store…';
    });
    try {
      final stdout = StringBuffer();
      final stderr = StringBuffer();
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable.trim(),
        arguments: ScribeForgeCli.statusArguments(storePath: directoryPath),
        onOutput: stdout.write,
        onError: stderr.write,
      );
      if (!mounted) return;
      if (exitCode != 0) {
        final detail = stderr.toString().trim();
        throw StateError(detail.isEmpty
            ? 'ScribeForge could not read this folder as a project store (exit code $exitCode).'
            : _firstLine(detail));
      }
      final projects = ProjectStatusSnapshot.listFromJsonText(stdout.toString());
      if (projects.isEmpty) {
        _message('The selected folder has no ScribeForge project yet.');
        return;
      }
      if (projects.length != 1) {
        _message(
          'This folder contains ${projects.length} projects. Processing currently requires one dedicated project per store; choose the specific project folder.',
        );
        return;
      }
      final project = projects.single;
      final sourceFile = File(project.sourceUri);
      final recordedPath = sourceFile.isAbsolute
          ? sourceFile.path
          : sourceFile.absolute.path;
      final exists = await File(recordedPath).exists();
      if (!mounted) return;
      setState(() {
        _projectStorePath = directoryPath;
        _projectStoreExplicit = true;
        _projectStatus = project;
        _projectSourceMatches = false;
        _projectSourceMismatchMessage = exists
            ? 'Project selected. Loading its recorded source PDF for identity checks…'
            : 'The recorded source PDF was not found at "$recordedPath". Choose the original PDF manually; processing stays disabled until path and SHA-256 both match.';
        _projectStatusMessage = null;
        _notice = 'Opened project store: $directoryPath';
      });
      if (exists) {
        await _loadSourcePdf(
          recordedPath,
          storePath: directoryPath,
          explicitStore: true,
        );
      } else {
        _message('Project store opened, but its recorded source PDF could not be located.');
      }
    } on Object catch (error) {
      if (mounted) {
        _message('Could not open project store: ${_firstLine(error.toString())}');
      }
    } finally {
      if (mounted) {
        setState(() => _statusLoading = false);
      }
    }
  }

  Future<void> _relinkSourcePdf() async {
    final status = _projectStatus;
    final storePath = _activeStorePath;
    if (!_projectStoreExplicit || status == null || storePath.isEmpty) {
      _message('Open an existing project store before re-linking its source PDF.');
      return;
    }

    final sourcePath = await _pick(
      'Select the moved original PDF',
      const ['pdf'],
    );
    if (sourcePath == null || !mounted) return;

    String canonicalSourcePath;
    try {
      canonicalSourcePath = await File(sourcePath).resolveSymbolicLinks();
    } on Object catch (error) {
      _message('Could not resolve the selected PDF path: $error');
      return;
    }
    if (!mounted) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Verify and relink source PDF?'),
        content: const Text(
          'ScribeForge will calculate the selected PDF’s SHA-256 and compare it '
          'with the project record. The source path will change only if the hashes '
          'match exactly. A different file is rejected; OCR results and review '
          'evidence are never overwritten by this operation.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Verify and relink'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final exitCode = await _runCli(
      title: 'Relink source PDF',
      arguments: ScribeForgeCli.relinkSourceArguments(
        storePath: storePath,
        sourcePath: canonicalSourcePath,
      ),
    );
    if (!mounted || exitCode != 0) return;

    setState(() {
      _pdfPath = canonicalSourcePath;
      _projectStorePath = storePath;
      _projectStoreExplicit = true;
      _projectSourceMatches = true;
      _projectSourceMismatchMessage = null;
      _projectStatusMessage = 'Verifying the updated source identity…';
      _notice =
          'Source path relinked after SHA-256 verification. Existing OCR evidence was preserved.';
    });
    await _refreshProjectStatus();
  }

  Future<EpubCandidate?> _restoreCandidate(Map<String, dynamic>? record) async {
    final path = record?['path'];
    final hash = record?['sha256'];
    if (path is! String || hash is! String) {
      return null;
    }
    try {
      final candidate = await EpubCandidate.load(path);
      return candidate.sha256 == hash ? candidate : null;
    } on Object {
      return null;
    }
  }

  Future<void> _openEpub({required bool left}) async {
    try {
      final path = await _pick(left ? 'Open EPUB candidate A' : 'Open EPUB candidate B',
          const ['epub']);
      if (path == null || !mounted) return;
      setState(() {
        _busy = true;
        _notice = null;
      });
      final candidate = await EpubCandidate.load(path);
      if (!mounted) {
        return;
      }
      setState(() {
        if (left) {
          _a = candidate;
          _aPath = path;
          _labelA.text = candidate.title;
        } else {
          _b = candidate;
          _bPath = path;
          _labelB.text = candidate.title;
        }
        _winner = null;
        _decisions.clear();
        _notice = 'Candidate changed. Previous ratings were cleared.';
      });
    } on Object catch (error) {
      if (mounted) _message('Could not open EPUB: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }


  Future<void> _editSettings() async {
    final controller = TextEditingController(text: _cliExecutable);
    try {
      final value = await showDialog<String>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('ScribeForge CLI settings'),
          content: SizedBox(
            width: 520,
            child: TextField(
              controller: controller,
              autofocus: true,
              decoration: const InputDecoration(
                labelText: 'CLI executable or full path',
                hintText: 'scribeforge',
                helperText: 'Install the Python package first, or enter the full executable path.',
              ),
              onSubmitted: (_) =>
                  Navigator.of(dialogContext).pop(controller.text.trim()),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () =>
                  Navigator.of(dialogContext).pop(controller.text.trim()),
              child: const Text('Save'),
            ),
          ],
        ),
      );
      if (value == null || value.trim().isEmpty) return;
      final executable = value.trim();
      await _settingsStore.saveCliExecutable(executable);
      if (!mounted) return;
      setState(() => _cliExecutable = executable);
      _message('CLI executable saved. Use the full path if the command is not on PATH.');
    } finally {
      controller.dispose();
    }
  }

  Future<int?> _runCli({
    required String title,
    required List<String> arguments,
    bool auditMode = false,
  }) async {
    final executable = _cliExecutable.trim();
    if (executable.isEmpty) {
      _message('Open CLI settings and enter the ScribeForge executable first.');
      return null;
    }

    setState(() {
      _busy = true;
      _notice = '$title is running. Long OCR jobs can take several minutes.';
    });

    final output = ValueNotifier<String>(
      'Command: $executable ${arguments.join(' ')}\n\n',
    );
    final state = ValueNotifier<String>('Starting process…');
    final finished = ValueNotifier<bool>(false);
    final dialogFuture = showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: SizedBox(
          width: 700,
          height: 420,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ValueListenableBuilder<String>(
                valueListenable: state,
                builder: (_, value, _) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(value,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                ),
              ),
              const Divider(height: 1),
              const SizedBox(height: 8),
              Expanded(
                child: ValueListenableBuilder<String>(
                  valueListenable: output,
                  builder: (_, value, _) => SingleChildScrollView(
                    child: SelectableText(
                      value,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11,
                        height: 1.4,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
        actions: [
          ValueListenableBuilder<bool>(
            valueListenable: finished,
            builder: (_, done, _) => TextButton(
              onPressed: done ? () => Navigator.of(dialogContext).pop() : null,
              child: Text(done ? 'Close' : 'Running…'),
            ),
          ),
        ],
      ),
    );
    unawaited(dialogFuture.whenComplete(() {
      output.dispose();
      state.dispose();
      finished.dispose();
    }));

    int? exitCode;
    try {
      exitCode = await ScribeForgeCli.run(
        executable: executable,
        arguments: arguments,
        onOutput: (chunk) => output.value += chunk,
      );
      if (auditMode) {
        state.value = switch (exitCode) {
          0 => 'Audit completed: no findings (exit code 0).',
          1 => 'Audit completed: findings detected. Review the report above.',
          _ => 'Audit could not complete (exit code $exitCode). See the log above.',
        };
      } else {
        state.value = switch (exitCode) {
          0 => 'Completed successfully (exit code 0).',
          1 => 'Completed with page-level failures (exit code 1). You can retry.',
          _ => 'Command failed (exit code $exitCode). See the log above.',
        };
      }
      output.value += '\n\nExit code: $exitCode\n';
      if (mounted) {
        setState(() {
          _notice = exitCode == 0
              ? '$title completed.'
              : '$title ended with exit code $exitCode. Review the command log.';
        });
      }
    } on Object catch (error) {
      state.value = 'Could not start the command.';
      output.value +=
          '\nCould not start "$executable".\n$error\n\nCheck CLI settings and install the Python package using pip install -e ".[dev]".\n';
      if (mounted) {
        setState(() =>
            _notice = 'Could not start ScribeForge CLI. Open settings to check its path.');
      }
    } finally {
      finished.value = true;
      if (mounted) setState(() => _busy = false);
    }
    return exitCode;
  }


  String get _activeStorePath =>
      _projectStorePath ??
      (_pdfPath == null ? '' : ScribeForgeCli.defaultStorePath(_pdfPath!));

  Future<void> _refreshProjectStatus() async {
    final pdf = _pdfPath;
    if (pdf == null) return;
    final requestVersion = ++_statusRequestVersion;
    setState(() {
      _statusLoading = true;
      _projectStatusMessage = 'Refreshing local project status…';
    });
    final stdout = StringBuffer();
    final stderr = StringBuffer();
    try {
      final exitCode = await ScribeForgeCli.run(
        executable: _cliExecutable.trim(),
        arguments: ScribeForgeCli.statusArguments(storePath: _activeStorePath),
        onOutput: stdout.write,
        onError: stderr.write,
      );
      if (!mounted || requestVersion != _statusRequestVersion) return;
      if (exitCode == 0) {
        final projects = ProjectStatusSnapshot.listFromJsonText(stdout.toString());
        if (projects.isEmpty) {
          setState(() {
            _projectStatus = null;
            _projectSourceMatches = !_projectStoreExplicit;
            _projectSourceMismatchMessage = _projectStoreExplicit
                ? 'The selected project store no longer contains a project. Select an existing store or switch back to the default store.'
                : null;
            _projectStatusMessage = _projectStoreExplicit
                ? 'No project was found in the selected store.'
                : 'No processing project yet. Choose Run OCR to create one.';
          });
          return;
        }
        if (projects.length != 1) {
          setState(() {
            _projectStatus = null;
            _projectSourceMatches = false;
            _projectSourceMismatchMessage =
                'This store contains multiple projects. Processing is blocked because page assets currently require a dedicated store per project.';
            _projectStatusMessage = null;
          });
          return;
        }
        final status = projects.single;
        final currentHash = await EvaluationStore.fingerprint(pdf);
        if (!mounted || requestVersion != _statusRequestVersion) return;
        final pathMatches = status.sourcePathMatches(pdf);
        final hashMatches = status.sourceHashMatches(currentHash);
        final sourceMatches = pathMatches && hashMatches;
        setState(() {
          _projectStatus = status;
          _projectSourceMatches = sourceMatches;
          _projectSourceMismatchMessage = sourceMatches
              ? null
              : !pathMatches && !hashMatches
                  ? 'Source path and SHA-256 differ from this project. Open the recorded source PDF or select a new project store; OCR and candidate export are blocked.'
                  : !pathMatches
                      ? 'Source path differs from the project record. Open the recorded source PDF or choose a new project store; processing is blocked.'
                      : 'Source PDF contents have changed since this project was created (SHA-256 mismatch). Use a new project store for the modified PDF; existing evidence is preserved.';
          _projectStatusMessage = null;
        });
      } else {
        final combined = stderr.toString().trim();
        final noStore = combined.toLowerCase().contains('no store found');
        setState(() {
          _projectStatus = null;
          _projectSourceMatches = !_projectStoreExplicit && noStore;
          _projectSourceMismatchMessage = _projectSourceMatches
              ? null
              : _projectStoreExplicit && noStore
                  ? 'The explicitly selected project store is missing its database.'
                  : null;
          _projectStatusMessage = noStore
              ? _projectStoreExplicit
                  ? 'Selected project store not found or incomplete.'
                  : 'No processing project yet. Choose Run OCR to create one.'
              : combined.isEmpty
                  ? 'The CLI returned exit code $exitCode while reading project status.'
                  : 'Could not read project status: ${_firstLine(combined)}';
        });
      }
    } on Object catch (error) {
      if (!mounted || requestVersion != _statusRequestVersion) return;
      setState(() {
        _projectStatus = null;
        _projectSourceMatches = false;
        _projectSourceMismatchMessage = null;
        _projectStatusMessage =
            'Status unavailable. Check the CLI path in settings. ${_firstLine(error.toString())}';
      });
    } finally {
      if (mounted && requestVersion == _statusRequestVersion) {
        setState(() => _statusLoading = false);
      }
    }
  }

  String _firstLine(String value) {
    final lines = value.trim().split(RegExp(r'\r?\n'));
    return lines.isEmpty ? value : lines.first;
  }

  Future<void> _setupRuntimes() async {
    await _runCli(title: 'OCR runtime setup', arguments: const ['setup']);
  }

  Future<void> _auditProject() async {
    final storePath = _activeStorePath;
    if (storePath.isEmpty) {
      _message('Open a ScribeForge project before running the integrity audit.');
      return;
    }
    await _runCli(
      title: 'Project integrity audit',
      arguments: ScribeForgeCli.auditArguments(storePath: storePath),
      auditMode: true,
    );
  }

  Future<void> _backupProject() async {
    final storePath = _activeStorePath;
    if (storePath.isEmpty) {
      _message('Open a ScribeForge project before creating a backup.');
      return;
    }
    final directory = await FilePicker.getDirectoryPath(
      dialogTitle: 'Choose a directory for the project backup',
    );
    if (directory == null || !mounted) return;

    final now = DateTime.now();
    String twoDigits(int value) => value.toString().padLeft(2, '0');
    final timestamp =
        '${now.year}${twoDigits(now.month)}${twoDigits(now.day)}-'
        '${twoDigits(now.hour)}${twoDigits(now.minute)}${twoDigits(now.second)}';
    final storeName = _basename(storePath);
    final outputPath = ScribeForgeCli.joinPath(
      directory,
      '$storeName-backup-$timestamp.zip',
    );
    final exitCode = await _runCli(
      title: 'Back up project',
      arguments: ScribeForgeCli.backupArguments(
        storePath: storePath,
        outputPath: outputPath,
      ),
    );
    if (!mounted || exitCode != 0) return;
    _message('Verified project backup saved to $outputPath');
  }

  Future<void> _verifyBackup() async {
    final backupPath = await _pick('Select a ScribeForge backup ZIP', const ['zip']);
    if (backupPath == null || !mounted) return;
    await _runCli(
      title: 'Verify project backup',
      arguments: ScribeForgeCli.verifyBackupArguments(backupPath: backupPath),
    );
  }

  Future<void> _restoreProjectBackup() async {
    final backupPath = await _pick('Select a ScribeForge backup ZIP to restore', const ['zip']);
    if (backupPath == null || !mounted) return;

    final verifyExit = await _runCli(
      title: 'Verify backup before restore',
      arguments: ScribeForgeCli.verifyBackupArguments(backupPath: backupPath),
    );
    if (!mounted || verifyExit != 0) return;

    final parent = await FilePicker.getDirectoryPath(
      dialogTitle: 'Choose the parent directory for the restored project',
    );
    if (parent == null || !mounted) return;

    final archiveName = _basename(backupPath);
    final archiveStem = archiveName.toLowerCase().endsWith('.zip')
        ? archiveName.substring(0, archiveName.length - 4)
        : archiveName;
    final targetPath = ScribeForgeCli.joinPath(
      parent,
      '$archiveStem.restored.scribeforge',
    );
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Restore project backup?'),
        content: Text(
          'The verified backup will be restored to:\n\n$targetPath\n\n'
          'The destination must not already exist. Existing folders will never be overwritten.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Restore to new folder'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final exitCode = await _runCli(
      title: 'Restore project backup',
      arguments: ScribeForgeCli.restoreBackupArguments(
        backupPath: backupPath,
        targetPath: targetPath,
      ),
    );
    if (!mounted || exitCode != 0) return;

    setState(() {
      _projectStorePath = targetPath;
      _projectStoreExplicit = true;
      _projectStatus = null;
      _projectSourceMatches = _pdfPath == null;
      _projectSourceMismatchMessage = null;
      _projectStatusMessage =
          'Backup restored. Open the original source PDF to validate its identity before processing.';
      _notice = 'Project restored to $targetPath. Existing destination folders are never overwritten.';
    });
    if (_pdfPath != null) {
      await _refreshProjectStatus();
    }
    if (!mounted) return;
    _message('Project restored. Open its source PDF to confirm the stored path and SHA-256.');
  }

  Future<void> _runOcr() async {
    final pdf = _pdfPath;
    if (pdf == null) {
      _message('Open the original PDF before starting OCR.');
      return;
    }
    if (!_projectSourceMatches) {
      _message(_projectSourceMismatchMessage ?? 'The selected source does not match this project.');
      return;
    }
    final storePath = _activeStorePath;
    final exitCode = await _runCli(
      title: 'Process source PDF',
      arguments: ScribeForgeCli.processArguments(
        pdfPath: pdf,
        storePath: storePath,
      ),
    );
    if (!mounted || exitCode == null) return;
    await _refreshProjectStatus();
    if (!mounted) return;
    if (exitCode == 0) {
      _message('OCR finished. Next, choose “Build A/B EPUBs” to load both candidates.');
    } else if (exitCode == 1) {
      _message('Some pages failed. Run OCR again to retry resumable jobs, then build candidates.');
    }
  }

  Future<void> _buildCandidates() async {
    final pdf = _pdfPath;
    if (pdf == null) {
      _message('Open the original PDF before generating candidates.');
      return;
    }
    if (!_projectSourceMatches || _projectStatus == null) {
      _message(_projectSourceMismatchMessage ?? 'Run OCR in a valid project before generating candidates.');
      return;
    }
    final storePath = _activeStorePath;
    final outputDirectory = ScribeForgeCli.candidateOutputDirectoryForPdf(pdf);
    final exitCode = await _runCli(
      title: 'Build OCR candidate EPUBs',
      arguments: ScribeForgeCli.exportCandidateArguments(
        storePath: storePath,
        outputDirectory: outputDirectory,
      ),
    );
    if (!mounted || exitCode != 0) return;

    try {
      final pathA = ScribeForgeCli.candidateEpubPath(outputDirectory, 'mineru');
      final pathB = ScribeForgeCli.candidateEpubPath(outputDirectory, 'paddleocr');
      final candidateA = await EpubCandidate.load(pathA);
      final candidateB = await EpubCandidate.load(pathB);
      if (!mounted) return;
      setState(() {
        _a = candidateA;
        _b = candidateB;
        _aPath = pathA;
        _bPath = pathB;
        _labelA.text = 'MinerU';
        _labelB.text = 'PaddleOCR';
        _page = 0;
        _winner = null;
        _decisions.clear();
        _notice =
            'Candidate EPUBs loaded. They are raw, unverified OCR outputs; compare and decide manually.';
      });
      _message('Both candidates were loaded. Their OCR text is unverified; no winner is inferred.');
    } on Object catch (error) {
      _message('Could not load generated candidates: $error');
    }
  }

  Future<void> _exportVerifiedBook() async {
    final storePath = _activeStorePath;
    final status = _projectStatus;
    if (storePath.isEmpty || status == null || !_projectSourceMatches) {
      _message(
        'Open a project whose source path and SHA-256 match before exporting.',
      );
      return;
    }

    final titleController = TextEditingController(text: status.title);
    final authorController = TextEditingController();
    final languageController = TextEditingController(text: 'und');
    final modifiedController = TextEditingController();
    final formKey = GlobalKey<FormState>();
    var format = 'epub';

    try {
      final metadata = await showDialog<Map<String, String>>(
        context: context,
        builder: (_) => StatefulBuilder(
          builder: (dialogContext, setDialogState) => AlertDialog(
            title: const Text('Export verified book'),
            content: SizedBox(
              width: 520,
              child: SingleChildScrollView(
                child: Form(
                  key: formKey,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      DropdownButtonFormField<String>(
                        initialValue: format,
                        decoration: const InputDecoration(
                          labelText: 'Output format',
                        ),
                        items: const [
                          DropdownMenuItem(
                            value: 'epub',
                            child: Text('EPUB 3 (.epub)'),
                          ),
                          DropdownMenuItem(
                            value: 'docx',
                            child: Text('Word document (.docx)'),
                          ),
                        ],
                        onChanged: (value) {
                          if (value != null) {
                            setDialogState(() => format = value);
                          }
                        },
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: titleController,
                        autofocus: true,
                        decoration: const InputDecoration(
                          labelText: 'Book title',
                        ),
                        validator: (value) =>
                            value?.trim().isNotEmpty == true
                                ? null
                                : 'Enter a book title.',
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: authorController,
                        decoration: const InputDecoration(
                          labelText: 'Author (optional)',
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: languageController,
                        decoration: const InputDecoration(
                          labelText: 'Language tag',
                          hintText: 'zh-Hant, en, ja, or und',
                          helperText:
                              'Use a BCP 47 language tag; und means unknown.',
                        ),
                        validator: (value) =>
                            value?.trim().isNotEmpty == true
                                ? null
                                : 'Enter a language tag, or use und.',
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: modifiedController,
                        decoration: const InputDecoration(
                          labelText: 'Fixed modification time (optional)',
                          hintText: '2026-10-22T00:00:00Z',
                          helperText:
                              'ISO 8601 timestamp for reproducible exports. Leave blank to use the current time.',
                        ),
                        validator: (value) {
                          final raw = value?.trim() ?? '';
                          if (raw.isEmpty || DateTime.tryParse(raw) != null) {
                            return null;
                          }
                          return 'Use a valid ISO 8601 timestamp.';
                        },
                      ),
                      const SizedBox(height: 12),
                      const Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          'Only accepted verification decisions are exported. '
                          'The CLI rejects incomplete pages or unresolved review '
                          'pairs; the A/B candidate EPUBs are never treated as final text.',
                          style: TextStyle(fontSize: 12, color: Color(0xFF68766F)),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(),
                child: const Text('Cancel'),
              ),
              FilledButton.icon(
                onPressed: () {
                  if (!(formKey.currentState?.validate() ?? false)) return;
                  Navigator.of(dialogContext).pop(<String, String>{
                    'format': format,
                    'title': titleController.text.trim(),
                    'author': authorController.text.trim(),
                    'language': languageController.text.trim(),
                    'modified': modifiedController.text.trim(),
                  });
                },
                icon: const Icon(Icons.file_download_outlined),
                label: const Text('Choose destination'),
              ),
            ],
          ),
        ),
      );
      if (metadata == null || !mounted) return;

      final selectedPath = await FilePicker.saveFile(
        dialogTitle: 'Export verified ${metadata['format']!.toUpperCase()}',
        fileName:
            '${_safeFileName(metadata['title']!)}.${metadata['format']}',
        type: FileType.custom,
        allowedExtensions: <String>[metadata['format']!],
      );
      if (selectedPath == null || !mounted) return;

      final selectedFormat = metadata['format']!;
      var outputPath = selectedPath;
      final lowerPath = outputPath.toLowerCase();
      if (lowerPath.endsWith('.epub') || lowerPath.endsWith('.docx')) {
        if (!lowerPath.endsWith('.$selectedFormat')) {
          _message(
            'The destination extension does not match the selected '
            '${selectedFormat.toUpperCase()} format.',
          );
          return;
        }
      } else {
        outputPath = '$outputPath.$selectedFormat';
      }

      final exitCode = await _runCli(
        title: 'Export verified book',
        arguments: ScribeForgeCli.exportArguments(
          storePath: storePath,
          outputPath: outputPath,
          title: metadata['title']!,
          language: metadata['language']!,
          author: metadata['author'],
          modified: metadata['modified'],
        ),
      );
      if (!mounted || exitCode != 0) return;
      _message(
        'Verified ${selectedFormat.toUpperCase()} exported to $outputPath',
      );
    } on Object catch (error) {
      if (mounted) {
        _message('Could not export the verified book: $error');
      }
    } finally {
      titleController.dispose();
      authorController.dispose();
      languageController.dispose();
      modifiedController.dispose();
    }
  }

  String _safeFileName(String value) {
    final safe = value
        .replaceAll(RegExp(r'[<>:"/\\|?*\x00-\x1F]'), '_')
        .trim();
    return safe.isEmpty ? 'ScribeForge export' : safe;
  }

  int get _pageCount {
    final pdfCount = _pdfPageCount;
    if (pdfCount != null) return pdfCount;
    final a = _a?.pages.length ?? 0;
    final b = _b?.pages.length ?? 0;
    return a > b ? a : b;
  }

  EpubPage? _pageFor(EpubCandidate? candidate) =>
      candidate?.pageForSourceIndex(_page);

  void _message(String message) {
    if (!mounted) return;
    setState(() => _notice = message);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _save() async {
    if (_pdfPath == null || _a == null || _b == null) {
      _message('Open a source PDF and both EPUB candidates before saving.');
      return;
    }
    setState(() => _busy = true);
    try {
      final path = await EvaluationStore.save(
        sourcePdfPath: _pdfPath!,
        candidateAPath: _aPath,
        candidateBPath: _bPath,
        candidateALabel: _labelA.text.trim().isEmpty ? 'Candidate A' : _labelA.text.trim(),
        candidateBLabel: _labelB.text.trim().isEmpty ? 'Candidate B' : _labelB.text.trim(),
        pageDecisions: _decisions,
        overallWinner: _winner,
        notes: _notes.text,
      );
      if (mounted) _message('Evaluation saved: $path');
    } on Object catch (error) {
      if (mounted) _message('Could not save evaluation: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pageA = _pageFor(_a);
    final pageB = _pageFor(_b);
    final aligned = (_a?.isSourcePageAligned ?? true) && (_b?.isSourcePageAligned ?? true);
    final rows = pageA == null || pageB == null
        ? const <TextDiffRow>[]
        : diffLines(pageA.lines, pageB.lines);
    final summary = summarizeDiff(
      rows,
      pageA?.lines ?? const <String>[],
      pageB?.lines ?? const <String>[],
    );

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _header(),
            _toolbar(),
            if (_pdfPath != null) _projectStatusPanel(),
            if (_notice != null) _noticeBar(_notice!),
            _pageToolbar(summary, aligned),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Expanded(flex: 10, child: _pdfPanel()),
                    const SizedBox(width: 10),
                    Expanded(
                      flex: 10,
                      child: _epubPanel(left: true, candidate: _a, page: pageA, rows: rows),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      flex: 10,
                      child: _epubPanel(left: false, candidate: _b, page: pageB, rows: rows),
                    ),
                  ],
                ),
              ),
            ),
            _evaluationPanel(),
          ],
        ),
      ),
    );
  }

  Widget _header() => Container(
        height: 60,
        padding: const EdgeInsets.symmetric(horizontal: 18),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(bottom: BorderSide(color: Color(0xFFE2E7E4))),
        ),
        child: Row(
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: const Color(0xFFE3F2EC),
                borderRadius: BorderRadius.circular(9),
              ),
              child: const Icon(Icons.menu_book_rounded, color: Color(0xFF236B5D)),
            ),
            const SizedBox(width: 10),
            const Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('ScribeForge', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
                Text('PDF + EPUB CANDIDATE COMPARISON',
                    style: TextStyle(fontSize: 9, letterSpacing: 1.1, color: Color(0xFF718079))),
              ],
            ),
            IconButton(
              tooltip: 'CLI settings',
              onPressed: _busy ? null : _editSettings,
              icon: const Icon(Icons.settings_outlined, size: 19),
            ),
            const Spacer(),
            const Icon(Icons.shield_outlined, size: 16, color: Color(0xFF236B5D)),
            const SizedBox(width: 6),
            const Text('Local files · Human decision',
                style: TextStyle(fontSize: 11, color: Color(0xFF235E52))),
          ],
        ),
      );

  Widget _toolbar() => Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 8),
        child: Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading ? null : _openProjectStore,
              icon: const Icon(Icons.folder_open_outlined),
              label: const Text('Open project'),
            ),
            if (_projectStoreExplicit || _projectStatus != null) ...[
              OutlinedButton.icon(
                onPressed: _busy || _statusLoading ? null : _auditProject,
                icon: const Icon(Icons.fact_check_outlined),
                label: const Text('Audit project'),
              ),
              OutlinedButton.icon(
                onPressed: _busy || _statusLoading ? null : _backupProject,
                icon: const Icon(Icons.archive_outlined),
                label: const Text('Backup project'),
              ),
            ],
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading ? null : _verifyBackup,
              icon: const Icon(Icons.verified_outlined),
              label: const Text('Verify backup'),
            ),
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading ? null : _restoreProjectBackup,
              icon: const Icon(Icons.settings_backup_restore_outlined),
              label: const Text('Restore project'),
            ),
            FilledButton.tonalIcon(
              onPressed: _busy || _statusLoading ? null : _openPdf,
              icon: const Icon(Icons.picture_as_pdf_outlined),
              label: Text(_pdfPath == null ? 'Open source PDF' : _basename(_pdfPath!)),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _openEpub(left: true),
              icon: const Icon(Icons.menu_book_outlined),
              label: Text(_aPath == null ? 'Open Candidate A EPUB' : 'Replace A · ${_basename(_aPath!)}'),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _openEpub(left: false),
              icon: const Icon(Icons.menu_book_outlined),
              label: Text(_bPath == null ? 'Open Candidate B EPUB' : 'Replace B · ${_basename(_bPath!)}'),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : _setupRuntimes,
              icon: const Icon(Icons.settings_suggest_outlined),
              label: const Text('Set up OCR'),
            ),
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading || _pdfPath == null || !_projectSourceMatches ? null : _runOcr,
              icon: const Icon(Icons.document_scanner_outlined),
              label: const Text('Run OCR'),
            ),
            FilledButton.tonalIcon(
              onPressed: _busy || _statusLoading || _pdfPath == null || !_projectSourceMatches || _projectStatus == null ? null : _buildCandidates,
              icon: const Icon(Icons.auto_stories_outlined),
              label: const Text('Build A/B EPUBs'),
            ),
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading || _projectStatus == null || !_projectSourceMatches
                  ? null
                  : _exportVerifiedBook,
              icon: const Icon(Icons.file_download_done_outlined),
              label: const Text('Export verified book'),
            ),
            if (_projectStoreExplicit && _projectStatus != null && !_projectSourceMatches)
              OutlinedButton.icon(
                onPressed: _busy || _statusLoading ? null : _relinkSourcePdf,
                icon: const Icon(Icons.link_outlined),
                label: const Text('Relink source PDF'),
              ),
            if (_busy)
              const SizedBox(width: 20, height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2)),
          ],
        ),
      );


  Widget _projectStatusPanel() {
    final status = _projectStatus;
    final failed = status?.failedJobs ?? 0;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(14, 0, 14, 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: const Color(0xFFF7F9F8),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFE2E7E4)),
      ),
      child: Row(
        children: [
          const Icon(Icons.monitor_heart_outlined, size: 19, color: Color(0xFF53675E)),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Text(
                      status?.title ?? 'Project status',
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
                    ),
                    if (_statusLoading) ...[
                      const SizedBox(width: 8),
                      const SizedBox(
                        width: 12,
                        height: 12,
                        child: CircularProgressIndicator(strokeWidth: 1.8),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 4),
                if (!_projectSourceMatches && _projectSourceMismatchMessage != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 5),
                    child: Text(
                      _projectSourceMismatchMessage!,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 10, color: Color(0xFF9A4E12), fontWeight: FontWeight.w600),
                    ),
                  ),
                if (status == null)
                  Text(
                    _projectStatusMessage ?? 'Status has not been loaded yet.',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 10, color: Color(0xFF68766F)),
                  )
                else ...[
                  Row(
                    children: [
                      SizedBox(
                        width: 100,
                        child: LinearProgressIndicator(
                          value: status.ocrProgress,
                          minHeight: 5,
                          backgroundColor: const Color(0xFFE2E7E4),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '${status.progressLabel} · ${status.reviewQueued} pairs need human review',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 10, color: Color(0xFF53675E)),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(
                    status.jobsLabel,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 10,
                      color: failed > 0 ? const Color(0xFF9A5D16) : const Color(0xFF7D8983),
                    ),
                  ),
                  if (failed > 0 && status.failedJobDetails.isNotEmpty)
                    Text(
                      'Latest failure: ${status.failedJobDetails.first.description}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 10, color: Color(0xFF9A5D16)),
                    ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            tooltip: 'Refresh project status',
            visualDensity: VisualDensity.compact,
            onPressed: _statusLoading || _busy ? null : _refreshProjectStatus,
            icon: const Icon(Icons.refresh, size: 18),
          ),
          if (failed > 0 && _projectSourceMatches)
            OutlinedButton.icon(
              onPressed: _busy || _statusLoading ? null : _runOcr,
              icon: const Icon(Icons.replay, size: 15),
              label: const Text('Retry failed pages'),
            ),
        ],
      ),
    );
  }

  Widget _noticeBar(String message) => Container(
        width: double.infinity,
        margin: const EdgeInsets.fromLTRB(14, 0, 14, 7),
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: message.contains('changed') || message.contains('not restored')
              ? const Color(0xFFFFF2DD)
              : const Color(0xFFEAF4F0),
          borderRadius: BorderRadius.circular(7),
        ),
        child: Row(children: [
          const Icon(Icons.info_outline, size: 16),
          const SizedBox(width: 7),
          Expanded(child: Text(message, style: const TextStyle(fontSize: 11))),
          IconButton(
            onPressed: () => setState(() => _notice = null),
            visualDensity: VisualDensity.compact,
            icon: const Icon(Icons.close, size: 15),
          ),
        ]),
      );

  Widget _pageToolbar(TextDiffSummary summary, bool aligned) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 9),
        child: Row(children: [
          const Text('COMPARE', style: TextStyle(fontSize: 10, letterSpacing: 1.1,
              color: Color(0xFF718079), fontWeight: FontWeight.w800)),
          const SizedBox(width: 12),
          IconButton(
            visualDensity: VisualDensity.compact,
            onPressed: _page > 0 ? () => setState(() => _page--) : null,
            icon: const Icon(Icons.chevron_left),
          ),
          Text(_pageCount > 0 ? 'Page ${_page + 1} / $_pageCount' : 'Page —',
              style: const TextStyle(fontWeight: FontWeight.w600)),
          IconButton(
            visualDensity: VisualDensity.compact,
            onPressed: _pageCount > 0 && _page < _pageCount - 1
                ? () => setState(() => _page++)
                : null,
            icon: const Icon(Icons.chevron_right),
          ),
          if (_a != null && _b != null)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(color: const Color(0xFFE8EFEB),
                  borderRadius: BorderRadius.circular(18)),
              child: Text('${summary.changedRows} differing rows · ${summary.sharedLines} identical',
                  style: const TextStyle(fontSize: 11, color: Color(0xFF53675E))),
            ),
          const Spacer(),
          if (!aligned)
            const Tooltip(
              message: 'At least one EPUB is chapter-based. Its page mapping is approximate.',
              child: Row(children: [
                Icon(Icons.warning_amber_rounded, size: 16, color: Color(0xFF94601A)),
                SizedBox(width: 5),
                Text('Chapter mapping is approximate', style: TextStyle(fontSize: 10)),
              ]),
            ),
        ]),
      );

  Widget _pdfPanel() => _panelCard(
        title: 'Original scan',
        subtitle: 'Immutable PDF source',
        icon: Icons.picture_as_pdf_outlined,
        child: _pdfPath == null
            ? _empty('Open the original PDF',
                'Compare omissions, punctuation, layout and reading order against this page.')
            : PdfDocumentViewBuilder.file(
                _pdfPath!,
                builder: (context, document) {
                  if (document == null) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  final count = document.pages.length;
                  if (_pdfPageCount != count) {
                    WidgetsBinding.instance.addPostFrameCallback((_) {
                      if (mounted && _pdfPageCount != count) {
                        setState(() {
                          _pdfPageCount = count;
                          _page = _page.clamp(0, count > 0 ? count - 1 : 0);
                        });
                      }
                    });
                  }
                  if (count == 0) {
                    return const Center(child: Text('PDF has no pages.'));
                  }
                  return ColoredBox(
                    color: const Color(0xFFE8ECEA),
                    child: PdfPageView(
                      document: document,
                      pageNumber: _page.clamp(0, count - 1) + 1,
                      alignment: Alignment.topCenter,
                    ),
                  );
                },
              ),
        footer: _pdfPath,
      );

  Widget _epubPanel({
    required bool left,
    required EpubCandidate? candidate,
    required EpubPage? page,
    required List<TextDiffRow> rows,
  }) {
    final accent = left ? const Color(0xFF1F7867) : const Color(0xFF7B5DAA);
    final label = left ? _labelA : _labelB;
    final letter = left ? 'A' : 'B';
    return _panelCard(
      title: left ? 'Candidate A' : 'Candidate B',
      subtitle: 'Human review · raw text comparison',
      icon: Icons.menu_book_outlined,
      accent: accent,
      footer: left ? _aPath : _bPath,
      headerExtra: candidate == null
          ? null
          : Text('${candidate.pages.length} spine items',
              style: const TextStyle(fontSize: 10, color: Color(0xFF718079))),
      child: candidate == null
          ? _empty('Load EPUB candidate $letter',
              'The two EPUB candidates remain separate; no automatic winner is inferred.')
          : page == null
              ? _empty('No item for source page ${_page + 1}',
                  candidate.isSourcePageAligned
                      ? 'This candidate lacks an XHTML item for this source page.'
                      : 'Try another page; this EPUB is chapter-based.')
              : Column(
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(10, 0, 10, 7),
                      child: TextField(
                        controller: label,
                        onChanged: (_) => setState(() {}),
                        decoration: const InputDecoration(
                          hintText: 'Candidate label',
                          isDense: true,
                          prefixIcon: Icon(Icons.edit_outlined, size: 16),
                        ),
                      ),
                    ),
                    Container(
                      alignment: Alignment.centerLeft,
                      padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
                      color: const Color(0xFFF6F8F6),
                      child: Text(page.title,
                          maxLines: 1, overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 10, color: Color(0xFF68766F))),
                    ),
                    Expanded(
                      child: ListView.builder(
                        padding: const EdgeInsets.all(8),
                        itemCount: rows.isEmpty ? page.lines.length : rows.length,
                        itemBuilder: (context, index) {
                          if (rows.isEmpty) {
                            return _line(page.lines[index], false, absent: false);
                          }
                          final row = rows[index];
                          final text = left ? row.left : row.right;
                          return _line(text ?? '∅  [no corresponding line]', row.changed,
                              absent: text == null);
                        },
                      ),
                    ),
                  ],
                ),
    );
  }

  Widget _line(String text, bool changed, {required bool absent}) => Container(
        margin: const EdgeInsets.only(bottom: 3),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(
          color: changed ? const Color(0xFFFFF2DB) : Colors.transparent,
          borderRadius: BorderRadius.circular(5),
          border: changed
              ? const Border(left: BorderSide(color: Color(0xFFD49A36), width: 3))
              : null,
        ),
        child: SelectableText(text, style: TextStyle(
          fontSize: 12,
          height: 1.5,
          color: absent ? const Color(0xFF989F9B) : const Color(0xFF27332D),
          fontStyle: absent ? FontStyle.italic : FontStyle.normal,
        )),
      );

  Widget _panelCard({
    required String title,
    required String subtitle,
    required IconData icon,
    required Widget child,
    String? footer,
    Color accent = const Color(0xFF536575),
    Widget? headerExtra,
  }) => Card(
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(11, 11, 11, 9),
              child: Row(children: [
                Icon(icon, size: 18, color: accent),
                const SizedBox(width: 7),
                Expanded(child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 2),
                    Text(subtitle, style: const TextStyle(fontSize: 10, color: Color(0xFF7D8983))),
                  ],
                )),
                ?headerExtra,
              ]),
            ),
            const Divider(height: 1),
            Expanded(child: child),
            if (footer != null)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
                color: const Color(0xFFF8F9F8),
                child: Text(footer, maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 9, color: Color(0xFF8A9690))),
              ),
          ],
        ),
      );

  Widget _empty(String title, String detail) => Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.document_scanner_outlined, size: 34, color: Color(0xFFB2BEB8)),
            const SizedBox(height: 10),
            Text(title, textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(detail, textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 11, height: 1.45, color: Color(0xFF7D8983))),
          ]),
        ),
      );

  Widget _evaluationPanel() => Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(14, 9, 14, 11),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: Color(0xFFE2E7E4))),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min,
          children: [
            Row(children: [
              const Icon(Icons.fact_check_outlined, size: 18, color: Color(0xFF236B5D)),
              const SizedBox(width: 7),
              const Text('Human evaluation', style: TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(width: 12),
              const Text('This page:', style: TextStyle(fontSize: 11, color: Color(0xFF718079))),
              const SizedBox(width: 5),
              _chip('A better', 'a', _decisions[_page], (v) => setState(() => _decisions[_page] = v)),
              _chip('B better', 'b', _decisions[_page], (v) => setState(() => _decisions[_page] = v)),
              _chip('Tie', 'tie', _decisions[_page], (v) => setState(() => _decisions[_page] = v)),
              _chip('Review later', 'review', _decisions[_page], (v) => setState(() => _decisions[_page] = v)),
              const Spacer(),
              FilledButton.icon(
                onPressed: _busy || _pdfPath == null || _a == null || _b == null ? null : _save,
                icon: const Icon(Icons.save_outlined, size: 16),
                label: const Text('Save evaluation'),
              ),
            ]),
            const SizedBox(height: 5),
            Row(children: [
              const SizedBox(width: 110, child: Text('Whole-book winner', style: TextStyle(fontSize: 11))),
              _chip('Candidate A', 'a', _winner, (v) => setState(() => _winner = v)),
              _chip('Candidate B', 'b', _winner, (v) => setState(() => _winner = v)),
              _chip('No clear winner', 'tie', _winner, (v) => setState(() => _winner = v)),
              _chip('More review', 'review', _winner, (v) => setState(() => _winner = v)),
              const SizedBox(width: 10),
              Expanded(child: SizedBox(height: 36, child: TextField(
                controller: _notes,
                decoration: const InputDecoration(
                  hintText: 'Notes: missing lines, punctuation, order, layout…',
                  prefixIcon: Icon(Icons.notes_outlined, size: 17),
                ),
              ))),
            ]),
            const SizedBox(height: 4),
            Text(
              _pdfPath == null
                  ? 'Open a PDF and two EPUBs. The app records human choices; it does not infer an automatic winner.'
                  : 'Review data is saved beside the PDF with SHA-256 fingerprints.',
              maxLines: 1, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 10, color: Color(0xFF7D8983)),
            ),
          ],
        ),
      );

  Widget _chip(String label, String value, String? selected, ValueChanged<String> onSelected) =>
      Padding(
        padding: const EdgeInsets.only(right: 4),
        child: ChoiceChip(
          label: Text(label, style: const TextStyle(fontSize: 10)),
          selected: selected == value,
          onSelected: (_) => onSelected(value),
          visualDensity: VisualDensity.compact,
          padding: const EdgeInsets.symmetric(horizontal: 3),
        ),
      );

  String _basename(String path) => path.replaceAll('\\', '/').split('/').last;
}
