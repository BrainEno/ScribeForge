import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/unified_workspace.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';
import 'package:scribeforge_desktop/services/workspace_file_system.dart';

void main() {
  Future<void> pumpUntil(
    WidgetTester tester,
    bool Function() condition, {
    required String description,
  }) async {
    for (var attempt = 0; attempt < 200; attempt++) {
      await tester.pump(const Duration(milliseconds: 50));
      if (condition()) return;
    }
    fail('Timed out waiting for $description.');
  }

  Future<_Harness> createHarness(
    WidgetTester tester, {
    List<int> processResults = const <int>[0],
    List<int> exportResults = const <int>[0],
    List<Map<String, Object?>>? reviewItems,
    bool holdProcess = false,
  }) async {
    late Directory root;
    await tester.runAsync(() async {
      root = await Directory.systemTemp.createTemp('scribeforge-workflow-');
    });
    addTearDown(() {
      if (root.existsSync()) root.deleteSync(recursive: true);
    });
    final source = File('${root.path}${Platform.pathSeparator}Sample.pdf');
    final exportDirectory = Directory('${root.path}${Platform.pathSeparator}exports');
    final sourceBytes = <int>[37, 80, 68, 70, 45, 49, 46, 55];
    source.writeAsBytesSync(sourceBytes);
    exportDirectory.createSync();
    final sourceHash = sha256.convert(sourceBytes).toString();
    final fileSystem = _FakeWorkspaceFileSystem(
      sourcePath: source.path,
      sourceBytes: sourceBytes,
    );
    final picker = _FakeDesktopFilePicker(
      files: <String?>[source.path],
      directories: <String?>[exportDirectory.path],
    );
    final cli = _FakeCliExecutor(
      sourcePath: source.path,
      sourceHash: sourceHash,
      fileSystem: fileSystem,
      processResults: processResults,
      exportResults: exportResults,
      reviewItems: reviewItems ?? <Map<String, Object?>>[_reviewItem(0, '鸦', '雅')],
      holdProcess: holdProcess,
    );

    tester.view.physicalSize = const Size(1440, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: UnifiedWorkspace(
          cliExecutor: cli,
          filePicker: picker,
          settingsStore: _MemorySettingsStore(),
          fileSystem: fileSystem,
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    return _Harness(source: source, picker: picker, cli: cli);
  }

  Future<void> openTab(WidgetTester tester, String title) async {
    final rail = find.byType(NavigationRail);
    await tester.tap(find.descendant(of: rail, matching: find.text(title)));
    await tester.pump();
  }

  Future<void> importBook(WidgetTester tester) async {
    await openTab(tester, '书籍');
    await tester.tap(find.text('导入书籍'));
    await pumpUntil(
      tester,
      () => find.textContaining('Sample.pdf').evaluate().isNotEmpty,
      description: 'the selected source file after import',
    );
  }

  Future<void> waitForWorkflowIdle(WidgetTester tester) async {
    for (var attempt = 0; attempt < 200; attempt++) {
      await tester.pump(const Duration(milliseconds: 50));
      if (find.byType(CircularProgressIndicator).evaluate().isEmpty) {
        await tester.pump();
        return;
      }
    }
    fail('The fake-CLI workflow did not become idle within ten seconds.');
  }

  testWidgets('imports, processes, reviews and exports through a replaceable CLI',
      (tester) async {
    final harness = await createHarness(tester);

    await importBook(tester);
    expect(find.textContaining('Sample.pdf'), findsWidgets);
    await tester.tap(find.text('开始处理'));
    await waitForWorkflowIdle(tester);

    final processCall = harness.cli.calls.singleWhere(
      (arguments) => arguments.first == 'process',
    );
    expect(processCall, contains(harness.source.path));
    expect(processCall, isNot(contains('--vlm-endpoint')));

    await openTab(tester, '复核');
    expect(find.text('第 1 页 · 第 1 组'), findsOneWidget);
    await tester.tap(find.text('填入 OCR 1'));
    await tester.pump();
    expect(
      harness.cli.calls.where((arguments) => arguments.first == 'resolve'),
      isEmpty,
      reason: 'Choosing a candidate fills the editor but is not a decision.',
    );
    await tester.tap(find.text('记录人工决策'));
    await waitForWorkflowIdle(tester);
    expect(
      harness.cli.calls.where((arguments) => arguments.first == 'resolve'),
      hasLength(1),
    );
    expect(harness.cli.lastHumanDecisionText, '鸦');
    expect(find.text('暂无待处理配对。'), findsOneWidget);

    await openTab(tester, '导出');
    await tester.tap(find.byWidgetPredicate(
      (widget) => widget is TextField && widget.readOnly,
    ));
    await tester.pump();
    await tester.tap(find.text('开始导出'));
    await waitForWorkflowIdle(tester);

    expect(
      harness.cli.calls.where((arguments) => arguments.first == 'export'),
      hasLength(1),
    );
    expect(harness.cli.lastExportPath, contains('Sample.epub'));
    expect(File(harness.cli.lastExportPath!).existsSync(), isTrue);
  });

  testWidgets('a cancelled import does not invoke processing or create a project',
      (tester) async {
    final harness = await createHarness(tester);
    harness.picker.files.addFirst(null);

    await openTab(tester, '书籍');
    await tester.tap(find.text('导入书籍'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(harness.cli.calls.where((args) => args.first == 'process'), isEmpty);
    expect(harness.cli.projectCreated, isFalse);
    expect(find.text('尚未选择书籍或打开项目。'), findsOneWidget);
  });

  testWidgets('a failed page run is visible and can be retried', (tester) async {
    final harness = await createHarness(tester, processResults: <int>[1, 0]);
    await importBook(tester);

    await tester.tap(find.text('开始处理'));
    await waitForWorkflowIdle(tester);
    expect(find.text('重试失败页面'), findsOneWidget);
    expect(find.textContaining('simulated worker failure'), findsOneWidget);

    await tester.tap(find.text('重试失败页面'));
    await waitForWorkflowIdle(tester);
    expect(find.text('开始处理'), findsOneWidget);
    expect(
      harness.cli.calls.where((args) => args.first == 'process'),
      hasLength(2),
    );
  });

  testWidgets('running processing can be cancelled and retried', (tester) async {
    final harness = await createHarness(tester, holdProcess: true);
    await importBook(tester);

    await tester.tap(find.text('开始处理'));
    await tester.pump();
    expect(find.text('取消处理'), findsOneWidget);
    expect(
      harness.cli.calls.where((args) => args.first == 'process').single,
      contains('--cancelable'),
    );

    await tester.tap(find.text('取消处理'));
    await pumpUntil(
      tester,
      () => find.text('取消处理').evaluate().isEmpty &&
          find.text('正在取消…').evaluate().isEmpty,
      description: 'the cancelled process to exit',
    );

    expect(harness.cli.cancelRequests, 1);
    expect(find.textContaining('处理已取消'), findsOneWidget);
    expect(find.text('重试失败页面'), findsOneWidget);

    await tester.tap(find.text('重试失败页面'));
    await waitForWorkflowIdle(tester);
    expect(find.text('开始处理'), findsOneWidget);
    expect(
      harness.cli.calls.where((args) => args.first == 'process'),
      hasLength(2),
    );
  });

  testWidgets('an export failure can be retried without reprocessing the source',
      (tester) async {
    final harness = await createHarness(tester, exportResults: <int>[2, 0]);
    await importBook(tester);
    await tester.tap(find.text('开始处理'));
    await waitForWorkflowIdle(tester);
    await openTab(tester, '复核');
    await tester.tap(find.text('填入 OCR 1'));
    await tester.tap(find.text('记录人工决策'));
    await waitForWorkflowIdle(tester);
    await openTab(tester, '导出');
    await tester.tap(find.byWidgetPredicate(
      (widget) => widget is TextField && widget.readOnly,
    ));
    await tester.pump();

    await tester.tap(find.text('开始导出'));
    await waitForWorkflowIdle(tester);
    expect(find.textContaining('simulated export failure'), findsOneWidget);

    await tester.tap(find.text('开始导出'));
    await waitForWorkflowIdle(tester);
    expect(
      harness.cli.calls.where((args) => args.first == 'process'),
      hasLength(1),
    );
    expect(
      harness.cli.calls.where((args) => args.first == 'export'),
      hasLength(2),
    );
  });

  testWidgets('queue navigation, page jump and focus shortcuts do not decide items',
      (tester) async {
    final harness = await createHarness(
      tester,
      reviewItems: <Map<String, Object?>>[
        _reviewItem(0, '第一项', '候选一'),
        _reviewItem(4, '第二项', '候选二'),
      ],
    );
    await importBook(tester);
    await tester.tap(find.text('开始处理'));
    await waitForWorkflowIdle(tester);
    await openTab(tester, '复核');

    expect(find.text('第 1 页 · 第 1 组'), findsOneWidget);
    await tester.tap(find.byTooltip('下一项'));
    await tester.pump();
    expect(find.text('第 5 页 · 第 1 组'), findsOneWidget);
    await tester.tap(find.byTooltip('上一项'));
    await tester.pump();
    expect(find.text('第 1 页 · 第 1 组'), findsOneWidget);

    final pageJump = find.byKey(const ValueKey('review-page-jump'));
    await tester.enterText(pageJump, '5');
    await tester.tap(find.text('跳转'));
    await tester.pump();
    expect(find.text('第 5 页 · 第 1 组'), findsOneWidget);

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyJ);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyJ);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    expect(
      tester.widget<TextField>(find.byKey(const ValueKey('review-decision-input')))
          .focusNode!
          .hasFocus,
      isTrue,
    );

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyG);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyG);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    expect(
      tester.widget<TextField>(pageJump).focusNode!.hasFocus,
      isTrue,
    );

    await tester.sendKeyDownEvent(LogicalKeyboardKey.altLeft);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.arrowLeft);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.arrowLeft);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.altLeft);
    await tester.pump();
    expect(find.text('第 1 页 · 第 1 组'), findsOneWidget);
    expect(
      harness.cli.calls.where((args) => args.first == 'resolve'),
      isEmpty,
    );
  });

  testWidgets('opens project audit and backup tools from the integrated workspace',
      (tester) async {
    await createHarness(tester);
    await openTab(tester, '项目安全');

    expect(find.byKey(const ValueKey('project-safety-page')), findsOneWidget);
    expect(find.text('项目安全中心'), findsOneWidget);
    expect(find.text('请先打开项目'), findsOneWidget);
    expect(
      tester.widget<FilledButton>(find.widgetWithText(FilledButton, '创建项目备份'))
          .onPressed,
      isNull,
    );
    expect(find.text('选择备份文件'), findsOneWidget);
  });

  testWidgets('VLM remains off until the user enables it in settings', (tester) async {
    final harness = await createHarness(tester);
    await openTab(tester, '设置');
    expect(find.textContaining('默认关闭'), findsOneWidget);
    await tester.tap(find.text('配置 VLM 复核'));
    await pumpUntil(
      tester,
      () => find.text('Enable VLM review during OCR').evaluate().isNotEmpty,
      description: 'the VLM settings dialog',
    );

    final enabledSwitch = find.ancestor(
      of: find.text('Enable VLM review during OCR'),
      matching: find.byType(SwitchListTile),
    );
    expect(tester.widget<SwitchListTile>(enabledSwitch).value, isFalse);
    await tester.tap(find.text('Enable VLM review during OCR'));
    await tester.enterText(
      find.widgetWithText(TextFormField, 'OpenAI-compatible endpoint'),
      'http://127.0.0.1:1234/v1',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Vision model'),
      'fixture-vision',
    );
    await tester.tap(find.text('Save'));
    await pumpUntil(
      tester,
      () => find.text('Save').evaluate().isEmpty,
      description: 'the VLM settings dialog to close',
    );

    await importBook(tester);
    await tester.tap(find.text('开始处理'));
    await waitForWorkflowIdle(tester);
    final processCall = harness.cli.calls.singleWhere(
      (arguments) => arguments.first == 'process',
    );
    expect(processCall, contains('--vlm-endpoint'));
    expect(processCall, contains('http://127.0.0.1:1234/v1'));
    expect(processCall, contains('--vlm-model'));
    expect(processCall, contains('fixture-vision'));
  });
}

Map<String, Object?> _reviewItem(int pageIndex, String primary, String secondary) =>
    <String, Object?>{
      'page_index': pageIndex,
      'pair_index': 0,
      'score': 0.8,
      'reasons': <String>['text_disagreement'],
      'primary_text': primary,
      'secondary_text': secondary,
      'vlm_text': null,
      'vlm_uncertain': false,
      'crop_path': null,
      'source_image_path': '',
    };

class _Harness {
  const _Harness({
    required this.source,
    required this.picker,
    required this.cli,
  });

  final File source;
  final _FakeDesktopFilePicker picker;
  final _FakeCliExecutor cli;
}

class _MemorySettingsStore extends DesktopSettingsStore {
  _MemorySettingsStore() : super();

  String _cliExecutable = DesktopSettingsStore.defaultCliExecutable;
  DesktopVlmSettings _vlmSettings = const DesktopVlmSettings();

  @override
  Future<String> loadCliExecutable() async => _cliExecutable;

  @override
  Future<void> saveCliExecutable(String executable) async {
    _cliExecutable = executable;
  }

  @override
  Future<DesktopVlmSettings> loadVlmSettings() async => _vlmSettings;

  @override
  Future<void> saveVlmSettings(DesktopVlmSettings settings) async {
    _vlmSettings = settings;
  }
}

class _FakeWorkspaceFileSystem implements WorkspaceFileSystem {
  _FakeWorkspaceFileSystem({
    required this.sourcePath,
    required List<int> sourceBytes,
  })  : sourceBytes = List<int>.unmodifiable(sourceBytes),
        _existingFiles = <String>{sourcePath};

  final String sourcePath;
  final List<int> sourceBytes;
  final Set<String> _existingFiles;

  void addProjectStore(String storePath) {
    _existingFiles.add('$storePath${Platform.pathSeparator}project.sqlite3');
  }

  @override
  Future<bool> fileExists(String path) async => _existingFiles.contains(path);

  @override
  Future<bool> directoryExists(String path) async => false;

  @override
  Future<WorkspaceFileMetadata?> stat(String path) async {
    if (path != sourcePath) return null;
    return WorkspaceFileMetadata(
      size: sourceBytes.length,
      modifiedMilliseconds: 1,
    );
  }

  @override
  Stream<List<int>> openRead(String path) {
    if (path != sourcePath) {
      return Stream<List<int>>.error(FileSystemException('Unknown fake file', path));
    }
    return Stream<List<int>>.value(sourceBytes);
  }
}

class _FakeDesktopFilePicker implements DesktopFilePicker {
  _FakeDesktopFilePicker({
    required List<String?> files,
    required List<String?> directories,
  })  : files = Queue<String?>.from(files),
        directories = Queue<String?>.from(directories);

  final Queue<String?> files;
  final Queue<String?> directories;

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

class _FakeCliExecutor implements ScribeForgeCliExecutor {
  _FakeCliExecutor({
    required this.sourcePath,
    required this.sourceHash,
    required this.fileSystem,
    required List<int> processResults,
    required List<int> exportResults,
    required this.reviewItems,
    required bool holdProcess,
  })  : _processResults = Queue<int>.from(processResults),
        _exportResults = Queue<int>.from(exportResults),
        _holdNextProcess = holdProcess;

  final String sourcePath;
  final String sourceHash;
  final _FakeWorkspaceFileSystem fileSystem;
  final Queue<int> _processResults;
  final Queue<int> _exportResults;
  final List<Map<String, Object?>> reviewItems;
  bool _holdNextProcess;
  Completer<int>? _pendingProcess;
  int cancelRequests = 0;
  final List<List<String>> calls = <List<String>>[];
  final Set<String> _resolved = <String>{};
  bool projectCreated = false;
  bool _partialFailure = false;
  String? lastHumanDecisionText;
  String? lastExportPath;

  @override
  Future<void> cancel() async {
    cancelRequests++;
    final pending = _pendingProcess;
    if (pending != null && !pending.isCompleted) pending.complete(130);
  }

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    final args = List<String>.unmodifiable(arguments);
    calls.add(args);
    switch (args.first) {
      case 'runtime-status':
        onOutput(jsonEncode(<String, Object?>{
          'schema_version': 1,
          'runtime_root': '/tmp/scribeforge-runtime',
          'runtime_root_exists': true,
          'uv_available': true,
          'hardware': <String, Object?>{},
          'paths': <String, Object?>{},
          'backends': <String, Object?>{},
        }));
        return 0;
      case 'process':
        final storePath = args[args.indexOf('--store') + 1];
        if (_holdNextProcess) {
          _holdNextProcess = false;
          fileSystem.addProjectStore(storePath);
          projectCreated = true;
          _pendingProcess = Completer<int>();
          onOutput('processing started');
          final result = await _pendingProcess!.future;
          _partialFailure = result == 1 || result == 130;
          onOutput(result == 130 ? 'processing cancelled' : 'processed');
          return result;
        }
        final result = _processResults.isEmpty ? 0 : _processResults.removeFirst();
        if (result == 2) {
          onError('simulated fatal processing failure');
          return result;
        }
        fileSystem.addProjectStore(storePath);
        projectCreated = true;
        _partialFailure = result == 1 || result == 130;
        onOutput(result == 130 ? 'processing cancelled' : 'processed');
        return result;
      case 'status':
        onOutput(jsonEncode(_statusPayload()));
        return 0;
      case 'review':
        onOutput(jsonEncode(_reviewPayload()));
        return 0;
      case 'resolve':
        final page = args[args.indexOf('--page') + 1];
        final pair = args[args.indexOf('--pair') + 1];
        _resolved.add('$page-$pair');
        lastHumanDecisionText = args[args.indexOf('--text') + 1];
        onOutput('human decision recorded');
        return 0;
      case 'export':
        final result = _exportResults.isEmpty ? 0 : _exportResults.removeFirst();
        final outputPath = args[args.indexOf('--out') + 1];
        lastExportPath = outputPath;
        if (result != 0) {
          onError('simulated export failure');
          return result;
        }
        File(outputPath).parent.createSync(recursive: true);
        File(outputPath).writeAsStringSync('fake exported book');
        onOutput('exported');
        return 0;
      default:
        onError('unexpected fake CLI command: ${args.first}');
        return 2;
    }
  }

  Map<String, Object?> _statusPayload() => <String, Object?>{
        'schema_version': 1,
        'store': '/fake/store',
        'projects': <Map<String, Object?>>[
          <String, Object?>{
            'id': 1,
            'title': 'Sample',
            'source_uri': sourcePath,
            'source_sha256': sourceHash,
            'pages_total': 1,
            'pages_with_both_ocr_success': _partialFailure ? 0 : 1,
            'review_queued': _pendingReviewItems.length,
            'job_counts': <String, int>{
              'pending': _partialFailure ? 1 : 0,
              'running': 0,
              'succeeded': _partialFailure ? 4 : 5,
              'failed': _partialFailure ? 1 : 0,
            },
            'failed_jobs': _partialFailure
                ? <Map<String, Object?>>[
                    <String, Object?>{
                      'stage': 'primary_ocr',
                      'page_index': 0,
                      'attempts': 1,
                      'last_error': 'simulated worker failure',
                    },
                  ]
                : <Map<String, Object?>>[],
            'active_jobs': <Map<String, Object?>>[],
          },
        ],
      };

  Map<String, Object?> _reviewPayload() => <String, Object?>{
        'schema_version': 1,
        'store': '/fake/store',
        'projects': <Map<String, Object?>>[
          <String, Object?>{
            'id': 1,
            'title': 'Sample',
            'source_uri': sourcePath,
            'items': _pendingReviewItems
                .map((item) => <String, Object?>{
                      ...item,
                      'source_image_path': sourcePath,
                    })
                .toList(growable: false),
          },
        ],
      };

  List<Map<String, Object?>> get _pendingReviewItems => reviewItems
      .where((item) => !_resolved.contains('${item['page_index']}-${item['pair_index']}'))
      .toList(growable: false);
}
