import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';

import '../l10n/desktop_strings.dart';
import '../models/export_readiness.dart';
import '../models/next_step_guidance.dart';
import '../models/processing_report.dart';
import '../models/project_status.dart';
import '../models/review_history.dart';
import '../models/review_queue.dart';
import '../models/review_queue_navigation.dart';
import '../models/review_triage.dart';
import '../models/superseded_review_drafts.dart';
import '../models/runtime_health.dart';
import '../models/runtime_location.dart';
import '../models/runtime_setup.dart';
import '../models/runtime_status.dart';
import '../services/backend_diagnostics.dart';
import '../services/backend_environment.dart';
import '../services/backend_resolver.dart';
import '../services/book_diagnosis_service.dart';
import '../services/candidate_comparison_store.dart';
import '../services/cli_executor.dart';
import '../services/cli_path_validator.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';
import '../services/export_readiness_service.dart';
import '../services/export_verification_service.dart';
import '../services/job_history_service.dart';
import '../services/ocr_evidence_service.dart';
import '../services/processing_report_loader.dart';
import '../services/python_locator.dart';
import '../services/report_comparison_service.dart';
import '../services/review_history_service.dart';
import '../services/runtime_location_service.dart';
import '../services/settings_store.dart';
import '../services/textless_confirmation_service.dart';
import '../services/workspace_file_system.dart';
import '../theme/scribe_theme.dart';
import '../widgets/workspace_components.dart';
import '../widgets/workspace_help_dialog.dart';
import '../widgets/workspace_shell.dart';
import 'candidate_comparison_screen.dart';
import 'evidence/ocr_evidence_browser.dart';
import 'export/book_diagnosis_screen.dart';
import 'export/export_readiness_screen.dart';
import 'export/export_verification_screen.dart';
import 'jobs/job_history_screen.dart';
import 'jobs/scoped_retry_dialog.dart';
import 'processing_report_sheet.dart';
import 'project_safety_screen.dart';
import 'reports/report_comparison_screen.dart';
import 'review/review_history_screen.dart';
import 'review/review_workspace_page.dart';
import 'review/superseded_drafts_dialog.dart';
import 'review/textless_confirmation_screen.dart';
import 'runtime_health_dialog.dart';
import 'runtime_location_dialog.dart';
import 'runtime_setup_dialog.dart';
import 'vlm_settings_dialog.dart';

part 'unified_workspace_output.dart';
part 'unified_workspace_processing.dart';
part 'unified_workspace_project.dart';
part 'unified_workspace_review.dart';
part 'unified_workspace_settings.dart';

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
  int _pageWorkers = 1;
  bool _cancelRequested = false;
  String? _notice;
  String? _error;
  bool _cliProbing = false;
  String? _cliProbeMessage;
  SettingsCorruption? _settingsCorruption;
  bool _settingsLoading = true;
  FileSystemException? _settingsReadError;

  ProjectStatusSnapshot? _projectStatus;
  bool _statusLoading = false;
  int _statusRequestVersion = 0;
  Timer? _processStatusTimer;
  String? _sourceHashCachePath;
  int? _sourceHashCacheSize;
  int? _sourceHashCacheModifiedMillis;
  String? _sourceHashCacheValue;

  RuntimeStatusSnapshot? _runtimeStatus;
  RuntimeHealthReport? _runtimeHealthReport;
  String? _runtimeStatusError;
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

  final Map<(ReviewItemKey, String?), TextEditingController>
  _reviewControllers = {};
  final _supersededDrafts = SupersededReviewDrafts();

  List<WorkspaceDestination> _destinations(DesktopStrings strings) =>
      <WorkspaceDestination>[
        WorkspaceDestination(strings.books, Icons.library_books),
        WorkspaceDestination(strings.process, Icons.auto_awesome),
        WorkspaceDestination(strings.review, Icons.fact_check_outlined),
        WorkspaceDestination(strings.candidates, Icons.compare_arrows),
        WorkspaceDestination(strings.export, Icons.output),
        WorkspaceDestination(strings.safety, Icons.shield_outlined),
        WorkspaceDestination(strings.settings, Icons.settings),
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
    late final String executable;
    late final DesktopVlmSettings vlmSettings;
    late final RuntimeLocationPreferences locations;
    late final String backendPython;
    late final String backendSourcePath;
    late final String? lastProjectStore;
    try {
      executable = await _settingsStore.loadCliExecutable();
      vlmSettings = await _settingsStore.loadVlmSettings();
      locations = await _settingsStore.loadRuntimeLocations();
      backendPython = await _settingsStore.loadBackendPython();
      backendSourcePath = await _settingsStore.loadBackendSourcePath();
      lastProjectStore = await _settingsStore.loadLastProjectStore();
    } on FileSystemException catch (error) {
      if (!mounted) return;
      setState(() {
        _settingsLoading = false;
        _settingsReadError = error;
        _runtimeLoading = false;
      });
      return;
    }
    final venvCliPath = BackendVenvPaths(this._backendVenvDirectory)
        .cliExecutable;
    final resolution = await _backendResolver.resolve(
      executable,
      venvCliPath: venvCliPath,
    );
    if (!mounted) return;
    final corruption = _settingsStore.pendingCorruption;
    setState(() {
      _settingsLoading = false;
      _settingsReadError = null;
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

  @override
  Widget build(BuildContext context) {
    final strings = DesktopStrings.of(context);
    return _settingsLoading || _settingsReadError != null
        ? _buildSettingsRecovery()
        : WorkspaceShell(
            destinations: _destinations(strings),
            selectedIndex: _selectedIndex,
            onSelect: (index) => setState(() => _selectedIndex = index),
            actions: <Widget>[
              IconButton(
                key: const ValueKey('help-button'),
                icon: const Icon(Icons.help_outline),
                tooltip: strings.helpTooltip,
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
                  icon: const Icon(Icons.close, size: 20),
                  tooltip: strings.closeErrorTooltip,
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
  }

  Widget _buildSettingsRecovery() {
    final error = _settingsLoading ? null : _settingsReadError;
    final path =
        '${_settingsStore.settingsDirectory.path}'
        '${Platform.pathSeparator}${DesktopSettingsStore.settingsFileName}';
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 560),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  if (error == null) ...<Widget>[
                    const CircularProgressIndicator(),
                    const SizedBox(height: 16),
                    const Text('正在读取桌面设置…'),
                  ] else ...<Widget>[
                    Text(
                      '无法读取桌面设置',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 12),
                    const Text('为保护已有设置，工作台暂未初始化，也不会自动重置或覆盖文件。'),
                    const SizedBox(height: 12),
                    SelectableText('配置文件：$path'),
                    const SizedBox(height: 8),
                    SelectableText('系统错误：$error'),
                    const SizedBox(height: 12),
                    const Text('请检查目录和文件权限、磁盘连接或其他程序占用，然后重试。'),
                    const SizedBox(height: 16),
                    FilledButton.icon(
                      key: const ValueKey('retry-settings-read'),
                      onPressed: () {
                        setState(() => _settingsLoading = true);
                        unawaited(_loadSettings());
                      },
                      icon: const Icon(Icons.refresh),
                      label: const Text('重试读取设置'),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  void _showInitialization() {
    setState(() => _selectedIndex = 6);
    if (_backendConnected) {
      unawaited(_openRuntimeSetup());
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
      pagesTotal: status?.sourcePagesTotal ?? status?.pagesTotal ?? 0,
      importIncomplete:
          (status?.importState != null && status?.importState != 'succeeded') ||
          (status?.importState == null && status?.sourcePagesTotal != null),
      sourceExtentMismatch:
          status?.importState == 'succeeded' &&
          status?.sourcePagesTotal != null &&
          status?.pagesTotal != status?.sourcePagesTotal,
      sourceExtentUnknown: status != null && status.sourcePagesTotal == null,
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
    final strings = DesktopStrings.of(context);
    if (guidance.action == NextStepAction.none) return const SizedBox.shrink();
    return _workspaceStrip(
      icon: Icons.arrow_forward_rounded,
      background: ScribePalette.mist,
      foreground: ScribePalette.forest,
      message: Text(
        '${strings.nextStepPrefix}${guidance.message}',
        key: const ValueKey('next-step-message'),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      action: TextButton(
        key: const ValueKey('next-step-action'),
        onPressed: _busy || _backendBusy
            ? null
            : () => unawaited(_runNextStepAction(guidance.action)),
        child: Text(strings.actionButton(guidance.action.name)),
      ),
    );
  }

  Widget _buildNoticeBar() {
    final message = _error ?? _notice;
    if (message == null) return const SizedBox.shrink();
    final isError = _error != null;
    return _workspaceStrip(
      icon: isError ? Icons.error_outline : Icons.info_outline,
      background: isError
          ? ScribePalette.amberSurface
          : ScribePalette.infoSurface,
      foreground: isError ? ScribePalette.amber : ScribePalette.info,
      message: Tooltip(
        message: message,
        child: Text(message, maxLines: 2, overflow: TextOverflow.ellipsis),
      ),
      action: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (_runtimeSetupActionRequired &&
              _backendConnected &&
              _nextStep().action != NextStepAction.initEnvironment)
            TextButton(
              onPressed: _busy ? null : _showInitialization,
              child: const Text('初始化环境'),
            ),
          IconButton(
            tooltip: '查看完整消息',
            icon: const Icon(Icons.open_in_full, size: 16),
            onPressed: () => showDialog<void>(
              context: context,
              builder: (context) => AlertDialog(
                title: Text(isError ? '需要处理的问题' : '操作消息'),
                content: SingleChildScrollView(child: SelectableText(message)),
                actions: <Widget>[
                  TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('关闭'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _workspaceStrip({
    required IconData icon,
    required Color background,
    required Color foreground,
    required Widget message,
    required Widget action,
  }) {
    final style = Theme.of(context).textTheme.bodySmall;
    return ColoredBox(
      color: background,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: ScribeSpacing.x3),
        child: Row(
          children: <Widget>[
            Icon(icon, color: foreground, size: 17),
            const SizedBox(width: ScribeSpacing.x2),
            Expanded(
              child: DefaultTextStyle(
                style: (style ?? const TextStyle()).copyWith(color: foreground),
                child: message,
              ),
            ),
            const SizedBox(width: ScribeSpacing.x1),
            action,
          ],
        ),
      ),
    );
  }

  // ============================================================ 书籍页面

  void _updateWorkspace(VoidCallback mutation) {
    if (mounted) setState(mutation);
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
