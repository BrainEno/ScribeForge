import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';

import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';
import '../services/workspace_file_system.dart';
import '../theme/scribe_theme.dart';
import '../widgets/workspace_components.dart';

/// Project audit, backup, verification, and safe restore in one desktop page.
///
/// The screen delegates every external operation to the injected CLI and file
/// boundaries. Audit never writes to the store; restore requires a verified
/// archive, an explicit confirmation, and a newly generated destination path.
class ProjectSafetyScreen extends StatefulWidget {
  const ProjectSafetyScreen({
    super.key,
    required this.storePath,
    required this.projectTitle,
    required this.cliExecutable,
    required this.cliExecutor,
    required this.filePicker,
    required this.onProjectRestored,
    this.fileSystem,
  });

  final String? storePath;
  final String projectTitle;
  final String cliExecutable;
  final ScribeForgeCliExecutor cliExecutor;
  final DesktopFilePicker filePicker;
  final Future<void> Function(String storePath) onProjectRestored;
  final WorkspaceFileSystem? fileSystem;

  @override
  State<ProjectSafetyScreen> createState() => _ProjectSafetyScreenState();
}

class _ProjectSafetyScreenState extends State<ProjectSafetyScreen> {
  late final TextEditingController _backupDirectoryController;
  late final TextEditingController _backupPathController;
  late final TextEditingController _restoreParentController;
  late final WorkspaceFileSystem _fileSystem;

  Map<String, dynamic>? _auditReport;
  Map<String, dynamic>? _backupReport;
  Map<String, dynamic>? _verificationReport;
  Map<String, dynamic>? _restoreReport;
  String? _auditError;
  String? _backupError;
  String? _verificationError;
  String? _restoreError;
  String? _verifiedBackupPath;
  String? _verifiedBackupFingerprint;
  bool _auditRunning = false;
  bool _backupRunning = false;
  bool _verificationRunning = false;
  bool _restoreRunning = false;

  bool get _busy =>
      _auditRunning || _backupRunning || _verificationRunning || _restoreRunning;

  String? get _storePath {
    final value = widget.storePath?.trim();
    return value == null || value.isEmpty ? null : value;
  }

  @override
  void initState() {
    super.initState();
    _backupDirectoryController = TextEditingController();
    _backupPathController = TextEditingController();
    _restoreParentController = TextEditingController();
    _fileSystem = widget.fileSystem ?? const IoWorkspaceFileSystem();
  }

  @override
  void didUpdateWidget(covariant ProjectSafetyScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.storePath != widget.storePath ||
        oldWidget.projectTitle != widget.projectTitle) {
      _auditReport = null;
      _backupReport = null;
      _verificationReport = null;
      _restoreReport = null;
      _auditError = null;
      _backupError = null;
      _verificationError = null;
      _restoreError = null;
      _verifiedBackupPath = null;
      _verifiedBackupFingerprint = null;
      _backupPathController.clear();
    }
  }

  @override
  void dispose() {
    _backupDirectoryController.dispose();
    _backupPathController.dispose();
    _restoreParentController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.projectTitle.trim();
    return SingleChildScrollView(
      child: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1120),
          child: Padding(
            key: const ValueKey('project-safety-page'),
            padding: const EdgeInsets.all(ScribeSpacing.x6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                ScribePageHeader(
                  icon: Icons.shield_outlined,
                  title: '项目安全中心',
                  subtitle: '检查项目完整性、创建可验证备份，并安全恢复到新目录。',
                  trailing: _storePath == null
                      ? const Chip(
                          avatar: Icon(Icons.archive_outlined, size: 18),
                          label: Text('恢复工具可用'),
                        )
                      : Chip(
                          avatar: const Icon(Icons.folder_open_outlined, size: 18),
                          label: Text(title.isEmpty ? _lastPathSegment(_storePath!) : title),
                        ),
                ),
                const SizedBox(height: ScribeSpacing.x6),
                if (_storePath != null) ...<Widget>[
                  ScribeSectionCard(
                    icon: Icons.fact_check_outlined,
                    title: '项目完整性审计',
                    description: '以只读方式检查数据库、来源文件与页面证据；不会修复或改写项目。',
                    child: _buildAuditContent(),
                  ),
                  const SizedBox(height: ScribeSpacing.x5),
                  ScribeSectionCard(
                    icon: Icons.archive_outlined,
                    title: '创建项目备份',
                    description: '将项目存储为带校验信息的 ZIP 归档。默认不覆盖已有备份。',
                    child: _buildBackupContent(),
                  ),
                ] else ...<Widget>[
                  ScribeSectionCard(
                    icon: Icons.folder_off,
                    title: '项目完整性审计',
                    description: '打开项目后，可在此运行只读审计并创建项目备份。',
                    child: const ScribeEmptyState(
                      icon: Icons.folder_open_outlined,
                      title: '请先打开项目',
                      message: '恢复备份不需要先打开项目；审计和创建备份需要一个当前项目。',
                    ),
                  ),
                  const SizedBox(height: ScribeSpacing.x5),
                  ScribeSectionCard(
                    icon: Icons.archive_outlined,
                    title: '创建项目备份',
                    description: '打开项目后，可创建不会默认覆盖现有文件的备份。',
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const Text('请先打开项目，再选择备份保存目录。'),
                        const SizedBox(height: ScribeSpacing.x3),
                        FilledButton.icon(
                          onPressed: null,
                          icon: const Icon(Icons.archive_outlined),
                          label: const Text('创建项目备份'),
                        ),
                      ],
                    ),
                  ),
                ],
                const SizedBox(height: ScribeSpacing.x5),
                ScribeSectionCard(
                  icon: Icons.restore,
                  title: '验证与恢复',
                  description: '先验证归档，再确认恢复目标；现有项目目录绝不会被覆盖。',
                  child: _buildRestoreContent(),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildAuditContent() {
    final report = _auditReport;
    final findings = report?['findings'];
    final healthy = report?['healthy'] == true;
    final hasBlockingFinding = findings is List && findings.any((finding) {
      if (finding is! Map) return true;
      final severity = finding['severity'];
      return severity == 'fatal' || severity == 'error';
    });
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (_auditError != null) ...<Widget>[
          ScribeStatusBanner(
            kind: ScribeStatusKind.error,
            title: '审计失败，项目未修改',
            message: _auditError!,
          ),
          const SizedBox(height: ScribeSpacing.x4),
        ] else if (report != null) ...<Widget>[
          ScribeStatusBanner(
            kind: healthy
                ? ScribeStatusKind.success
                : hasBlockingFinding
                    ? ScribeStatusKind.error
                    : ScribeStatusKind.warning,
            title: healthy ? '项目完整性正常' : '审计发现需要关注的问题',
            message: _auditSummary(report),
          ),
          if (findings is List && findings.isNotEmpty) ...<Widget>[
            const SizedBox(height: ScribeSpacing.x3),
            ...findings.map(_buildFinding),
          ],
          const SizedBox(height: ScribeSpacing.x4),
        ],
        Wrap(
          spacing: 12,
          runSpacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: <Widget>[
            FilledButton.icon(
              onPressed: _busy ? null : _runAudit,
              icon: _auditRunning
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.play_arrow_rounded),
              label: Text(_auditRunning ? '正在审计…' : '运行完整性审计'),
            ),
            if (report != null)
              TextButton.icon(
                onPressed: _busy ? null : _runAudit,
                icon: const Icon(Icons.refresh),
                label: const Text('重新审计'),
              ),
          ],
        ),
      ],
    );
  }

  Widget _buildFinding(Object? rawFinding) {
    final finding = rawFinding is Map
        ? Map<String, dynamic>.from(rawFinding)
        : <String, dynamic>{'severity': 'error', 'message': 'Invalid finding record.'};
    final severity = finding['severity']?.toString() ?? 'warning';
    final isBlocking = severity == 'fatal' || severity == 'error';
    return Padding(
      padding: const EdgeInsets.only(top: ScribeSpacing.x2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(
            isBlocking ? Icons.error_outline : Icons.warning_amber_outlined,
            size: 18,
            color: isBlocking
                ? Theme.of(context).colorScheme.error
                : ScribePalette.amber,
          ),
          const SizedBox(width: ScribeSpacing.x2),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  '${severity.toUpperCase()} · ${finding['code'] ?? 'finding'}',
                  style: Theme.of(context).textTheme.labelMedium,
                ),
                Text(finding['message']?.toString() ?? 'No diagnostic message.'),
                if (finding['path'] is String)
                  SelectableText(
                    finding['path'] as String,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBackupContent() {
    final report = _backupReport;
    final healthy = report?['healthy'] == true;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        _pathPickerRow(
          controller: _backupDirectoryController,
          label: '備份保存目錄',
          hint: '選擇 ZIP 備份的保存位置',
          key: const ValueKey('backup-directory-path'),
          buttonLabel: '選擇備份位置',
          onPressed: _busy ? null : _pickBackupDirectory,
        ),
        const SizedBox(height: ScribeSpacing.x4),
        if (_backupError != null) ...<Widget>[
          ScribeStatusBanner(
            kind: ScribeStatusKind.error,
            title: '備份失敗，原項目保持不變',
            message: _backupError!,
          ),
          const SizedBox(height: ScribeSpacing.x4),
        ] else if (report != null) ...<Widget>[
          ScribeStatusBanner(
            kind: healthy ? ScribeStatusKind.success : ScribeStatusKind.warning,
            title: healthy ? '備份已创建' : '备份已创建，但报告提示需要关注',
            message: _backupSummary(report),
          ),
          if (report['archive'] is String)
            Padding(
              padding: const EdgeInsets.only(top: ScribeSpacing.x2),
              child: SelectableText(report['archive'] as String),
            ),
          const SizedBox(height: ScribeSpacing.x4),
        ],
        Wrap(
          spacing: 12,
          runSpacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: <Widget>[
            FilledButton.icon(
              onPressed: _busy ||
                      _storePath == null ||
                      _backupDirectoryController.text.trim().isEmpty
                  ? null
                  : _createBackup,
              icon: _backupRunning
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.archive_outlined),
              label: Text(_backupRunning ? '正在创建备份…' : '创建项目备份'),
            ),
            if (report != null)
              TextButton.icon(
                onPressed: _busy || report['archive'] is! String
                    ? null
                    : () => _useCreatedBackup(report['archive'] as String),
                icon: const Icon(Icons.verified_outlined),
                label: const Text('验证此备份'),
              ),
          ],
        ),
      ],
    );
  }

  Widget _buildRestoreContent() {
    final report = _verificationReport;
    final restoreReport = _restoreReport;
    final verified = report?['healthy'] == true &&
        _verifiedBackupPath != null &&
        _verifiedBackupPath == _backupPathController.text;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        _pathPickerRow(
          controller: _backupPathController,
          label: '备份 ZIP 文件',
          hint: '选择 ScribeForge 项目 ZIP 备份',
          key: const ValueKey('restore-backup-path'),
          buttonLabel: '选择备份文件',
          onPressed: _busy ? null : _pickBackupFile,
        ),
        const SizedBox(height: ScribeSpacing.x3),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: <Widget>[
            OutlinedButton.icon(
              onPressed: _busy || _backupPathController.text.trim().isEmpty
                  ? null
                  : _verifySelectedBackup,
              icon: _verificationRunning
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.verified_outlined),
              label: Text(_verificationRunning ? '正在验证…' : '验证备份'),
            ),
            if (verified)
              const Chip(
                avatar: Icon(Icons.check_circle_outline, size: 18),
                label: Text('已验证，可恢复'),
              ),
          ],
        ),
        if (_verificationError != null) ...<Widget>[
          const SizedBox(height: ScribeSpacing.x4),
          ScribeStatusBanner(
            kind: ScribeStatusKind.error,
            title: '备份验证失败',
            message: _verificationError!,
          ),
        ] else if (report != null) ...<Widget>[
          const SizedBox(height: ScribeSpacing.x4),
          ScribeStatusBanner(
            kind: report['healthy'] == true
                ? ScribeStatusKind.success
                : ScribeStatusKind.warning,
            title: report['healthy'] == true ? '备份验证通过' : '备份验证发现问题',
            message: _verificationSummary(report),
          ),
          if (report['archive'] is String)
            Padding(
              padding: const EdgeInsets.only(top: ScribeSpacing.x2),
              child: SelectableText(report['archive'] as String),
            ),
          if (report['source_store'] is String)
            Padding(
              padding: const EdgeInsets.only(top: ScribeSpacing.x2),
              child: SelectableText('归档原项目：${report['source_store']}'),
            ),
          if (report['audit_warnings'] is List &&
              (report['audit_warnings'] as List).isNotEmpty) ...<Widget>[
            const SizedBox(height: ScribeSpacing.x3),
            ScribeStatusBanner(
              kind: ScribeStatusKind.warning,
              title: '原项目审计记录了 ${(report['audit_warnings'] as List).length} 条提示',
              message: '归档本身已通过校验；以下项目审计提示仍需留意。',
            ),
            ...(report['audit_warnings'] as List).map(_buildFinding),
          ],
        ],
        const SizedBox(height: ScribeSpacing.x5),
        _pathPickerRow(
          controller: _restoreParentController,
          label: '恢复父目录',
          hint: '选择新项目文件夹的放置位置',
          key: const ValueKey('restore-parent-path'),
          buttonLabel: '选择恢复父目录',
          onPressed: _busy ? null : _pickRestoreParent,
        ),
        const SizedBox(height: ScribeSpacing.x4),
        if (_restoreError != null) ...<Widget>[
          ScribeStatusBanner(
            kind: ScribeStatusKind.error,
            title: '恢复失败；请查看诊断并重试',
            message: _restoreError!,
          ),
          const SizedBox(height: ScribeSpacing.x4),
        ] else if (restoreReport != null) ...<Widget>[
          ScribeStatusBanner(
            kind: restoreReport['healthy'] == true
                ? ScribeStatusKind.success
                : ScribeStatusKind.error,
            title: restoreReport['healthy'] == true
                ? '恢复完成'
                : '恢复目标未通过完整性检查',
            message: _restoreSummary(restoreReport),
          ),
          if (restoreReport['store'] is String)
            Padding(
              padding: const EdgeInsets.only(top: ScribeSpacing.x2),
              child: SelectableText(restoreReport['store'] as String),
            ),
          if (restoreReport['audit_warnings'] is List &&
              (restoreReport['audit_warnings'] as List).isNotEmpty) ...<Widget>[
            const SizedBox(height: ScribeSpacing.x3),
            ScribeStatusBanner(
              kind: ScribeStatusKind.warning,
              title: '恢复后的审计包含 ${(restoreReport['audit_warnings'] as List).length} 条提示',
              message: '恢复成功；这些提示未被自动修复。',
            ),
            ...(restoreReport['audit_warnings'] as List).map(_buildFinding),
          ],
          const SizedBox(height: ScribeSpacing.x4),
        ],
        FilledButton.icon(
          onPressed: _busy ||
                  !verified ||
                  _restoreParentController.text.trim().isEmpty
              ? null
              : _confirmRestore,
          icon: _restoreRunning
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.restore),
          label: Text(_restoreRunning ? '正在恢复…' : '恢复到新目录'),
        ),
      ],
    );
  }

  Widget _pathPickerRow({
    required TextEditingController controller,
    required String label,
    required String hint,
    required Key key,
    required String buttonLabel,
    required VoidCallback? onPressed,
  }) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final field = TextField(
          key: key,
          controller: controller,
          readOnly: true,
          onTap: onPressed,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            prefixIcon: const Icon(Icons.folder_open_outlined),
          ),
        );
        final button = OutlinedButton.icon(
          onPressed: onPressed,
          icon: const Icon(Icons.folder_open_outlined),
          label: Text(buttonLabel),
        );
        if (constraints.maxWidth < 560) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[field, const SizedBox(height: ScribeSpacing.x2), button],
          );
        }
        return Row(
          children: <Widget>[
            Expanded(child: field),
            const SizedBox(width: ScribeSpacing.x3),
            button,
          ],
        );
      },
    );
  }

  Future<void> _runAudit() async {
    final storePath = _storePath;
    if (storePath == null || _busy) return;
    setState(() {
      _auditRunning = true;
      _auditError = null;
      _auditReport = null;
    });
    try {
      final report = await _runJsonCommand(
        ScribeForgeCli.auditArguments(storePath: storePath),
        operation: '项目审计',
        acceptNonZeroJsonResult: true,
      );
      _validateAuditReport(report);
      if (!mounted) return;
      setState(() => _auditReport = report);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _auditError = _errorMessage(error));
    } finally {
      if (mounted) setState(() => _auditRunning = false);
    }
  }

  Future<void> _pickBackupDirectory() async {
    final path = await widget.filePicker.getDirectoryPath(
      dialogTitle: '选择项目备份保存位置',
    );
    if (!mounted || path == null || path.trim().isEmpty) return;
    setState(() {
      _backupDirectoryController.text = path;
      _backupReport = null;
      _backupError = null;
    });
  }

  Future<void> _createBackup() async {
    final storePath = _storePath;
    final directory = _backupDirectoryController.text.trim();
    if (_busy || storePath == null || directory.isEmpty) return;
    final outputPath = ScribeForgeCli.joinPath(
      directory,
      '${_safeName(widget.projectTitle)}-backup-${_timestamp()}.zip',
    );
    setState(() {
      _backupRunning = true;
      _backupError = null;
      _backupReport = null;
    });
    try {
      final report = await _runJsonCommand(
        ScribeForgeCli.backupArguments(
          storePath: storePath,
          outputPath: outputPath,
        ),
        operation: '项目备份',
      );
      _validateBackupReport(report);
      if (!mounted) return;
      setState(() => _backupReport = report);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _backupError = _errorMessage(error));
    } finally {
      if (mounted) setState(() => _backupRunning = false);
    }
  }

  Future<void> _pickBackupFile() async {
    final path = await widget.filePicker.pickFile(
      dialogTitle: '选择 ScribeForge 项目备份',
      allowedExtensions: const <String>['zip'],
    );
    if (!mounted || path == null || path.trim().isEmpty) return;
    setState(() {
      _backupPathController.text = path;
      _verificationReport = null;
      _verificationError = null;
      _verifiedBackupPath = null;
      _verifiedBackupFingerprint = null;
      _restoreReport = null;
      _restoreError = null;
    });
  }

  Future<void> _pickRestoreParent() async {
    final path = await widget.filePicker.getDirectoryPath(
      dialogTitle: '选择新项目的恢复位置',
    );
    if (!mounted || path == null || path.trim().isEmpty) return;
    setState(() {
      _restoreParentController.text = path;
      _restoreReport = null;
      _restoreError = null;
    });
  }

  Future<void> _useCreatedBackup(String path) async {
    setState(() {
      _backupPathController.text = path;
      _verificationReport = null;
      _verificationError = null;
      _verifiedBackupPath = null;
      _verifiedBackupFingerprint = null;
      _restoreReport = null;
      _restoreError = null;
    });
    await _verifySelectedBackup();
  }

  Future<void> _verifySelectedBackup() async {
    final backupPath = _backupPathController.text.trim();
    if (_busy || backupPath.isEmpty) return;
    setState(() {
      _verificationRunning = true;
      _verificationError = null;
      _verificationReport = null;
      _verifiedBackupPath = null;
      _verifiedBackupFingerprint = null;
      _restoreReport = null;
      _restoreError = null;
    });
    try {
      if (!await _fileSystem.fileExists(backupPath)) {
        throw const _WorkflowFailure('找不到所选备份文件；请重新选择后再试。');
      }
      final fingerprintBefore = await _fingerprint(backupPath);
      final report = await _runJsonCommand(
        ScribeForgeCli.verifyBackupArguments(backupPath: backupPath),
        operation: '备份验证',
      );
      _validateVerificationReport(report);
      final fingerprintAfter = await _fingerprint(backupPath);
      if (fingerprintBefore != fingerprintAfter) {
        throw const _WorkflowFailure('备份文件在验证过程中发生变化，请重新选择并验证。');
      }
      if (!mounted) return;
      setState(() {
        _verificationReport = report;
        if (report['healthy'] == true) {
          _verifiedBackupPath = backupPath;
          _verifiedBackupFingerprint = fingerprintAfter;
        }
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _verificationError = _errorMessage(error));
    } finally {
      if (mounted) setState(() => _verificationRunning = false);
    }
  }

  Future<void> _confirmRestore() async {
    final backupPath = _backupPathController.text.trim();
    final parent = _restoreParentController.text.trim();
    if (_busy ||
        backupPath.isEmpty ||
        parent.isEmpty ||
        _verifiedBackupPath != backupPath ||
        _verifiedBackupFingerprint == null) {
      return;
    }
    final name = _safeName(
      _lastPathSegment(backupPath).replaceFirst(
        RegExp(r'\.zip$', caseSensitive: false),
        '',
      ),
    );
    final targetPath = ScribeForgeCli.joinPath(
      parent,
      '$name-restored-${_timestamp()}',
    );
    final currentStorePath = _storePath;
    if (currentStorePath != null && _isWithinDirectory(targetPath, currentStorePath)) {
      setState(() {
        _restoreError = '恢复目标不能位于当前项目目录内部；请选择项目目录之外的位置。';
      });
      return;
    }
    final shouldRestore = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        icon: const Icon(Icons.warning_amber_rounded),
        title: const Text('确认恢复到新项目目录？'),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text('恢复前会再次由 CLI 校验归档内容。此操作不会覆盖现有项目。'),
              const SizedBox(height: ScribeSpacing.x4),
              Text(
                '备份来源',
                style: Theme.of(dialogContext)
                    .textTheme
                    .labelLarge
                    ?.copyWith(fontWeight: FontWeight.w600),
              ),
              SelectableText(backupPath),
              const SizedBox(height: ScribeSpacing.x3),
              Text(
                '新项目目录',
                style: Theme.of(dialogContext)
                    .textTheme
                    .labelLarge
                    ?.copyWith(fontWeight: FontWeight.w600),
              ),
              SelectableText(targetPath),
            ],
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          FilledButton.icon(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            icon: const Icon(Icons.restore),
            label: const Text('确认恢复'),
          ),
        ],
      ),
    );
    if (shouldRestore != true || !mounted) return;
    await _restoreVerifiedBackup(
      backupPath: backupPath,
      targetPath: targetPath,
    );
  }

  Future<void> _restoreVerifiedBackup({
    required String backupPath,
    required String targetPath,
  }) async {
    if (_busy) return;
    setState(() {
      _restoreRunning = true;
      _restoreError = null;
      _restoreReport = null;
    });
    try {
      if (await _fileSystem.directoryExists(targetPath)) {
        throw const _WorkflowFailure(
          '恢复目标目录已存在；请重新选择恢复位置以生成一个新目录。',
        );
      }
      final currentFingerprint = await _fingerprint(backupPath);
      if (currentFingerprint != _verifiedBackupFingerprint) {
        throw const _WorkflowFailure(
          '备份文件在验证后发生变化；请重新验证后再恢复。',
        );
      }
      final report = await _runJsonCommand(
        ScribeForgeCli.restoreBackupArguments(
          backupPath: backupPath,
          targetPath: targetPath,
        ),
        operation: '项目恢复',
        acceptNonZeroJsonResult: true,
      );
      _validateRestoreReport(report, expectedStorePath: targetPath);
      if (!mounted) return;
      setState(() => _restoreReport = report);
      if (report['healthy'] == true) {
        await widget.onProjectRestored(targetPath);
      }
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _restoreError = _errorMessage(error));
    } finally {
      if (mounted) setState(() => _restoreRunning = false);
    }
  }

  Future<String> _fingerprint(String path) async =>
      (await sha256.bind(_fileSystem.openRead(path)).first).toString();

  Future<Map<String, dynamic>> _runJsonCommand(
    List<String> arguments, {
    required String operation,
    bool acceptNonZeroJsonResult = false,
  }) async {
    final output = StringBuffer();
    final diagnostics = StringBuffer();
    final exitCode = await widget.cliExecutor.run(
      executable: widget.cliExecutable,
      arguments: arguments,
      onOutput: output.write,
      onError: diagnostics.write,
    );
    final raw = output.toString().trim();
    if (exitCode != 0 &&
        (!acceptNonZeroJsonResult || raw.isEmpty || !raw.startsWith('{'))) {
      final detail = diagnostics.toString().trim().isNotEmpty
          ? diagnostics.toString().trim()
          : raw;
      throw _WorkflowFailure(
        detail.isEmpty ? '$operation失败（退出码 $exitCode）。' : detail,
      );
    }
    if (raw.isEmpty) {
      final detail = diagnostics.toString().trim();
      throw _WorkflowFailure(
        detail.isEmpty ? '$operation没有返回 JSON 结果。' : detail,
      );
    }
    dynamic decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      if (exitCode != 0) {
        final detail = diagnostics.toString().trim();
        throw _WorkflowFailure(
          detail.isEmpty ? '$operation失败（退出码 $exitCode），且未返回有效 JSON。' : detail,
        );
      }
      rethrow;
    }
    if (decoded is! Map) throw _WorkflowFailure('$operation返回的 JSON 不是对象。');
    return Map<String, dynamic>.from(decoded);
  }

  void _validateAuditReport(Map<String, dynamic> report) {
    if (report['schema_version'] != 1 ||
        report['healthy'] is! bool ||
        report['database_readable'] is! bool ||
        report['sqlite_integrity'] is! String ||
        report['foreign_key_violations'] is! int ||
        report['project_count'] is! int ||
        report['page_count'] is! int ||
        report['findings'] is! List) {
      throw const _WorkflowFailure('审计报告格式无效或版本不受支持；未推断或隐藏结果。');
    }
  }

  void _validateBackupReport(Map<String, dynamic> report) {
    if (report['healthy'] is! bool ||
        report['archive'] is! String ||
        report['file_count'] is! int ||
        report['total_bytes'] is! int) {
      throw const _WorkflowFailure('备份报告格式无效；请查看 CLI 诊断并重试。');
    }
  }

  void _validateVerificationReport(Map<String, dynamic> report) {
    if (report['healthy'] is! bool ||
        report['archive'] is! String ||
        report['file_count'] is! int ||
        report['total_bytes'] is! int ||
        report['sqlite_integrity'] is! String ||
        report['foreign_key_violations'] is! int ||
        report['audit_warnings'] is! List) {
      throw const _WorkflowFailure('备份验证报告格式无效或不完整；无法安全恢复。');
    }
  }

  void _validateRestoreReport(
    Map<String, dynamic> report, {
    required String expectedStorePath,
  }) {
    if (report['healthy'] is! bool ||
        report['store'] is! String ||
        report['store'] != expectedStorePath ||
        report['file_count'] is! int ||
        report['total_bytes'] is! int ||
        report['project_count'] is! int ||
        report['page_count'] is! int) {
      throw const _WorkflowFailure('恢复报告未确认目标目录与项目完整性；请检查目标后再试。');
    }
  }

  String _auditSummary(Map<String, dynamic> report) {
    final sqlite = report['sqlite_integrity'];
    final foreignKeys = report['foreign_key_violations'];
    return '数据库${report['database_readable'] == true ? '可读' : '不可读'}'
        ' · SQLite $sqlite · 外键问题 $foreignKeys'
        ' · ${report['project_count']} 个项目 / ${report['page_count']} 页';
  }

  String _backupSummary(Map<String, dynamic> report) =>
      '${report['file_count']} 个文件 · ${_formatBytes(report['total_bytes'] as int)}'
      '${report['created_at'] is String ? ' · ${report['created_at']}' : ''}';

  String _verificationSummary(Map<String, dynamic> report) =>
      '${report['file_count']} 个文件 · ${_formatBytes(report['total_bytes'] as int)}'
      ' · SQLite ${report['sqlite_integrity']}'
      ' · 外键问题 ${report['foreign_key_violations']}'
      '${report['created_at'] is String ? ' · 创建于 ${report['created_at']}' : ''}';

  String _restoreSummary(Map<String, dynamic> report) =>
      '${report['project_count']} 个项目 · ${report['page_count']} 页'
      ' · ${report['file_count']} 个文件 · ${_formatBytes(report['total_bytes'] as int)}';

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }

  String _timestamp() {
    final now = DateTime.now().toUtc();
    String twoDigits(int value) => value.toString().padLeft(2, '0');
    final micros = now.microsecond.toString().padLeft(6, '0');
    return '${now.year}${twoDigits(now.month)}${twoDigits(now.day)}-'
        '${twoDigits(now.hour)}${twoDigits(now.minute)}${twoDigits(now.second)}-'
        '${micros}Z';
  }

  String _errorMessage(Object error) => error is _WorkflowFailure
      ? error.message
      : error.toString().replaceFirst(RegExp(r'^Bad state: '), '');

  String _safeName(String value) {
    final sanitized = value
        .trim()
        .replaceAll(RegExp(r'[<>:"/\\|?*\x00-\x1F]'), '-')
        .replaceAll(RegExp(r'\s+'), '-')
        .replaceAll(RegExp(r'-+'), '-')
        .replaceAll(RegExp(r'^[. -]+|[. -]+$'), '');
    return sanitized.isEmpty ? 'ScribeForge-project' : sanitized;
  }

  bool _isWithinDirectory(String path, String directory) {
    String normalize(String value) {
      final absolute = Directory(value).absolute.path.replaceAll(r'\', '/');
      final trimmed = absolute.length > 1
          ? absolute.replaceFirst(RegExp(r'/+$'), '')
          : absolute;
      return Platform.isWindows ? trimmed.toLowerCase() : trimmed;
    }

    final candidate = normalize(path);
    final root = normalize(directory);
    final prefix = root.endsWith('/') ? root : '$root/';
    return candidate == root || candidate.startsWith(prefix);
  }

  String _lastPathSegment(String path) =>
      path.replaceAll(r'\', '/').split('/').where((part) => part.isNotEmpty).last;
}

class _WorkflowFailure implements Exception {
  const _WorkflowFailure(this.message);

  final String message;

  @override
  String toString() => message;
}
