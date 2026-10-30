import 'dart:collection';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/project_safety_screen.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/workspace_file_system.dart';

void main() {
  const storePath = '/books/Study.scribeforge';
  const projectTitle = 'Study';
  const cliExecutable = '/tools/scribeforge';

  late _FakeCliExecutor cli;
  late _FakeDesktopFilePicker filePicker;
  late _FakeWorkspaceFileSystem fileSystem;

  Future<void> pumpScreen(
    WidgetTester tester, {
    Future<void> Function(String path)? onProjectRestored,
  }) async {
    tester.view.physicalSize = const Size(1440, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectSafetyScreen(
            storePath: storePath,
            projectTitle: projectTitle,
            cliExecutable: cliExecutable,
            cliExecutor: cli,
            filePicker: filePicker,
            fileSystem: fileSystem,
            onProjectRestored: onProjectRestored ?? (_) async {},
          ),
        ),
      ),
    );
    await tester.pump();
  }

  Future<void> pumpUntil(
    WidgetTester tester,
    Finder finder, {
    required String description,
  }) async {
    for (var attempt = 0; attempt < 100; attempt++) {
      await tester.pump(const Duration(milliseconds: 50));
      if (finder.evaluate().isNotEmpty) return;
    }
    fail('Timed out waiting for $description.');
  }

  setUp(() {
    cli = _FakeCliExecutor();
    filePicker = _FakeDesktopFilePicker();
    fileSystem = _FakeWorkspaceFileSystem();
  });

  testWidgets('audits and creates a non-overwriting project backup',
      (tester) async {
    cli.enqueue('audit', _FakeResult(0, _healthyAuditReport));
    cli.enqueue('backup', const _FakeResult(0, '@backup-report'));
    filePicker.directories.add('/tmp/archives');
    await pumpScreen(tester);

    await tester.tap(find.text('运行完整性审计'));
    await pumpUntil(
      tester,
      find.text('项目完整性正常'),
      description: 'the read-only audit result',
    );

    await tester.tap(find.text('选择备份位置'));
    await tester.pump();
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('backup-directory-path')))
          .controller!
          .text,
      '/tmp/archives',
    );

    await tester.tap(find.text('创建项目备份'));
    await pumpUntil(
      tester,
      find.text('备份已创建'),
      description: 'the verified project backup',
    );

    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'audit',
      'backup',
    ]);
    final backupCall = cli.calls.last;
    expect(
      backupCall,
      ScribeForgeCli.backupArguments(
        storePath: storePath,
        outputPath: backupCall[backupCall.indexOf('--out') + 1],
      ),
    );
    final outputPath = backupCall[backupCall.indexOf('--out') + 1];
    expect(outputPath, startsWith('/tmp/archives/Study-backup-'));
    expect(outputPath, endsWith('.zip'));
    expect(backupCall, isNot(contains('--overwrite')));
  });

  testWidgets('verifies a backup before explicit restore to a new project directory',
      (tester) async {
    cli.enqueue('verify-backup', _FakeResult(0, _healthyVerificationReport));
    cli.enqueue('restore', const _FakeResult(0, '@restore-report'));
    fileSystem.files['/tmp/archives/Study.zip'] = <int>[1, 2, 3, 4];
    filePicker.files.add('/tmp/archives/Study.zip');
    filePicker.directories.add('/tmp/restored-projects');
    String? restoredStorePath;
    await pumpScreen(
      tester,
      onProjectRestored: (path) async {
        restoredStorePath = path;
      },
    );

    await tester.tap(find.text('选择备份文件'));
    await tester.pump();
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('restore-backup-path')))
          .controller!
          .text,
      '/tmp/archives/Study.zip',
    );
    await tester.tap(find.text('验证备份'));
    await pumpUntil(
      tester,
      find.text('备份验证通过'),
      description: 'the archive integrity verification',
    );

    await tester.tap(find.text('选择恢复父目录'));
    await tester.pump();
    await tester.tap(find.text('恢复到新目录'));
    await tester.pumpAndSettle();

    expect(find.textContaining('不会覆盖现有项目'), findsOneWidget);
    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'verify-backup',
    ]);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'verify-backup',
    ]);

    await tester.tap(find.text('恢复到新目录'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('确认恢复'));
    await pumpUntil(
      tester,
      find.text('恢复完成'),
      description: 'the completed restore',
    );

    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'verify-backup',
      'restore',
    ]);
    final restoreCall = cli.calls.last;
    final targetPath = restoreCall[restoreCall.indexOf('--to') + 1];
    expect(targetPath, startsWith('/tmp/restored-projects/'));
    expect(targetPath, contains('restored'));
    expect(targetPath, isNot(storePath));
    expect(restoredStorePath, targetPath);
  });

  testWidgets('refuses an occupied restore destination before invoking the CLI',
      (tester) async {
    cli.enqueue('verify-backup', _FakeResult(0, _healthyVerificationReport));
    fileSystem.files['/tmp/archives/Study.zip'] = <int>[1, 2, 3, 4];
    fileSystem.directoryExistsResult = true;
    filePicker.files.add('/tmp/archives/Study.zip');
    filePicker.directories.add('/tmp/restored-projects');
    await pumpScreen(tester);

    await tester.tap(find.text('选择备份文件'));
    await tester.pump();
    await tester.tap(find.text('验证备份'));
    await pumpUntil(
      tester,
      find.text('备份验证通过'),
      description: 'the archive integrity verification',
    );
    await tester.tap(find.text('选择恢复父目录'));
    await tester.pump();
    await tester.tap(find.text('恢复到新目录'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('确认恢复'));
    await pumpUntil(
      tester,
      find.textContaining('恢复目标目录已存在'),
      description: 'the refusal to overwrite an existing restore destination',
    );

    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'verify-backup',
    ]);
  });

  testWidgets('shows read-only audit findings when the CLI exits partially',
      (tester) async {
    cli.enqueue('audit', _FakeResult(1, _warningAuditReport));
    await pumpScreen(tester);

    await tester.tap(find.text('运行完整性审计'));
    await pumpUntil(
      tester,
      find.text('审计发现需要关注的问题'),
      description: 'the structured audit findings from a partial exit',
    );

    expect(find.textContaining('source_missing'), findsOneWidget);
    expect(find.text('Recorded source file was not found.'), findsOneWidget);
    expect(find.textContaining('审计失败'), findsNothing);
    expect(cli.calls.single.first, 'audit');
  });

  testWidgets('shows audit failures and retries without changing the project',
      (tester) async {
    cli.enqueue(
      'audit',
      const _FakeResult(2, '', 'simulated read-only audit failure'),
    );
    cli.enqueue('audit', _FakeResult(0, _healthyAuditReport));
    await pumpScreen(tester);

    await tester.tap(find.text('运行完整性审计'));
    await pumpUntil(
      tester,
      find.textContaining('simulated read-only audit failure'),
      description: 'the failed audit diagnostic',
    );
    await tester.tap(find.text('运行完整性审计'));
    await pumpUntil(
      tester,
      find.text('项目完整性正常'),
      description: 'the retried audit',
    );

    expect(cli.calls.map((arguments) => arguments.first), <String>[
      'audit',
      'audit',
    ]);
    expect(cli.calls.every((arguments) => arguments.first != 'restore'), isTrue);
  });
}

const _healthyAuditReport = '''
{
  "schema_version": 1,
  "healthy": true,
  "database_readable": true,
  "sqlite_integrity": "ok",
  "foreign_key_violations": 0,
  "project_count": 1,
  "page_count": 3,
  "findings": []
}
''';

const _warningAuditReport = '''
{
  "schema_version": 1,
  "healthy": false,
  "database_readable": true,
  "sqlite_integrity": "ok",
  "foreign_key_violations": 0,
  "project_count": 1,
  "page_count": 3,
  "findings": [
    {
      "code": "source_missing",
      "severity": "warning",
      "message": "Recorded source file was not found.",
      "path": "/books/source.pdf"
    }
  ]
}
''';

const _healthyVerificationReport = '''
{
  "healthy": true,
  "archive": "/tmp/archives/Study.zip",
  "source_store": "/books/Study.scribeforge",
  "created_at": "2026-10-30T00:00:00Z",
  "file_count": 8,
  "total_bytes": 24000,
  "sqlite_integrity": "ok",
  "foreign_key_violations": 0,
  "audit_warnings": []
}
''';

class _FakeResult {
  const _FakeResult(this.exitCode, this.output, [this.error = '']);

  final int exitCode;
  final String output;
  final String error;
}

class _FakeCliExecutor implements ScribeForgeCliExecutor {
  final Map<String, Queue<_FakeResult>> _responses =
      <String, Queue<_FakeResult>>{};
  final List<List<String>> calls = <List<String>>[];

  void enqueue(String command, _FakeResult result) {
    _responses.putIfAbsent(command, () => Queue<_FakeResult>()).add(result);
  }

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    calls.add(List<String>.unmodifiable(arguments));
    final command = arguments.first;
    final queue = _responses[command];
    final result = queue == null || queue.isEmpty
        ? _FakeResult(2, '', 'unexpected fake CLI command: $command')
        : queue.removeFirst();
    var output = result.output;
    if (output == '@backup-report') {
      final archive = arguments[arguments.indexOf('--out') + 1];
      output = jsonEncode(<String, Object>{
        'archive': archive,
        'source_store': '/books/Study.scribeforge',
        'file_count': 8,
        'total_bytes': 24000,
        'created_at': '2026-10-30T00:00:00Z',
        'healthy': true,
      });
    } else if (output == '@restore-report') {
      final target = arguments[arguments.indexOf('--to') + 1];
      output = jsonEncode(<String, Object>{
        'backup': '/tmp/archives/Study.zip',
        'store': target,
        'file_count': 8,
        'total_bytes': 24000,
        'project_count': 1,
        'page_count': 3,
        'healthy': true,
        'audit_warnings': <Object>[],
        'source_store': '/books/Study.scribeforge',
      });
    }
    if (output.isNotEmpty) onOutput(output);
    if (result.error.isNotEmpty) onError(result.error);
    return result.exitCode;
  }
}

class _FakeWorkspaceFileSystem implements WorkspaceFileSystem {
  final Map<String, List<int>> files = <String, List<int>>{};
  bool directoryExistsResult = false;

  @override
  Future<bool> fileExists(String path) async => files.containsKey(path);

  @override
  Future<bool> directoryExists(String path) async => directoryExistsResult;

  @override
  Future<WorkspaceFileMetadata?> stat(String path) async {
    final content = files[path];
    if (content == null) return null;
    return WorkspaceFileMetadata(
      size: content.length,
      modifiedMilliseconds: 0,
    );
  }

  @override
  Stream<List<int>> openRead(String path) {
    final content = files[path];
    if (content == null) {
      return Stream<List<int>>.error(StateError('Unknown fake file: $path'));
    }
    return Stream<List<int>>.value(content);
  }
}

class _FakeDesktopFilePicker implements DesktopFilePicker {
  final Queue<String?> files = Queue<String?>();
  final Queue<String?> directories = Queue<String?>();

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async =>
      files.isEmpty ? null : files.removeFirst();

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async =>
      directories.isEmpty ? null : directories.removeFirst();
}
