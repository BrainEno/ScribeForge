import 'dart:async';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/project_status.dart';
import '../models/review_queue.dart';
import '../models/review_queue_navigation.dart';
import '../models/runtime_setup.dart';
import '../models/runtime_status.dart';
import '../services/candidate_comparison_store.dart';
import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';
import '../services/settings_store.dart';
import '../services/workspace_file_system.dart';
import '../theme/scribe_theme.dart';
import '../widgets/review_keyboard_shortcuts.dart';
import 'candidate_comparison_screen.dart';
import 'project_safety_screen.dart';
import 'runtime_setup_dialog.dart';
import 'vlm_settings_dialog.dart';

/// 一体化书籍数字化工作台。
///
/// 面向新手使用者：把「导入书籍 → 运行处理 → 复核决策 → 导出 EPUB/DOCX」
/// 全流程聚合在一个窗口里，无需接触命令行。首次启动会引导完成运行时
/// 配置（setup），之后所有操作都通过界面按钮完成。
///
/// 与 Python CLI 保持一致的语义：
/// - `process`：导入并运行整条管线（OCR / 对齐 / 风险 / 复核 / 决策）
/// - `review`：列出尚需人工决定的配对
/// - `resolve`：记录一条人工决策
/// - `confirm`：标记空白页 / 非文本页
/// - `export`：写出 EPUB 3 或 DOCX
/// - `runtime-status` / `setup`：运行时与首次配置
class UnifiedWorkspace extends StatefulWidget {
  const UnifiedWorkspace({
    super.key,
    this.cliExecutor,
    this.filePicker,
    this.settingsStore,
    this.fileSystem,
  });

  final ScribeForgeCliExecutor? cliExecutor;
  final DesktopFilePicker? filePicker;
  final DesktopSettingsStore? settingsStore;
  final WorkspaceFileSystem? fileSystem;

  @override
  State<UnifiedWorkspace> createState() => _UnifiedWorkspaceState();
}

class _UseReviewCandidateIntent extends Intent {
  const _UseReviewCandidateIntent(this.candidateNumber);

  final int candidateNumber;
}

class _RecordReviewDecisionIntent extends Intent {
  const _RecordReviewDecisionIntent();
}

class _UnifiedWorkspaceState extends State<UnifiedWorkspace> {
  int _selectedIndex = 0;

  String? _pdfPath;
  String? _projectStorePath;
  bool _projectSourceMatches = true;
  bool _projectCanExport = true;
  String? _projectSourceMismatchMessage;

  late final DesktopSettingsStore _settingsStore;
  late final ScribeForgeCliExecutor _cliExecutor;
  late final DesktopFilePicker _filePicker;
  late final WorkspaceFileSystem _fileSystem;
  String _cliExecutable = ScribeForgeCli.defaultExecutable;
  DesktopVlmSettings _vlmSettings = const DesktopVlmSettings();
  bool _busy = false;
  bool _processing = false;
  bool _cancelRequested = false;
  String? _notice;
  String? _error;

  ProjectStatusSnapshot? _projectStatus;
  bool _statusLoading = false;
  int _statusRequestVersion = 0;
  Timer? _processStatusTimer;
  String? _sourceHashCachePath;
  int? _sourceHashCacheSize;
  int? _sourceHashCacheModifiedMillis;
  String? _sourceHashCacheValue;

  RuntimeStatusSnapshot? _runtimeStatus;
  bool _runtimeLoading = false;

  // 复核队列（来自 `scribeforge review --json` 的稳定契约）
  final List<ReviewQueueItem> _reviewQueue = [];
  bool _reviewLoading = false;
  bool _includePageFurnitureInReview = false;
  int _selectedReviewIndex = 0;
  final _reviewPageJumpController = TextEditingController();
  final _reviewPageJumpFocusNode = FocusNode(debugLabel: 'review-page-jump');
  final _reviewDecisionFocusNode = FocusNode(debugLabel: 'review-decision-input');

  // 导出表单控制器（需在 dispose 中释放）
  final _exportOutputController = TextEditingController();
  String? _exportOutputDirectory;
  final _exportLanguageController = TextEditingController(text: 'zh');
  final _exportTitleController = TextEditingController();
  final _exportAuthorController = TextEditingController();
  var _exportFormatEpub = true;
  var _exportLayoutChapter = true;
  var _exportStrictAcceptance = false;
  var _exportIncludePageFurniture = false;

  // 复核队列每条项的输入控制器（键：projectId-pageIndex-pairIndex）
  final Map<String, TextEditingController> _reviewControllers = {};

  TextEditingController _reviewControllerFor(ReviewQueueItem item) {
    final key = '${item.projectId}-${item.pageIndex}-${item.pairIndex}';
    return _reviewControllers.putIfAbsent(key, () => TextEditingController());
  }

  ReviewQueueItem? get _selectedReviewItem =>
      _selectedReviewIndex < 0 || _selectedReviewIndex >= _reviewQueue.length
          ? null
          : _reviewQueue[_selectedReviewIndex];

  static const List<String> _navTitles = <String>[
    '书籍',
    '处理',
    '复核',
    '候选比较',
    '导出',
    '项目安全',
    '设置',
  ];

  static const List<IconData> _navIcons = <IconData>[
    Icons.library_books,
    Icons.auto_awesome,
    Icons.fact_check_outlined,
    Icons.compare_arrows,
    Icons.output,
    Icons.shield_outlined,
    Icons.settings,
  ];

  @override
  void initState() {
    super.initState();
    _settingsStore = widget.settingsStore ?? DesktopSettingsStore();
    _cliExecutor = widget.cliExecutor ?? ProcessScribeForgeCliExecutor();
    _filePicker = widget.filePicker ?? const PluginDesktopFilePicker();
    _fileSystem = widget.fileSystem ?? const IoWorkspaceFileSystem();
    _loadSettings();
  }

  @override
  void dispose() {
    _processStatusTimer?.cancel();
    if (_processing) unawaited(_cliExecutor.cancel());
    _exportOutputController.dispose();
    _exportLanguageController.dispose();
    _exportTitleController.dispose();
    _exportAuthorController.dispose();
    _reviewPageJumpController.dispose();
    _reviewPageJumpFocusNode.dispose();
    _reviewDecisionFocusNode.dispose();
    for (final controller in _reviewControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _loadSettings() async {
    final executable = await _settingsStore.loadCliExecutable();
    final vlmSettings = await _settingsStore.loadVlmSettings();
    if (!mounted) return;
    setState(() {
      _cliExecutable = executable;
      _vlmSettings = vlmSettings;
    });
    await _refreshRuntimeStatus();
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  // ----------------------------------------------------------------- status

  Future<void> _refreshStatus({bool waitForInFlight = false}) async {
    if (_projectStorePath == null) return;
    if (_statusLoading) {
      if (!waitForInFlight) return;
      while (mounted && _statusLoading) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      if (!mounted) return;
    }
    final storePath = _projectStorePath;
    final sourcePath = _pdfPath;
    if (storePath == null) return;
    final version = ++_statusRequestVersion;
    setState(() => _statusLoading = true);
    try {
      final databasePath = ScribeForgeCli.joinPath(storePath, 'project.sqlite3');
      if (!await _fileSystem.fileExists(databasePath)) {
        if (!mounted || version != _statusRequestVersion) return;
        setState(() {
          _projectStatus = null;
          _projectSourceMatches = true;
          _projectCanExport = true;
          _projectSourceMismatchMessage = null;
          _error = null;
        });
        return;
      }
      final arguments = ScribeForgeCli.statusArguments(storePath: storePath);
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted || version != _statusRequestVersion) return;
      if (exitCode != 0) {
        setState(() => _error = buffer.toString().trim());
        return;
      }

      final projects = ProjectStatusSnapshot.listFromJsonText(buffer.toString());
      var selected = sourcePath == null
          ? null
          : ProjectStatusSnapshot.fromJsonText(
              buffer.toString(),
              sourcePath: sourcePath,
            );
      if (selected == null && projects.length == 1) selected = projects.single;

      var sourceMatches = false;
      var exportAllowed = false;
      var mismatchMessage = '此存储目录没有与所选源文件匹配的唯一项目。';
      if (selected != null && sourcePath != null) {
        final pathMatches = selected.sourcePathMatches(sourcePath);
        final sourceExists = await _fileSystem.fileExists(sourcePath);
        final sourceHash = pathMatches && sourceExists
            ? await _sourceSha256(sourcePath)
            : null;
        final hashMatches = sourceHash != null &&
            selected.sourceHashMatches(sourceHash);
        sourceMatches = projects.length == 1 && pathMatches && hashMatches;
        exportAllowed =
            projects.length == 1 && pathMatches && (!sourceExists || hashMatches);
        if (projects.length > 1) {
          mismatchMessage = '项目存储包含多个项目；请打开每本书各自的项目文件夹。';
        } else if (!pathMatches) {
          mismatchMessage = '所选源文件路径与项目记录不一致；请重新选择原文件或新建项目。';
        } else if (!sourceExists) {
          mismatchMessage = '找不到项目记录的源文件；请重新关联原文件。';
        } else if (!hashMatches) {
          mismatchMessage = '源文件内容与项目记录的 SHA-256 不一致，已阻止继续处理。';
        }
      }
      setState(() {
        _projectStatus = selected;
        _projectSourceMatches = projects.isEmpty || (selected != null && sourceMatches);
        _projectCanExport = projects.isEmpty || (selected != null && exportAllowed);
        _projectSourceMismatchMessage =
            projects.isEmpty || (selected != null && sourceMatches) ? null : mismatchMessage;
        if (selected != null && _exportTitleController.text.trim().isEmpty) {
          _exportTitleController.text = selected.title;
        }
        _error = null;
      });
    } catch (exc) {
      if (!mounted || version != _statusRequestVersion) return;
      setState(() => _error = '读取项目状态失败：$exc');
    } finally {
      if (mounted && version == _statusRequestVersion) {
        setState(() => _statusLoading = false);
      }
    }
  }

  void _clearSourceHashCache() {
    _sourceHashCachePath = null;
    _sourceHashCacheSize = null;
    _sourceHashCacheModifiedMillis = null;
    _sourceHashCacheValue = null;
  }

  Future<String?> _sourceSha256(String sourcePath) async {
    final stat = await _fileSystem.stat(sourcePath);
    if (stat == null) return null;
    final modifiedMillis = stat.modifiedMilliseconds;
    if (_sourceHashCachePath == sourcePath &&
        _sourceHashCacheSize == stat.size &&
        _sourceHashCacheModifiedMillis == modifiedMillis) {
      return _sourceHashCacheValue;
    }
    final digest = (await sha256.bind(_fileSystem.openRead(sourcePath)).first).toString();
    _sourceHashCachePath = sourcePath;
    _sourceHashCacheSize = stat.size;
    _sourceHashCacheModifiedMillis = modifiedMillis;
    _sourceHashCacheValue = digest;
    return digest;
  }

  // ---------------------------------------------------------------- runtime

  Future<void> _refreshRuntimeStatus() async {
    setState(() => _runtimeLoading = true);
    try {
      final arguments = ScribeForgeCli.runtimeStatusArguments();
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _runtimeStatus = RuntimeStatusSnapshot.fromJsonText(buffer.toString());
          _error = null;
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '读取运行时状态失败：$exc');
    } finally {
      if (mounted) setState(() => _runtimeLoading = false);
    }
  }

  // ------------------------------------------------------------- review queue

  Future<void> _refreshReviewQueue() async {
    final storePath = _projectStorePath;
    if (storePath == null) return;
    final databasePath = ScribeForgeCli.joinPath(storePath, 'project.sqlite3');
    final databaseExists = await _fileSystem.fileExists(databasePath);
    if (!mounted) return;
    if (!databaseExists) {
      setState(() {
        _reviewQueue.clear();
        _selectedReviewIndex = -1;
      });
      return;
    }
    setState(() => _reviewLoading = true);
    try {
      final arguments = ScribeForgeCli.reviewArguments(
        storePath: storePath,
        includePageFurniture: _includePageFurnitureInReview,
      );
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode == 0) {
        final snapshot = ReviewQueueSnapshot.fromJsonText(buffer.toString());
        final selectedItem = _selectedReviewItem;
        setState(() {
          _reviewQueue
            ..clear()
            ..addAll(snapshot.items);
          final retainedIndex = selectedItem == null
              ? -1
              : _reviewQueue.indexWhere((item) =>
                  item.projectId == selectedItem.projectId &&
                  item.pageIndex == selectedItem.pageIndex &&
                  item.pairIndex == selectedItem.pairIndex);
          _selectedReviewIndex = retainedIndex >= 0
              ? retainedIndex
              : ReviewQueueNavigation.move(
                  _selectedReviewIndex,
                  itemCount: _reviewQueue.length,
                  delta: 0,
                );
          _error = null;
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '读取复核队列失败：$exc');
    } finally {
      if (mounted) setState(() => _reviewLoading = false);
    }
  }

  // ---------------------------------------------------------------- actions

  Future<void> _pickPdf() async {
    final sourcePath = await _filePicker.pickFile(
      allowedExtensions: <String>['pdf', 'png', 'jpg', 'jpeg'],
    );
    if (!mounted || sourcePath == null) return;
    final storePath = ScribeForgeCli.defaultStorePath(sourcePath);
    final filename = File(sourcePath).uri.pathSegments.last;
    final extension = filename.lastIndexOf('.');
    final title = extension > 0 ? filename.substring(0, extension) : filename;
    _clearSourceHashCache();
    setState(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _pdfPath = sourcePath;
      _exportTitleController.text = title;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectStorePath = storePath;
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _projectStatus = null;
      _reviewQueue.clear();
      _selectedReviewIndex = -1;
      _error = null;
      _notice = null;
    });
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  Future<void> _openProject() async {
    final storePath = await _filePicker.getDirectoryPath(
      dialogTitle: '选择 ScribeForge 项目文件夹',
    );
    if (!mounted || storePath == null) return;
    final databasePath = ScribeForgeCli.joinPath(storePath, 'project.sqlite3');
    if (!await _fileSystem.fileExists(databasePath)) {
      setState(() => _error = '所选文件夹中没有 ScribeForge 项目数据库。');
      return;
    }
    _clearSourceHashCache();
    setState(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _pdfPath = null;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectStatus = null;
      _projectSourceMatches = false;
      _projectCanExport = false;
      _projectSourceMismatchMessage = null;
      _reviewQueue.clear();
      _selectedReviewIndex = -1;
      _error = null;
    });
    try {
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.statusArguments(storePath: storePath),
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode != 0) {
        setState(() => _error = buffer.toString().trim());
        return;
      }
      final projects = ProjectStatusSnapshot.listFromJsonText(buffer.toString());
      if (projects.length != 1) {
        setState(() => _error = projects.isEmpty
            ? '所选项目文件夹还没有导入书籍。'
            : '该文件夹包含多个项目；请打开每本书各自的项目文件夹。');
        return;
      }
      setState(() {
        _pdfPath = projects.single.sourceUri;
        _projectStatus = projects.single;
        _exportTitleController.text = projects.single.title;
        _selectedIndex = 0;
      });
      await _refreshStatus();
      await _refreshReviewQueue();
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '打开项目失败：$exc');
    }
  }

  Future<void> _chooseProjectStoreDirectory() async {
    final storePath = await _filePicker.getDirectoryPath(
      dialogTitle: '选择项目存储目录',
    );
    if (!mounted || storePath == null) return;
    setState(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _projectStatus = null;
      _exportOutputDirectory = null;
      _exportOutputController.clear();
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _reviewQueue.clear();
      _selectedReviewIndex = -1;
    });
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  Future<void> _relinkSource() async {
    final storePath = _projectStorePath;
    if (storePath == null) return;
    final sourcePath = await _filePicker.pickFile(
      dialogTitle: '重新关联原始 PDF 或页面图像',
      allowedExtensions: <String>['pdf', 'png', 'jpg', 'jpeg'],
    );
    if (!mounted || sourcePath == null) return;
    setState(() => _busy = true);
    try {
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.relinkSourceArguments(
          storePath: storePath,
          sourcePath: sourcePath,
        ),
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode == 0) {
        _clearSourceHashCache();
        setState(() {
          _pdfPath = sourcePath;
          _projectSourceMatches = true;
          _projectCanExport = true;
          _projectSourceMismatchMessage = null;
          _notice = '原始文件已重新关联；SHA-256 匹配，现有处理证据未更改。';
          _error = null;
        });
        await _refreshStatus();
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '重新关联源文件失败：$exc');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancelProcessing() async {
    if (!_processing || _cancelRequested) return;
    setState(() {
      _cancelRequested = true;
      _error = null;
      _notice = '正在请求取消处理…';
    });
    try {
      await _cliExecutor.cancel();
    } catch (exc) {
      if (!mounted) return;
      setState(() {
        _cancelRequested = false;
        _notice = null;
        _error = '发送取消请求失败：$exc';
      });
    }
  }

  Future<void> _runProcess() async {
    final sourcePath = _pdfPath;
    if (sourcePath == null || _statusLoading || !_projectSourceMatches) return;
    final storePath = _projectStorePath ??
        ScribeForgeCli.defaultStorePath(sourcePath);
    setState(() {
      _busy = true;
      _processing = true;
      _cancelRequested = false;
      _projectStorePath = storePath;
      _error = null;
      _notice = '处理中；正在读取页级任务状态…';
    });
    _processStatusTimer?.cancel();
    _processStatusTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _busy && !_statusLoading) unawaited(_refreshStatus());
    });
    unawaited(_refreshStatus());
    try {
      final arguments = ScribeForgeCli.processArguments(
        pdfPath: sourcePath,
        storePath: storePath,
        vlmSettings: _vlmSettings,
        cancelable: true,
      );
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: buffer.write,
      );
      if (!mounted) return;
      if (exitCode == 130) {
        await _refreshStatus(waitForInFlight: true);
        await _refreshReviewQueue();
        if (!mounted) return;
        setState(() {
          _error = null;
          _notice = '处理已取消；已保存的页面证据和人工决策保留，可重试失败任务。';
        });
      } else if (exitCode == 0 || exitCode == 1) {
        await _refreshStatus(waitForInFlight: true);
        await _refreshReviewQueue();
        setState(() {
          _notice = exitCode == 0
              ? '处理完成'
              : '处理完成（部分页面失败；请查看页级状态后重试）';
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '处理失败：$exc');
    } finally {
      _processStatusTimer?.cancel();
      _processStatusTimer = null;
      if (mounted) {
        setState(() {
          _busy = false;
          _processing = false;
          _cancelRequested = false;
        });
      }
    }
  }

  Future<void> _resolveReviewPair(ReviewQueueItem item, String acceptedText) async {
    if (_projectStorePath == null) return;
    setState(() => _busy = true);
    try {
      final arguments = ScribeForgeCli.resolveArguments(
        storePath: _projectStorePath!,
        projectId: item.projectId,
        page: item.pageIndex,
        pair: item.pairIndex,
        text: acceptedText,
      );
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _reviewQueue.removeWhere((r) =>
              r.projectId == item.projectId &&
              r.pageIndex == item.pageIndex &&
              r.pairIndex == item.pairIndex);
          _selectedReviewIndex = ReviewQueueNavigation.move(
            _selectedReviewIndex,
            itemCount: _reviewQueue.length,
            delta: 0,
          );
          _reviewControllers
              .remove('${item.projectId}-${item.pageIndex}-${item.pairIndex}')
              ?.dispose();
          _notice =
              '已记录第 ${item.pageIndex + 1} 页第 ${item.pairIndex + 1} 组的人工决策';
          _error = null;
        });
        await _refreshStatus(waitForInFlight: true);
        _focusReviewDecisionInputAfterFrame();
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '记录决策失败：$exc');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _runExport({
    required String outputPath,
    required String language,
    required String title,
    String? author,
    String? modified,
    bool layoutChapter = true,
    bool strictAcceptance = false,
    bool includePageFurniture = false,
  }) async {
    if (_projectStorePath == null || _projectStatus == null || !_projectCanExport) return;
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      final arguments = ScribeForgeCli.exportArguments(
        storePath: _projectStorePath!,
        outputPath: outputPath,
        title: title,
        language: language,
        author: author,
        modified: modified,
        includePageFurniture: includePageFurniture,
      );
      // 布局与严格校验：CLI 支持 --layout 与 --strict-acceptance，这里拼接。
      final allArgs = <String>[...arguments];
      allArgs.addAll(<String>['--layout', layoutChapter ? 'chapter' : 'page']);
      if (strictAcceptance) allArgs.add('--strict-acceptance');

      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: allArgs,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      if (!mounted) return;
      if (exitCode == 0) {
        setState(() {
          _notice = '导出成功：$outputPath';
        });
      } else {
        setState(() => _error = buffer.toString().trim());
      }
    } catch (exc) {
      if (!mounted) return;
      setState(() => _error = '导出失败：$exc');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _updateExportOutputPath() {
    final directory = _exportOutputDirectory;
    if (directory == null) return;
    var stem = _exportTitleController.text.trim();
    if (stem.isEmpty) stem = 'book';
    stem = stem.replaceAll(RegExp(r'[<>:"/\\|?*]'), '_');
    if (stem.isEmpty) stem = 'book';
    final extension = _exportFormatEpub ? 'epub' : 'docx';
    _exportOutputController.text =
        ScribeForgeCli.joinPath(directory, '$stem.$extension');
  }

  Future<void> _pickExportOutput() async {
    final directory = await _filePicker.getDirectoryPath(
      dialogTitle: '选择导出文件夹',
    );
    if (!mounted || directory == null) return;
    setState(() {
      _exportOutputDirectory = directory;
      _updateExportOutputPath();
    });
  }

  Future<void> _openVlmSettings() async {
    final settings = await showDialog<DesktopVlmSettings>(
      context: context,
      builder: (context) => VlmSettingsDialog(
        executable: _cliExecutable,
        initial: _vlmSettings,
        cliExecutor: _cliExecutor,
      ),
    );
    if (!mounted || settings == null) return;
    try {
      await _settingsStore.saveVlmSettings(settings);
      if (!mounted) return;
      setState(() {
        _vlmSettings = settings;
        _notice = settings.enabled
            ? 'VLM 已启用；处理时会按此配置发送复核裁切图。'
            : 'VLM 已关闭；处理不会发送复核裁切图。';
        _error = null;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _error = '保存 VLM 设置失败：$error');
    }
  }

  Future<void> _openRuntimeSetup() async {
    final attempted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => RuntimeSetupDialog(
        runtimeStatus: _runtimeStatus,
        runSetup: _executeSetup,
      ),
    );
    if (!mounted || attempted != true) return;
    await _refreshRuntimeStatus();
  }

  Future<int> _executeSetup({
    required String? backend,
    required void Function(RuntimeSetupMessage message) onMessage,
    required void Function(String diagnostic) onDiagnostic,
  }) async {
    var pending = '';

    void parseLine(String line) {
      if (line.trim().isEmpty) return;
      try {
        onMessage(RuntimeSetupMessage.fromJsonLine(line));
      } on FormatException catch (error) {
        onDiagnostic('无法解析运行时进度：$error');
      }
    }

    void receiveChunk(String chunk) {
      pending += chunk;
      var lineEnd = pending.indexOf('\n');
      while (lineEnd >= 0) {
        parseLine(pending.substring(0, lineEnd).replaceAll('\r', ''));
        pending = pending.substring(lineEnd + 1);
        lineEnd = pending.indexOf('\n');
      }
    }

    try {
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.setupArguments(backend: backend),
        onOutput: receiveChunk,
        onError: onDiagnostic,
      );
      if (pending.trim().isNotEmpty) parseLine(pending.trim());
      return exitCode;
    } on Object catch (error) {
      onDiagnostic('无法启动 ScribeForge 命令行工具：$error');
      rethrow;
    }
  }

  // ------------------------------------------------------------------ build

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_navTitles[_selectedIndex]),
        actions: <Widget>[
          if (_busy)
            const Padding(
              padding: EdgeInsets.all(8),
              child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            ),
          if (_error != null)
            IconButton(
              icon: const Icon(Icons.error_outline, color: Colors.amber),
              onPressed: () {
                setState(() => _error = null);
              },
            ),
        ],
      ),
      body: Column(
        children: <Widget>[
          _buildNoticeBar(),
          Expanded(
            child: Row(
              children: <Widget>[
                NavigationRail(
                  selectedIndex: _selectedIndex,
                  onDestinationSelected: (index) {
                    if (index != null) setState(() => _selectedIndex = index);
                  },
                  labelType: NavigationRailLabelType.all,
                  destinations: List.generate(
                    _navTitles.length,
                    (index) => NavigationRailDestination(
                      icon: Icon(_navIcons[index]),
                      selectedIcon: Icon(_navIcons[index], color: Theme.of(context).colorScheme.primary),
                      label: Text(_navTitles[index]),
                    ),
                  ),
                ),
                const VerticalDivider(thickness: 1, width: 1),
                Expanded(
                  child: IndexedStack(
                    index: _selectedIndex,
                    children: <Widget>[
                      _buildLibraryPage(),
                      _buildProcessPage(),
                      _buildReviewPage(),
                      _buildCandidateComparisonPage(),
                      _buildExportPage(),
                      _buildProjectSafetyPage(),
                      _buildSettingsPage(),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNoticeBar() {
    if (_notice == null && _error == null) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: _error == null ? Colors.green.shade50 : Colors.amber.shade50,
      child: Row(
        children: <Widget>[
          Icon(_error == null ? Icons.info : Icons.error_outline,
              color: _error == null ? Colors.green.shade700 : Colors.amber.shade700, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _error ?? _notice!,
              style: TextStyle(color: _error == null ? Colors.green.shade800 : Colors.amber.shade800, fontSize: 12),
            ),
          ),
          if (_error != null)
            IconButton(icon: const Icon(Icons.close, size: 16), onPressed: () => setState(() => _error = null)),
        ],
      ),
    );
  }

  // ============================================================ 书籍页面

  Widget _buildLibraryPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('已导入的书籍', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('导入新书，或打开已有项目继续处理、复核和导出。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 16),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: <Widget>[
              ElevatedButton.icon(
                icon: const Icon(Icons.add),
                label: const Text('导入书籍'),
                onPressed: _busy
                    ? null
                    : () async {
                        await _pickPdf();
                        if (mounted && _pdfPath != null) {
                          setState(() => _selectedIndex = 1);
                        }
                      },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.folder_open),
                label: const Text('打开项目文件夹'),
                onPressed: _busy ? null : _openProject,
              ),
            ],
          ),
          const SizedBox(height: 20),
          if (_statusLoading && _projectStatus == null)
            const LinearProgressIndicator()
          else if (_projectStatus == null)
            const Text('尚未选择书籍或打开项目。', style: TextStyle(color: Colors.grey))
          else
            _buildProjectStatusCard(),
        ],
      ),
    );
  }

  String _stageLabel(String stage) => switch (stage) {
        'import' => '导入页面',
        'preprocess' => '图像预处理',
        'primary_ocr' => 'OCR 引擎一',
        'secondary_ocr' => 'OCR 引擎二',
        'verify' => '比对与风险评估',
        'vlm_review' => '视觉模型复核',
        _ => stage,
      };

  Widget _buildProjectStatusCard() {
    final status = _projectStatus!;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(child: Text(status.title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600))),
                if (!_projectSourceMatches)
                  const Icon(Icons.warning, color: Colors.amber, size: 18),
              ],
            ),
            const SizedBox(height: 4),
            Text(status.sourceUri, style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
            const SizedBox(height: 12),
            LinearProgressIndicator(value: status.ocrProgress),
            const SizedBox(height: 6),
            Text(
              '${status.pagesWithBothOcrSuccess} / ${status.pagesTotal} 页已完成双引擎 OCR',
              style: const TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 8),
            Text(
              '任务：${status.succeededJobs} 已完成 · ${status.pendingJobs} 等待 · '
              '${status.runningJobs} 运行中 · ${status.failedJobs} 失败',
              style: const TextStyle(fontSize: 12),
            ),
            if (status.currentJob != null) ...[
              const SizedBox(height: 6),
              Text(
                '当前页面任务：${_stageLabel(status.currentJob!.stage)}'
                '${status.currentJob!.pageIndex == null ? '' : ' · 第 ${status.currentJob!.pageIndex! + 1} 页'}',
                style: TextStyle(color: Theme.of(context).colorScheme.primary, fontSize: 12),
              ),
            ],
            const SizedBox(height: 8),
            Text('复核队列：${status.reviewQueued} 项待处理', style: const TextStyle(fontSize: 12)),
            if (!_projectSourceMatches && _projectSourceMismatchMessage != null) ...[
              const SizedBox(height: 8),
              Text(
                _projectSourceMismatchMessage!,
                style: TextStyle(color: Colors.amber.shade800, fontSize: 12),
              ),
              TextButton.icon(
                onPressed: _busy ? null : _relinkSource,
                icon: const Icon(Icons.link, size: 16),
                label: const Text('重新关联源文件（需 SHA-256 相同）'),
              ),
            ],
            if (status.failedJobDetails.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Text('失败任务：', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
              ...status.failedJobDetails.map((failed) => Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(failed.description, style: TextStyle(color: Colors.red.shade700, fontSize: 11)),
              )),
            ],
          ],
        ),
      ),
    );
  }

  // ========================================================= 处理页面

  Widget _buildProcessPage() {
    final hasFailedJobs = (_projectStatus?.failedJobs ?? 0) > 0;
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('导入并处理书籍', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('选择扫描书籍的 PDF 或页面图片，运行可恢复的双引擎 OCR 与校对管线。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          Row(
            children: <Widget>[
              Expanded(
                child: InputDecorator(
                  decoration: const InputDecoration(
                    labelText: '源文件',
                    prefixIcon: Icon(Icons.description),
                  ),
                  child: Text(
                    _pdfPath ?? '尚未选择源文件',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              ElevatedButton.icon(
                icon: const Icon(Icons.folder_open),
                label: const Text('选择文件'),
                onPressed: _busy ? null : _pickPdf,
              ),
            ],
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              icon: Icon(
                _processing
                    ? (_cancelRequested ? Icons.hourglass_empty : Icons.cancel_outlined)
                    : (_busy ? Icons.hourglass_empty : Icons.play_arrow),
              ),
              label: Text(
                _processing
                    ? (_cancelRequested ? '正在取消…' : '取消处理')
                    : (_busy ? '处理中…' : (hasFailedJobs ? '重试失败页面' : '开始处理')),
              ),
              onPressed: _processing
                  ? (_cancelRequested ? null : _cancelProcessing)
                  : (_busy ||
                          _statusLoading ||
                          _pdfPath == null ||
                          !_projectSourceMatches
                      ? null
                      : _runProcess),
              style: ElevatedButton.styleFrom(
                minimumSize: const Size.fromHeight(48),
                backgroundColor: _processing ? Colors.red.shade700 : null,
                foregroundColor: _processing ? Colors.white : null,
              ),
            ),
          ),
          if (_busy) ...[
            const SizedBox(height: 12),
            const LinearProgressIndicator(),
            const SizedBox(height: 6),
            Text(
              _projectStatus?.currentJob == null
                  ? '正在导入页面或准备 OCR 运行时…'
                  : '当前任务：${_stageLabel(_projectStatus!.currentJob!.stage)}'
                    '${_projectStatus!.currentJob!.pageIndex == null ? '' : ' · 第 ${_projectStatus!.currentJob!.pageIndex! + 1} 页'}',
              style: TextStyle(color: Colors.grey.shade700, fontSize: 12),
            ),
          ],
          if (_pdfPath != null) ...[
            const SizedBox(height: 16),
            const Text('项目存储目录：', style: TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 4),
            SelectableText(
              _projectStorePath ?? ScribeForgeCli.defaultStorePath(_pdfPath!),
              style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _busy ? null : _chooseProjectStoreDirectory,
                icon: const Icon(Icons.drive_file_move_outline, size: 16),
                label: const Text('更改项目存储目录'),
              ),
            ),
          ],
          if (_projectStatus != null) ...[
            const SizedBox(height: 16),
            _buildProjectStatusCard(),
          ],
        ],
      ),
    );
  }

  // ========================================================= 复核页面

  Widget _buildReviewPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('复核队列', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text(
            '比较 OCR 与视觉模型留下的原始候选和图像证据。只有你明确记录后，人工决策才会写入追加式证据链。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
          const SizedBox(height: 4),
          Text(
            '快捷键：Alt+←/→ 上一项/下一项；Ctrl+J 聚焦决策输入；Ctrl+G 聚焦跳页；Alt+1/2/3 填入候选；Ctrl+Enter 明确记录。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
          ),
          const SizedBox(height: 16),
          Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: ScribeSpacing.x3,
            runSpacing: ScribeSpacing.x2,
            children: <Widget>[
              ElevatedButton.icon(
                icon: Icon(_reviewLoading ? Icons.hourglass_empty : Icons.refresh),
                label: Text(_reviewLoading ? '刷新中…' : '刷新队列'),
                onPressed: _reviewLoading || _projectStorePath == null || _projectStatus == null
                    ? null
                    : _refreshReviewQueue,
              ),
              FilterChip(
                key: const ValueKey('review-include-page-furniture'),
                avatar: Icon(
                  _includePageFurnitureInReview
                      ? Icons.visibility
                      : Icons.visibility_outlined,
                  size: 18,
                ),
                label: const Text('显示页眉 / 页脚 / 页码'),
                selected: _includePageFurnitureInReview,
                onSelected: _reviewLoading
                    ? null
                    : (include) {
                        setState(() => _includePageFurnitureInReview = include);
                        unawaited(_refreshReviewQueue());
                      },
              ),
              Text(
                '${_reviewQueue.length} 项当前队列',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
          const SizedBox(height: ScribeSpacing.x2),
          Text(
            '默认隐藏已识别的页眉、页脚与页码；开启后可查看 OCR 证据及分类依据，决策仍需你明确确认。',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
          ),
          const SizedBox(height: 12),
          if (_reviewLoading)
            const Expanded(child: Center(child: CircularProgressIndicator()))
          else if (_projectStorePath == null)
            const Expanded(child: Center(child: Text('请先导入书籍或打开一个项目。')))
          else if (_reviewQueue.isEmpty)
            const Expanded(child: Center(child: Text('暂无待处理配对。')))
          else
            Expanded(
              child: ReviewKeyboardShortcuts(
                onPreviousPage: () => _moveReviewSelection(-1),
                onNextPage: () => _moveReviewSelection(1),
                onNextDifference: () => _moveReviewSelection(1),
                onFocusDecisionInput: _focusReviewDecisionInput,
                onFocusPageJump: () => _reviewPageJumpFocusNode.requestFocus(),
                child: Column(
                  children: <Widget>[
                    _buildReviewNavigationControls(),
                    const SizedBox(height: 8),
                    Expanded(
                      child: SingleChildScrollView(
                        child: _buildReviewQueueItem(
                          _reviewQueue[_selectedReviewIndex],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildReviewNavigationControls() {
    final hasPrevious = _selectedReviewIndex > 0;
    final hasNext = _selectedReviewIndex >= 0 &&
        _selectedReviewIndex < _reviewQueue.length - 1;
    return Row(
      children: <Widget>[
        IconButton(
          tooltip: '上一项',
          onPressed: hasPrevious ? () => _moveReviewSelection(-1) : null,
          icon: const Icon(Icons.chevron_left),
        ),
        Text('${_selectedReviewIndex + 1} / ${_reviewQueue.length}'),
        IconButton(
          tooltip: '下一项',
          onPressed: hasNext ? () => _moveReviewSelection(1) : null,
          icon: const Icon(Icons.chevron_right),
        ),
        const SizedBox(width: 12),
        SizedBox(
          width: 128,
          child: TextField(
            key: const ValueKey('review-page-jump'),
            controller: _reviewPageJumpController,
            focusNode: _reviewPageJumpFocusNode,
            keyboardType: TextInputType.number,
            textInputAction: TextInputAction.go,
            onSubmitted: _jumpToPage,
            decoration: const InputDecoration(
              labelText: '跳转页码',
              hintText: '例如 12',
              isDense: true,
            ),
          ),
        ),
        const SizedBox(width: 8),
        OutlinedButton(
          onPressed: () => _jumpToPage(_reviewPageJumpController.text),
          child: const Text('跳转'),
        ),
      ],
    );
  }

  void _moveReviewSelection(int delta) {
    final nextIndex = ReviewQueueNavigation.move(
      _selectedReviewIndex,
      itemCount: _reviewQueue.length,
      delta: delta,
    );
    if (nextIndex < 0 || nextIndex == _selectedReviewIndex) return;
    setState(() => _selectedReviewIndex = nextIndex);
    _focusReviewDecisionInputAfterFrame();
  }

  void _jumpToPage(String rawPageNumber) {
    final pageNumber = int.tryParse(rawPageNumber.trim());
    final nextIndex = pageNumber == null
        ? null
        : ReviewQueueNavigation.indexForPage(
            _reviewQueue.map((item) => item.pageIndex + 1).toList(),
            pageNumber: pageNumber,
          );
    if (nextIndex == null) {
      setState(() => _error = '找不到第 ${rawPageNumber.trim()} 页的待复核配对。');
      return;
    }
    setState(() {
      _selectedReviewIndex = nextIndex;
      _error = null;
    });
    _focusReviewDecisionInputAfterFrame();
  }

  void _focusReviewDecisionInput() => _reviewDecisionFocusNode.requestFocus();

  void _focusReviewDecisionInputAfterFrame() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _selectedReviewItem != null) {
        _reviewDecisionFocusNode.requestFocus();
      }
    });
  }

  String _reasonLabel(String reason) => switch (reason) {
        'text_disagreement' => '文字不一致',
        'variant_disagreement' => '异体字差异',
        'punctuation_disagreement' => '标点差异',
        'missing_or_extra_line' => '缺行或多行',
        'low_confidence' => '置信度偏低',
        'unicode_anomaly' => '字符异常',
        'spatial_mismatch' => '位置不一致',
        _ => reason,
      };

  String _furnitureReasonLabel(String reason) => switch (reason) {
        'engine_block' => 'OCR 布局标记',
        'repeated_in_margin' => '重复页边行',
        'page_number' => '连续页码',
        _ => reason,
      };

  Widget _buildReviewEvidence(String label, String? text) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
          const SizedBox(height: 2),
          SelectableText(text == null || text.isEmpty ? '（无文本）' : text),
        ],
      ),
    );
  }

  Widget _buildReviewImage(String label, String imagePath) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.file(
              File(imagePath),
              width: double.infinity,
              height: 150,
              fit: BoxFit.contain,
              errorBuilder: (context, error, stackTrace) => Container(
                height: 90,
                alignment: Alignment.center,
                color: Colors.black12,
                child: const Text('无法载入图像；文字证据仍可复核。'),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildReviewQueueItem(ReviewQueueItem item) {
    final controller = _reviewControllerFor(item);
    return Shortcuts(
      shortcuts: <ShortcutActivator, Intent>{
        const SingleActivator(LogicalKeyboardKey.digit1, alt: true):
            const _UseReviewCandidateIntent(1),
        const SingleActivator(LogicalKeyboardKey.digit2, alt: true):
            const _UseReviewCandidateIntent(2),
        const SingleActivator(LogicalKeyboardKey.digit3, alt: true):
            const _UseReviewCandidateIntent(3),
        const SingleActivator(LogicalKeyboardKey.enter, control: true):
            const _RecordReviewDecisionIntent(),
      },
      child: Actions(
        actions: <Type, Action<Intent>>{
          _UseReviewCandidateIntent: CallbackAction<_UseReviewCandidateIntent>(
            onInvoke: (intent) {
              final candidate = switch (intent.candidateNumber) {
                1 => item.primaryText,
                2 => item.secondaryText,
                3 => item.vlmText,
                _ => null,
              };
              if (candidate != null) {
                controller.text = candidate;
                controller.selection =
                    TextSelection.collapsed(offset: candidate.length);
              }
              return null;
            },
          ),
          _RecordReviewDecisionIntent:
              CallbackAction<_RecordReviewDecisionIntent>(
            onInvoke: (_) {
              if (!_busy) {
                unawaited(_resolveReviewPair(item, controller.text));
              }
              return null;
            },
          ),
        },
        child: Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: 8,
              runSpacing: 4,
              children: <Widget>[
                Text(
                  '第 ${item.pageIndex + 1} 页 · 第 ${item.pairIndex + 1} 组',
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                ),
                Chip(
                  label: Text('风险 ${(item.score * 100).round()}%'),
                  visualDensity: VisualDensity.compact,
                ),
                ...item.reasons.map((reason) => Chip(
                      label: Text(_reasonLabel(reason)),
                      visualDensity: VisualDensity.compact,
                    )),
                if (item.furnitureReason != null)
                  Chip(
                    avatar: const Icon(Icons.auto_stories_outlined, size: 16),
                    label: Text(
                      '页边家具 · ${_furnitureReasonLabel(item.furnitureReason!)}',
                    ),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            if (_projectStatus == null || _projectStatus!.projectId != item.projectId)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('${item.projectTitle} · 项目 ${item.projectId}',
                    style: TextStyle(color: Colors.grey.shade600, fontSize: 11)),
              ),
            const Divider(height: 12),
            if (item.furnitureReason != null)
              _buildReviewEvidence(
                '页边家具分类依据',
                _furnitureReasonLabel(item.furnitureReason!),
              ),
            _buildReviewEvidence('OCR 引擎一候选', item.primaryText),
            _buildReviewEvidence('OCR 引擎二候选', item.secondaryText),
            if (item.vlmText != null) ...[
              _buildReviewEvidence('VLM 候选（仅供参考）', item.vlmText),
              if (item.vlmUncertain)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text('视觉模型标记为不确定；请勿将其视为自动结论。',
                      style: TextStyle(color: Colors.orange.shade800, fontSize: 12)),
                ),
            ],
            const SizedBox(height: 4),
            if (item.cropPath != null)
              _buildReviewImage('候选区域裁切', item.cropPath!),
            _buildReviewImage('原始页面图像', item.sourceImagePath),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('review-decision-input'),
              controller: controller,
              focusNode: _reviewDecisionFocusNode,
              minLines: 1,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '人工确认的文字',
                hintText: '比较上述证据后，输入你确认的文字',
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: <Widget>[
                if (item.primaryText != null)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.content_copy, size: 16),
                    label: const Text('填入 OCR 1'),
                    onPressed: _busy ? null : () => controller.text = item.primaryText!,
                  ),
                if (item.secondaryText != null)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.content_copy, size: 16),
                    label: const Text('填入 OCR 2'),
                    onPressed: _busy ? null : () => controller.text = item.secondaryText!,
                  ),
                if (item.vlmText != null)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.content_copy, size: 16),
                    label: const Text('填入 VLM 候选'),
                    onPressed: _busy ? null : () => controller.text = item.vlmText!,
                  ),
                ElevatedButton.icon(
                  icon: const Icon(Icons.fact_check_outlined),
                  label: const Text('记录人工决策'),
                  onPressed: _busy ? null : () => _resolveReviewPair(item, controller.text),
                ),
              ],
            ),
          ],
        ),
          ),
        ),
      ),
    );
  }

  // ======================================================= 候选比较页面

  Widget _buildCandidateComparisonPage() {
    final status = _projectStatus;
    final canGenerateCandidates = !_busy &&
        !_statusLoading &&
        _projectStorePath != null &&
        status != null &&
        _projectSourceMatches &&
        _projectCanExport &&
        status.pagesTotal > 0 &&
        status.runningJobs == 0 &&
        status.pendingJobs == 0 &&
        status.pagesWithBothOcrSuccess == status.pagesTotal;
    return CandidateComparisonScreen(
      sourcePdfPath: _pdfPath,
      storePath: _projectStorePath,
      cliExecutable: _cliExecutable,
      canGenerateCandidates: canGenerateCandidates,
      cliExecutor: _cliExecutor,
      filePicker: _filePicker,
      comparisonStore: const FileCandidateComparisonStore(),
      sourcePageCount: status?.pagesTotal,
      expectedSourceSha256: status?.sourceSha256,
      onOpenTrustedReview: () {
        setState(() => _selectedIndex = 2);
        unawaited(_refreshReviewQueue());
      },
    );
  }

  // ========================================================= 导出页面

  Widget _buildExportPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('导出成品', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('把已验证的文字写出为 EPUB 3 或 DOCX。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          _buildExportForm(),
        ],
      ),
    );
  }

  Widget _buildExportForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        DropdownButtonFormField<String>(
          initialValue: _exportFormatEpub ? 'epub' : 'docx',
          items: const <DropdownMenuItem<String>>[
            DropdownMenuItem(value: 'epub', child: Text('EPUB 3')),
            DropdownMenuItem(value: 'docx', child: Text('DOCX')),
          ],
          onChanged: (value) => setState(() {
            _exportFormatEpub = value == 'epub';
            _updateExportOutputPath();
          }),
          decoration: const InputDecoration(labelText: '导出格式'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportOutputController,
          decoration: const InputDecoration(
            labelText: '输出文件',
            hintText: '选择导出文件夹，文件名按书名和格式生成',
            suffixIcon: Icon(Icons.folder_open_outlined),
          ),
          readOnly: true,
          onTap: _pickExportOutput,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportLanguageController,
          decoration: const InputDecoration(labelText: '语言（BCP 47，如 zh）'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportTitleController,
          decoration: const InputDecoration(labelText: '书名（可选）'),
          onChanged: (_) => setState(_updateExportOutputPath),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _exportAuthorController,
          decoration: const InputDecoration(labelText: '作者（可选）'),
        ),
        const SizedBox(height: 12),
        CheckboxListTile(
          value: _exportLayoutChapter,
          title: const Text('按章节布局（EPUB）'),
          subtitle: const Text('关闭后每页一个文档（便于核对扫描）'),
          onChanged: (v) => setState(() => _exportLayoutChapter = v ?? true),
        ),
        CheckboxListTile(
          value: _exportStrictAcceptance,
          title: const Text('严格接受校验'),
          subtitle: const Text('有待抽查的配对时拒绝导出（退出码 2）'),
          onChanged: (v) => setState(() => _exportStrictAcceptance = v ?? false),
        ),
        CheckboxListTile(
          value: _exportIncludePageFurniture,
          title: const Text('保留页眉、页脚与页码'),
          subtitle: const Text('包含已识别的页边行；未裁决分歧仍会阻止可信导出。'),
          onChanged: (v) =>
              setState(() => _exportIncludePageFurniture = v ?? false),
        ),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          height: 48,
          child: ElevatedButton.icon(
            icon: Icon(_busy ? Icons.hourglass_empty : Icons.output),
            label: Text(_busy ? '导出中…' : '开始导出'),
            onPressed: _busy ||
                    _projectStorePath == null ||
                    _projectStatus == null ||
                    !_projectCanExport ||
                    _exportOutputController.text.isEmpty
                ? null
                : () => _runExport(
                      outputPath: _exportOutputController.text,
                      language: _exportLanguageController.text,
                      title: _exportTitleController.text,
                      author: _exportAuthorController.text,
                      layoutChapter: _exportLayoutChapter,
                      strictAcceptance: _exportStrictAcceptance,
                      includePageFurniture: _exportIncludePageFurniture,
                    ),
          ),
        ),
      ],
    );
  }

  // ========================================================= 项目安全页面

  Widget _buildProjectSafetyPage() {
    var title = _projectStatus?.title ?? '';
    if (title.isEmpty && _pdfPath != null) {
      final filename = File(_pdfPath!).uri.pathSegments.last;
      final extension = filename.lastIndexOf('.');
      title = extension > 0 ? filename.substring(0, extension) : filename;
    }
    return ProjectSafetyScreen(
      storePath: _projectStorePath,
      projectTitle: title,
      cliExecutable: _cliExecutable,
      cliExecutor: _cliExecutor,
      filePicker: _filePicker,
      fileSystem: _fileSystem,
      onProjectRestored: _handleProjectRestored,
    );
  }

  Future<void> _handleProjectRestored(String storePath) async {
    _clearSourceHashCache();
    setState(() {
      _statusRequestVersion++;
      _statusLoading = false;
      _projectStorePath = storePath;
      _pdfPath = null;
      _projectStatus = null;
      _projectSourceMatches = true;
      _projectCanExport = true;
      _projectSourceMismatchMessage = null;
      _exportTitleController.clear();
      _exportOutputController.clear();
      _exportOutputDirectory = null;
      _reviewQueue.clear();
      _selectedReviewIndex = -1;
      _selectedIndex = 0;
      _error = null;
      _notice = '项目已恢复并载入工作台。';
    });
    await _refreshStatus();
    if (!mounted) return;
    final restoredSourcePath = _projectStatus?.sourceUri;
    if (restoredSourcePath != null && restoredSourcePath.isNotEmpty) {
      setState(() => _pdfPath = restoredSourcePath);
      await _refreshStatus();
    }
    await _refreshReviewQueue();
  }

  // ========================================================= 设置页面

  Widget _buildSettingsPage() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('设置', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text('配置命令行工具与运行时。首次使用请先运行运行时配置。',
              style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
          const SizedBox(height: 20),
          ListTile(
            title: const Text('命令行工具'),
            subtitle: Text(_cliExecutable),
            trailing: IconButton(
              icon: const Icon(Icons.edit),
              onPressed: () async {
                final value = await _promptString('命令行工具路径', _cliExecutable);
                if (value != null && value.trim().isNotEmpty) {
                  await _settingsStore.saveCliExecutable(value.trim());
                  setState(() => _cliExecutable = value.trim());
                  await _refreshRuntimeStatus();
                  await _refreshStatus();
                  await _refreshReviewQueue();
                }
              },
            ),
          ),
          ListTile(
            title: const Text('视觉模型复核'),
            subtitle: Text(
              _vlmSettings.enabled
                  ? '已启用：处理时会按配置发送复核裁切图。'
                  : '默认关闭；未明确启用时不会发送复核裁切图。',
            ),
            trailing: TextButton(
              onPressed: _busy ? null : _openVlmSettings,
              child: const Text('配置 VLM 复核'),
            ),
          ),
          const Divider(),
          ListTile(
            title: const Text('运行时配置 (setup)'),
            subtitle: const Text('查看后端、阶段与步骤进度，或修复单个后端'),
            trailing: _runtimeLoading
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow),
            onTap: _runtimeLoading ? null : _openRuntimeSetup,
          ),
          if (_runtimeStatus != null) ...[
            const Divider(),
            const Text('运行时状态', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('根目录：${_runtimeStatus!.runtimeRoot}'),
            Text('存在：${_runtimeStatus!.runtimeRootExists}'),
            Text('uv 可用：${_runtimeStatus!.uvAvailable}'),
            const SizedBox(height: 8),
            Text('后端：', style: const TextStyle(fontWeight: FontWeight.w600)),
            ..._runtimeStatus!.backends.entries.map((entry) => Padding(
              padding: const EdgeInsets.only(left: 16, top: 4),
              child: Text('${entry.key}: ${entry.value.state} ${entry.value.environmentExists ? '' : '(环境未安装)'}',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
            )),
          ],
          const Divider(),
          ListTile(
            title: const Text('刷新运行时状态'),
            trailing: const Icon(Icons.refresh),
            onTap: () { _refreshRuntimeStatus(); },
          ),
        ],
      ),
    );
  }

  /// 简单的文本输入对话框。
  Future<String?> _promptString(String title, String initialValue) async {
    final controller = TextEditingController(text: initialValue);
    return showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: TextField(controller: controller, autofocus: true),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('确定')),
        ],
      ),
    );
  }
}
