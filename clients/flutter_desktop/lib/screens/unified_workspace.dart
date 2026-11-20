import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';

import '../models/processing_report.dart';
import '../models/project_status.dart';
import '../models/review_queue.dart';
import '../models/review_queue_navigation.dart';
import '../models/review_triage.dart';
import '../models/runtime_location.dart';
import '../models/runtime_setup.dart';
import '../models/runtime_status.dart';
import '../services/backend_diagnostics.dart';
import '../services/backend_environment.dart';
import '../services/backend_resolver.dart';
import '../services/candidate_comparison_store.dart';
import '../services/cli_executor.dart';
import '../services/cli_path_validator.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';
import '../services/processing_report_loader.dart';
import '../services/python_locator.dart';
import '../services/runtime_location_service.dart';
import '../services/settings_store.dart';
import '../services/workspace_file_system.dart';
import '../widgets/workspace_help_dialog.dart';
import '../widgets/workspace_shell.dart';
import 'candidate_comparison_screen.dart';
import 'processing_report_sheet.dart';
import 'project_safety_screen.dart';
import 'review/review_workspace_page.dart';
import 'runtime_location_dialog.dart';
import 'runtime_setup_dialog.dart';
import 'vlm_settings_dialog.dart';

part 'unified_workspace_settings.dart';

/// 主窗口"下一步"引导条可采取的操作。
enum NextStepAction {
  none,
  configureBackend,
  initEnvironment,
  importBook,
  resumeLastProject,
  relinkSource,
  goProcess,
  goReview,
  goExport,
}

/// 一条"下一步"建议：一句中文说明 + 一个可点击操作。
class NextStepGuidance {
  const NextStepGuidance(this.action, this.message, {this.count});

  final NextStepAction action;
  final String message;
  final int? count;
}

/// 由原始状态计算唯一的下一步建议（纯函数，便于穷举单测）。
NextStepGuidance nextStepGuidance({
  required bool backendChecked,
  required bool backendLaunchFailed,
  required bool runtimeSetupRequired,
  required bool hasProject,
  required bool hasSource,
  required bool sourceMismatch,
  required int pagesWithBothOcr,
  required int pagesTotal,
  required int reviewQueued,
  String? lastProjectStore,
}) {
  if (!backendChecked) {
    return const NextStepGuidance(NextStepAction.none, '');
  }
  if (backendLaunchFailed) {
    return const NextStepGuidance(
      NextStepAction.configureBackend,
      '命令行工具无法启动，请到「设置」检查命令行工具路径。',
    );
  }
  if (runtimeSetupRequired) {
    return const NextStepGuidance(
      NextStepAction.initEnvironment,
      '首次使用：请先初始化 OCR 环境，完成后即可导入并处理书籍。',
    );
  }
  if (!hasProject && !hasSource && lastProjectStore != null) {
    return NextStepGuidance(
      NextStepAction.resumeLastProject,
      '上次的项目还在：$lastProjectStore，一键继续。',
    );
  }
  if (!hasProject && !hasSource) {
    return const NextStepGuidance(
      NextStepAction.importBook,
      '导入一本 PDF 或图片书籍开始，或打开已有项目继续。',
    );
  }
  if (sourceMismatch) {
    return const NextStepGuidance(
      NextStepAction.relinkSource,
      '源文件与项目记录不匹配，请重新关联源文件（需 SHA-256 相同）。',
    );
  }
  if (hasProject && pagesWithBothOcr < pagesTotal) {
    return const NextStepGuidance(
      NextStepAction.goProcess,
      '还有页面未完成双引擎 OCR，去处理。',
    );
  }
  if (!hasProject && hasSource) {
    return const NextStepGuidance(NextStepAction.goProcess, '已选择源文件，去处理页面。');
  }
  if (reviewQueued > 0) {
    return NextStepGuidance(
      NextStepAction.goReview,
      '复核队列还有 $reviewQueued 项待处理，去复核。',
      count: reviewQueued,
    );
  }
  return const NextStepGuidance(
    NextStepAction.goExport,
    '所有页面已完成复核，可以导出 EPUB / DOCX 了。',
  );
}

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
    this.backendResolver,
    this.backendProvisioner,
    this.pythonLocator,
    this.cliPathValidator,
    this.reportLoader,
  });

  final ScribeForgeCliExecutor? cliExecutor;
  final DesktopBackendResolver? backendResolver;
  final CliPathValidator? cliPathValidator;
  final ProcessingReportLoader? reportLoader;
  final DesktopFilePicker? filePicker;
  final DesktopSettingsStore? settingsStore;
  final WorkspaceFileSystem? fileSystem;
  final BackendEnvironmentProvisioner? backendProvisioner;
  final PythonLocator? pythonLocator;

  @override
  State<UnifiedWorkspace> createState() => _UnifiedWorkspaceState();
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
  late final ScribeForgeCliExecutor _rawCliExecutor;
  RuntimeLocationPreferences _runtimeLocations =
      const RuntimeLocationPreferences();
  late final DesktopBackendResolver _backendResolver;
  late final DesktopFilePicker _filePicker;
  late final WorkspaceFileSystem _fileSystem;
  late final BackendEnvironmentProvisioner _provisioner;
  late final PythonLocator _pythonLocator;
  late final CliPathValidator _cliPathValidator;
  late final ProcessingReportLoader _reportLoader;
  String _cliExecutable = ScribeForgeCli.defaultExecutable;
  String _backendPython = '';
  String _backendSourcePath = '';
  bool _backendBusy = false;
  DesktopBackendResolution? _backendResolution;
  bool _backendConnected = false;
  bool _runtimeChecked = false;
  String? _lastProjectStorePath;
  Object? _backendLaunchError;
  bool _runtimeSetupActionRequired = false;
  DesktopVlmSettings _vlmSettings = const DesktopVlmSettings();
  bool _busy = false;
  bool _processing = false;
  bool _cancelRequested = false;
  String? _notice;
  String? _error;
  bool _cliProbing = false;
  String? _cliProbeMessage;
  SettingsCorruption? _settingsCorruption;

  ProjectStatusSnapshot? _projectStatus;
  bool _statusLoading = false;
  int _statusRequestVersion = 0;
  Timer? _processStatusTimer;
  String? _sourceHashCachePath;
  int? _sourceHashCacheSize;
  int? _sourceHashCacheModifiedMillis;
  String? _sourceHashCacheValue;

  RuntimeStatusSnapshot? _runtimeStatus;
  bool _runtimeLoading = true;
  bool _runtimeCleaning = false;

  // 复核队列（来自 `scribeforge review --json` 的稳定契约）
  final List<ReviewQueueItem> _reviewQueue = [];
  bool _reviewLoading = false;
  int _reviewRequestVersion = 0;
  bool _includePageFurnitureInReview = false;
  int _selectedReviewIndex = 0;
  final _reviewDecisionFocusNode = FocusNode(
    debugLabel: 'review-decision-input',
  );

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

  final Map<ReviewItemKey, TextEditingController> _reviewControllers = {};

  TextEditingController _reviewControllerFor(ReviewQueueItem item) =>
      _reviewControllers.putIfAbsent(
        reviewItemKey(item),
        TextEditingController.new,
      );

  void _disposeDraftsAfterFrame(Iterable<TextEditingController> controllers) {
    final detached = controllers.toList();
    if (detached.isEmpty) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      for (final controller in detached) {
        controller.dispose();
      }
    });
  }

  void _resetReviewForProject() {
    _reviewRequestVersion++;
    _reviewLoading = false;
    _reviewQueue.clear();
    _selectedReviewIndex = -1;
    _disposeDraftsAfterFrame(_reviewControllers.values);
    _reviewControllers.clear();
  }

  ReviewQueueItem? get _selectedReviewItem =>
      _selectedReviewIndex < 0 || _selectedReviewIndex >= _reviewQueue.length
      ? null
      : _reviewQueue[_selectedReviewIndex];

  static const _destinations = <WorkspaceDestination>[
    WorkspaceDestination('书籍', Icons.library_books),
    WorkspaceDestination('处理', Icons.auto_awesome),
    WorkspaceDestination('复核', Icons.fact_check_outlined),
    WorkspaceDestination('候选比较', Icons.compare_arrows),
    WorkspaceDestination('导出', Icons.output),
    WorkspaceDestination('项目安全', Icons.shield_outlined),
    WorkspaceDestination('设置', Icons.settings),
  ];

  @override
  void initState() {
    super.initState();
    _settingsStore = widget.settingsStore ?? DesktopSettingsStore();
    _rawCliExecutor = widget.cliExecutor ?? ProcessScribeForgeCliExecutor();
    _cliExecutor = RuntimeLocationExecutor(
      _rawCliExecutor,
      () => _runtimeLocations.active,
    );
    _backendResolver = widget.backendResolver ?? const DesktopBackendResolver();
    _filePicker = widget.filePicker ?? const PluginDesktopFilePicker();
    _fileSystem = widget.fileSystem ?? const IoWorkspaceFileSystem();
    _provisioner =
        widget.backendProvisioner ?? const BackendEnvironmentProvisioner();
    _pythonLocator =
        widget.pythonLocator ??
        PythonLocator(environment: Platform.environment);
    _cliPathValidator = widget.cliPathValidator ?? const CliPathValidator();
    _reportLoader = widget.reportLoader ?? const ProcessingReportLoader();
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
    _reviewDecisionFocusNode.dispose();
    for (final controller in _reviewControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _loadSettings() async {
    final executable = await _settingsStore.loadCliExecutable();
    final vlmSettings = await _settingsStore.loadVlmSettings();
    final locations = await _settingsStore.loadRuntimeLocations();
    final backendPython = await _settingsStore.loadBackendPython();
    final backendSourcePath = await _settingsStore.loadBackendSourcePath();
    final lastProjectStore = await _settingsStore.loadLastProjectStore();
    final venvCliPath = BackendVenvPaths(this._backendVenvDirectory)
        .cliExecutable;
    final resolution = await _backendResolver.resolve(
      executable,
      venvCliPath: venvCliPath,
    );
    if (!mounted) return;
    final corruption = _settingsStore.pendingCorruption;
    setState(() {
      _cliExecutable = resolution.executable;
      _backendResolution = resolution;
      _vlmSettings = vlmSettings;
      _runtimeLocations = locations;
      _backendPython = backendPython;
      _backendSourcePath = backendSourcePath;
      _lastProjectStorePath = lastProjectStore;
      if (corruption != null) {
        _settingsCorruption = corruption;
        _notice = corruption.message;
      }
    });
    await _refreshRuntimeStatus();
    await _refreshStatus();
    await _refreshReviewQueue();
    if (!mounted || !_backendConnected || _ocrRuntimeReady) return;
    setState(() {
      _selectedIndex = 6;
      _notice = '首次使用请先初始化 OCR 环境；安装完成后即可导入并处理 PDF。';
    });
  }

  bool get _ocrRuntimeReady {
    final status = _runtimeStatus;
    if (status == null) return false;
    return const <String>['mineru', 'paddleocr'].every((name) {
      final backend = status.backends[name];
      return backend != null &&
          backend.state == 'ready' &&
          backend.environmentExists;
    });
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
      final databasePath = ScribeForgeCli.joinPath(
        storePath,
        'project.sqlite3',
      );
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

      final projects = ProjectStatusSnapshot.listFromJsonText(
        buffer.toString(),
      );
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
        final hashMatches =
            sourceHash != null && selected.sourceHashMatches(sourceHash);
        sourceMatches = projects.length == 1 && pathMatches && hashMatches;
        exportAllowed =
            projects.length == 1 &&
            pathMatches &&
            (!sourceExists || hashMatches);
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
        _projectSourceMatches =
            projects.isEmpty || (selected != null && sourceMatches);
        _projectCanExport =
            projects.isEmpty || (selected != null && exportAllowed);
        _projectSourceMismatchMessage =
            projects.isEmpty || (selected != null && sourceMatches)
            ? null
            : mismatchMessage;
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
    final digest = (await sha256.bind(_fileSystem.openRead(sourcePath)).first)
        .toString();
    _sourceHashCachePath = sourcePath;
    _sourceHashCacheSize = stat.size;
    _sourceHashCacheModifiedMillis = modifiedMillis;
    _sourceHashCacheValue = digest;
    return digest;
  }

  // ---------------------------------------------------------------- runtime

  Future<void> _refreshRuntimeStatus() async {
    setState(() => _runtimeLoading = true);
    var commandExited = false;
    try {
      final arguments = ScribeForgeCli.runtimeStatusArguments();
      final buffer = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: arguments,
        onOutput: buffer.write,
        onError: (chunk) => buffer.write(chunk),
      );
      commandExited = true;
      if (!mounted) return;
      if (exitCode == 0) {
        final status = RuntimeStatusSnapshot.fromJsonText(buffer.toString());
        setState(() {
          _backendConnected = true;
          _backendLaunchError = null;
          _runtimeStatus = status;
          _runtimeSetupActionRequired = !_ocrRuntimeReady;
          _error = null;
        });
      } else {
        setState(() {
          _backendConnected = true;
          _backendLaunchError = null;
          _runtimeStatus = null;
          _runtimeSetupActionRequired = true;
          _error = buffer.toString().trim();
        });
      }
    } catch (exc) {
      if (!mounted) return;
      final diagnosis = diagnoseCoreLaunchFailure(exc, _cliExecutable);
      setState(() {
        _backendConnected = commandExited;
        _backendLaunchError = commandExited ? null : exc;
        _runtimeStatus = null;
        _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
        _error = commandExited ? '读取运行时状态失败：$exc' : diagnosis.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _runtimeLoading = false;
          _runtimeChecked = true;
        });
      }
    }
  }

  // ------------------------------------------------------------- review queue

  Future<void> _refreshReviewQueue() async {
    final storePath = _projectStorePath;
    if (!mounted || storePath == null) return;
    final version = ++_reviewRequestVersion;
    final includeFurniture = _includePageFurnitureInReview;
    bool isCurrent() =>
        mounted &&
        version == _reviewRequestVersion &&
        storePath == _projectStorePath;

    setState(() => _reviewLoading = true);
    try {
      final databasePath = ScribeForgeCli.joinPath(
        storePath,
        'project.sqlite3',
      );
      final exists = await _fileSystem.fileExists(databasePath);
      if (!isCurrent()) return;
      if (!exists) {
        setState(() {
          _reviewQueue.clear();
          _selectedReviewIndex = -1;
        });
        return;
      }
      final output = StringBuffer();
      final diagnostics = StringBuffer();
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.reviewArguments(
          storePath: storePath,
          includePageFurniture: includeFurniture,
        ),
        onOutput: output.write,
        onError: diagnostics.write,
      );
      if (!isCurrent()) return;
      if (exitCode != 0) {
        final diagnostic = diagnostics.isEmpty
            ? output.toString()
            : diagnostics.toString();
        setState(
          () => _error = diagnostic.trim().isEmpty
              ? '读取复核队列失败（退出码 $exitCode）。'
              : diagnostic.trim(),
        );
        return;
      }
      final snapshot = ReviewQueueSnapshot.fromJsonText(output.toString());
      final selected = _selectedReviewItem;
      final key = selected == null ? null : reviewItemKey(selected);
      setState(() {
        _reviewQueue
          ..clear()
          ..addAll(snapshot.items);
        final retained = key == null
            ? -1
            : _reviewQueue.indexWhere((item) => reviewItemKey(item) == key);
        _selectedReviewIndex = retained >= 0
            ? retained
            : ReviewQueueNavigation.move(
                _selectedReviewIndex,
                itemCount: _reviewQueue.length,
                delta: 0,
              );
        _error = null;
      });
    } catch (error) {
      if (isCurrent()) setState(() => _error = '读取复核队列失败：$error');
    } finally {
      if (isCurrent()) setState(() => _reviewLoading = false);
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
      _resetReviewForProject();
      _error = null;
      _notice = null;
    });
    _rememberProjectStore(storePath);
    await _refreshStatus();
    await _refreshReviewQueue();
  }

  void _rememberProjectStore(String storePath) {
    unawaited(
      _settingsStore.saveLastProjectStore(storePath).then((result) {
        if (!result.ok && mounted) {
          setState(() => _error = '保存最近项目失败：${result.error}');
        }
      }),
    );
  }

  Future<void> _openProject() async {
    final storePath = await _filePicker.getDirectoryPath(
      dialogTitle: '选择 ScribeForge 项目文件夹',
    );
    if (!mounted || storePath == null) return;
    await _openProjectAt(storePath);
  }

  /// Validates and loads the project store at [storePath]; shared by the
  /// file picker and the one-click "继续上次项目" resume action.
  Future<void> _openProjectAt(String storePath) async {
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
      _resetReviewForProject();
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
      final projects = ProjectStatusSnapshot.listFromJsonText(
        buffer.toString(),
      );
      if (projects.length != 1) {
        setState(
          () => _error = projects.isEmpty
              ? '所选项目文件夹还没有导入书籍。'
              : '该文件夹包含多个项目；请打开每本书各自的项目文件夹。',
        );
        return;
      }
      setState(() {
        _pdfPath = projects.single.sourceUri;
        _projectStatus = projects.single;
        _exportTitleController.text = projects.single.title;
        _selectedIndex = 0;
      });
      _rememberProjectStore(storePath);
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
      _resetReviewForProject();
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
    if (sourcePath == null ||
        _statusLoading ||
        _runtimeLoading ||
        !_projectSourceMatches) {
      return;
    }
    if (!_backendConnected) {
      setState(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _error = diagnoseCoreLaunchFailure(
          _backendLaunchError ?? StateError('backend is unavailable'),
          _cliExecutable,
        ).message;
      });
      return;
    }
    if (!_ocrRuntimeReady) {
      setState(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _notice = 'PDF 已保留在当前工作台。请先初始化 OCR 环境，再开始处理。';
      });
      await _openRuntimeSetup(autoStart: true);
      return;
    }
    final storePath =
        _projectStorePath ?? ScribeForgeCli.defaultStorePath(sourcePath);
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
          _notice = exitCode == 0 ? '处理完成' : '处理完成（部分页面失败；请查看页级状态后重试）';
        });
      } else {
        final diagnosis = diagnoseProcessingFailure(buffer.toString());
        setState(() {
          _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
          _error = diagnosis.message;
        });
      }
    } catch (exc) {
      if (!mounted) return;
      final diagnosis = diagnoseCoreLaunchFailure(exc, _cliExecutable);
      setState(() {
        _runtimeSetupActionRequired = diagnosis.requiresRuntimeSetup;
        _error = diagnosis.message;
      });
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

  Future<void> _resolveReviewPair(
    ReviewQueueItem item,
    String acceptedText,
  ) async {
    final storePath = _projectStorePath;
    final key = reviewItemKey(item);
    if (storePath == null ||
        _busy ||
        _reviewLoading ||
        !_reviewQueue.any((candidate) => reviewItemKey(candidate) == key))
      return;
    setState(() => _busy = true);
    try {
      final arguments = ScribeForgeCli.resolveArguments(
        storePath: storePath,
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
      if (!mounted || storePath != _projectStorePath) return;
      if (exitCode == 0) {
        setState(() {
          _reviewQueue.removeWhere((row) => reviewItemKey(row) == key);
          _selectedReviewIndex = ReviewQueueNavigation.move(
            _selectedReviewIndex,
            itemCount: _reviewQueue.length,
            delta: 0,
          );
          final detached = _reviewControllers.remove(key);
          if (detached != null) _disposeDraftsAfterFrame([detached]);
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
      if (!mounted || storePath != _projectStorePath) return;
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
    if (_projectStorePath == null ||
        _projectStatus == null ||
        !_projectCanExport)
      return;
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
    _exportOutputController.text = ScribeForgeCli.joinPath(
      directory,
      '$stem.$extension',
    );
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
      final result = await _settingsStore.saveVlmSettings(settings);
      if (!mounted) return;
      if (!result.ok) {
        final message = '保存 VLM 设置失败：${result.error}。可重新打开配置后重试。';
        setState(() => _error = message);
        return;
      }
      var notice = settings.enabled
          ? 'VLM 已启用；处理时会按此配置发送复核裁切图。'
          : 'VLM 已关闭；处理不会发送复核裁切图。';
      setState(() {
        _vlmSettings = settings;
        _notice = notice;
        _error = null;
      });
      // Issue #217: after saving, probe connectivity with the built-in sample
      // crop so a misconfigured endpoint is caught immediately. The API key
      // itself is never persisted — only the environment variable name.
      if (settings.enabled && settings.isConfigured) {
        final probe = await _probeVlmConnectivity(settings);
        if (mounted && probe != null) {
          setState(() => _notice = '$notice$probe');
        }
      }
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _error = '保存 VLM 设置失败：$error');
    }
  }

  /// Runs `vlm-check` once with the built-in sample crop (issue #217).
  ///
  /// Returns a short Chinese suffix describing the outcome, or null when the
  /// probe cannot run (e.g. CLI missing) so the save notice stays unchanged.
  Future<String?> _probeVlmConnectivity(DesktopVlmSettings settings) async {
    try {
      final sample = await writeBuiltInVlmSampleImage();
      var output = '';
      final exitCode = await _cliExecutor.run(
        executable: _cliExecutable,
        arguments: ScribeForgeCli.vlmCheckArguments(
          imagePath: sample.path,
          vlmSettings: settings,
        ),
        onOutput: (chunk) => output += chunk,
        onError: (chunk) => output += chunk,
      );
      if (exitCode == 0) {
        return ' 连通性探测成功。';
      }
      final detail = output.trim().isEmpty ? '' : '：${output.trim()}';
      return ' 连通性探测失败（退出码 $exitCode）$detail';
    } on Object {
      return null;
    }
  }

  Future<void> _openRuntimeSetup({bool autoStart = false}) async {
    if (!_backendConnected) {
      setState(() {
        _selectedIndex = 6;
        _runtimeSetupActionRequired = true;
        _error = diagnoseCoreLaunchFailure(
          _backendLaunchError ?? StateError('backend is unavailable'),
          _cliExecutable,
        ).message;
      });
      return;
    }
    final attempted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => RuntimeSetupDialog(
        runtimeStatus: _runtimeStatus,
        runSetup: _executeSetup,
        cancelSetup: _cliExecutor.cancel,
        autoStart: autoStart,
      ),
    );
    if (!mounted || attempted != true) return;
    await _refreshRuntimeStatus();
    if (!mounted || !_ocrRuntimeReady) return;
    setState(() {
      _selectedIndex = _pdfPath == null ? 0 : 1;
      _notice = '环境已就绪，可以继续导入或处理书籍。';
      _runtimeSetupActionRequired = false;
    });
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
  Widget build(BuildContext context) => WorkspaceShell(
    destinations: _destinations,
    selectedIndex: _selectedIndex,
    onSelect: (index) => setState(() => _selectedIndex = index),
    actions: <Widget>[
      IconButton(
        key: const ValueKey('help-button'),
        icon: const Icon(Icons.help_outline),
        tooltip: '使用帮助',
        onPressed: () => showWorkspaceHelpDialog(context),
      ),
      if (_busy)
        const Padding(
          padding: EdgeInsets.all(8),
          child: SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      if (_error != null)
        IconButton(
          icon: const Icon(Icons.error_outline, color: Colors.amber),
          onPressed: () => setState(() => _error = null),
        ),
    ],
    notices: <Widget>[_buildNoticeBar(), _buildNextStepBar()],
    pages: <Widget>[
      _buildLibraryPage(),
      _buildProcessPage(),
      _buildReviewPage(),
      _buildCandidateComparisonPage(),
      _buildExportPage(),
      _buildProjectSafetyPage(),
      this._buildSettingsPage(),
    ],
  );

  void _showInitialization() {
    setState(() => _selectedIndex = 6);
    if (_backendConnected) {
      unawaited(_openRuntimeSetup(autoStart: !_ocrRuntimeReady));
    }
  }

  NextStepGuidance _nextStep() {
    final status = _projectStatus;
    return nextStepGuidance(
      backendChecked: _runtimeChecked,
      backendLaunchFailed: _backendLaunchError != null,
      runtimeSetupRequired: _runtimeSetupActionRequired,
      hasProject: status != null,
      hasSource: _pdfPath != null,
      sourceMismatch: _projectSourceMismatchMessage != null,
      pagesWithBothOcr: status?.pagesWithBothOcrSuccess ?? 0,
      pagesTotal: status?.pagesTotal ?? 0,
      reviewQueued: status?.reviewQueued ?? 0,
      lastProjectStore: _lastProjectStorePath,
    );
  }

  Future<void> _runNextStepAction(NextStepAction action) async {
    switch (action) {
      case NextStepAction.none:
        return;
      case NextStepAction.configureBackend:
        setState(() => _selectedIndex = 6);
      case NextStepAction.initEnvironment:
        _showInitialization();
      case NextStepAction.importBook:
        await _pickPdf();
        if (mounted && _pdfPath != null) {
          setState(() => _selectedIndex = 1);
        }
      case NextStepAction.resumeLastProject:
        final lastStore = _lastProjectStorePath;
        if (lastStore == null) return;
        await _openProjectAt(lastStore);
        if (mounted && _error != null) {
          // The remembered store is gone; forget it so the banner falls
          // back to the plain import guidance.
          setState(() => _lastProjectStorePath = null);
          unawaited(_settingsStore.saveLastProjectStore(''));
        }
      case NextStepAction.relinkSource:
        await _relinkSource();
      case NextStepAction.goProcess:
        setState(() => _selectedIndex = 1);
      case NextStepAction.goReview:
        setState(() => _selectedIndex = 2);
      case NextStepAction.goExport:
        setState(() => _selectedIndex = 4);
    }
  }

  Widget _buildNextStepBar() {
    final guidance = _nextStep();
    if (guidance.action == NextStepAction.none) return const SizedBox.shrink();
    final actionLabel = switch (guidance.action) {
      NextStepAction.none => '',
      NextStepAction.configureBackend => '去设置',
      NextStepAction.initEnvironment => '下载并配置',
      NextStepAction.importBook => '去导入',
      NextStepAction.resumeLastProject => '继续上次项目',
      NextStepAction.relinkSource => '去关联源文件',
      NextStepAction.goProcess => '去处理',
      NextStepAction.goReview => '去复核',
      NextStepAction.goExport => '去导出',
    };
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      color: Theme.of(context).colorScheme.primaryContainer,
      child: Row(
        children: <Widget>[
          Icon(
            Icons.lightbulb_outline,
            color: Theme.of(context).colorScheme.onPrimaryContainer,
            size: 18,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '下一步：${guidance.message}',
              key: const ValueKey('next-step-message'),
              style: TextStyle(
                color: Theme.of(context).colorScheme.onPrimaryContainer,
                fontSize: 12,
              ),
            ),
          ),
          TextButton(
            key: const ValueKey('next-step-action'),
            onPressed: _busy
                ? null
                : () => unawaited(_runNextStepAction(guidance.action)),
            child: Text(actionLabel),
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
          Icon(
            _error == null ? Icons.info : Icons.error_outline,
            color: _error == null
                ? Colors.green.shade700
                : Colors.amber.shade700,
            size: 18,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _error ?? _notice!,
              style: TextStyle(
                color: _error == null
                    ? Colors.green.shade800
                    : Colors.amber.shade800,
                fontSize: 12,
              ),
            ),
          ),
          if (_runtimeSetupActionRequired)
            TextButton(
              onPressed: _busy ? null : _showInitialization,
              child: const Text('初始化环境'),
            ),
          if (_error != null)
            IconButton(
              icon: const Icon(Icons.close, size: 16),
              onPressed: () => setState(() => _error = null),
            ),
        ],
      ),
    );
  }

  // ============================================================ 书籍页面

  Widget _buildLibraryPage() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            '已导入的书籍',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '导入新书，或打开已有项目继续处理、复核和导出。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
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
                Expanded(
                  child: Text(
                    status.title,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                if (!_projectSourceMatches)
                  const Icon(Icons.warning, color: Colors.amber, size: 18),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              status.sourceUri,
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
            ),
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
                style: TextStyle(
                  color: Theme.of(context).colorScheme.primary,
                  fontSize: 12,
                ),
              ),
            ],
            const SizedBox(height: 8),
            Text(
              '复核队列：${status.reviewQueued} 项待处理',
              style: const TextStyle(fontSize: 12),
            ),
            if (!_projectSourceMatches &&
                _projectSourceMismatchMessage != null) ...[
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
              const Text(
                '失败任务：',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
              ),
              ...status.failedJobDetails.map(
                (failed) => Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    failed.description,
                    style: TextStyle(color: Colors.red.shade700, fontSize: 11),
                  ),
                ),
              ),
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
          const Text(
            '导入并处理书籍',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '选择扫描书籍的 PDF 或页面图片，运行可恢复的双引擎 OCR 与校对管线。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
          const SizedBox(height: 6),
          Text(
            this._engineComboSummary(),
            key: const ValueKey('engine-combo-summary'),
            style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
          ),
          const SizedBox(height: 20),
          WorkspaceFileField(
            field: InputDecorator(
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
            action: ElevatedButton.icon(
              icon: const Icon(Icons.folder_open),
              label: const Text('选择文件'),
              onPressed: _busy ? null : _pickPdf,
            ),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              icon: Icon(
                _processing
                    ? (_cancelRequested
                          ? Icons.hourglass_empty
                          : Icons.cancel_outlined)
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
                            _runtimeLoading ||
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
            const Text(
              '项目存储目录：',
              style: TextStyle(fontSize: 12, color: Colors.grey),
            ),
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
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                key: const ValueKey('open-report-button'),
                onPressed: _showProcessingReport,
                icon: const Icon(Icons.assessment_outlined, size: 16),
                label: const Text('查看处理报告'),
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

  Widget _buildReviewPage() => ReviewWorkspacePage(
    storePath: _projectStorePath,
    projectId: _projectStatus?.projectId,
    queue: _reviewQueue,
    selectedIndex: _selectedReviewIndex,
    busy: _busy,
    loading: _reviewLoading,
    includePageFurniture: _includePageFurnitureInReview,
    decisionFocusNode: _reviewDecisionFocusNode,
    controllerFor: _reviewControllerFor,
    onRecord: _resolveReviewPair,
    onRefresh: _refreshReviewQueue,
    onSelect: (index) => setState(() => _selectedReviewIndex = index),
    onIncludePageFurniture: (include) {
      setState(() => _includePageFurnitureInReview = include);
      unawaited(_refreshReviewQueue());
    },
  );

  void _focusReviewDecisionInputAfterFrame() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _selectedReviewItem != null) {
        _reviewDecisionFocusNode.requestFocus();
      }
    });
  }

  // ======================================================= 候选比较页面

  Widget _buildCandidateComparisonPage() {
    final status = _projectStatus;
    final canGenerateCandidates =
        !_busy &&
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
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            '导出成品',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            '把已验证的文字写出为 EPUB 3 或 DOCX。',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
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
          onChanged: (v) =>
              setState(() => _exportStrictAcceptance = v ?? false),
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
            onPressed:
                _busy ||
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
      _resetReviewForProject();
      _selectedIndex = 0;
      _error = null;
      _notice = '项目已恢复并载入工作台。';
    });
    _rememberProjectStore(storePath);
    await _refreshStatus();
    if (!mounted) return;
    final restoredSourcePath = _projectStatus?.sourceUri;
    if (restoredSourcePath != null && restoredSourcePath.isNotEmpty) {
      setState(() => _pdfPath = restoredSourcePath);
      await _refreshStatus();
    }
    await _refreshReviewQueue();
  }

  // The settings feature composes callbacks without bypassing the widget lifecycle.
  void _updateWorkspace(VoidCallback mutation) {
    if (mounted) setState(mutation);
  }

  void _showProcessingReport() {
    final storePath =
        _projectStorePath ??
        (_pdfPath == null ? null : ScribeForgeCli.defaultStorePath(_pdfPath!));
    if (storePath == null) return;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => ProcessingReportSheet(
        executable: _cliExecutable,
        storePath: storePath,
        loader: _reportLoader,
        processing: _processing,
        exportHandler: _exportProcessingReport,
      ),
    );
  }

  Future<String?> _exportProcessingReport(
    ProcessingReportSnapshot report, {
    required bool markdown,
  }) async {
    final directory = await _filePicker.getDirectoryPath(
      dialogTitle: '选择报告导出目录',
    );
    if (!mounted || directory == null || directory.trim().isEmpty) {
      return null;
    }
    final stamp = DateTime.now()
        .toIso8601String()
        .replaceAll(RegExp(r'[:.]'), '-')
        .substring(0, 19);
    final fileName = markdown ? 'report-$stamp.md' : 'report-$stamp.json';
    final file = File(ScribeForgeCli.joinPath(directory.trim(), fileName));
    await file.writeAsString(
      markdown ? processingReportMarkdown(report) : report.rawJson,
    );
    return file.path;
  }

  Future<String?> _promptString(String title, String initialValue) async {
    final controller = TextEditingController(text: initialValue);
    return showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: TextField(controller: controller, autofocus: true),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
  }
}
