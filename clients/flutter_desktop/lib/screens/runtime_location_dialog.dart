import 'dart:async';

import 'package:flutter/material.dart';

import '../models/runtime_location.dart';
import '../models/runtime_setup.dart';
import '../models/runtime_status.dart';
import '../services/desktop_file_picker.dart';
import '../services/runtime_location_service.dart';
import '../services/settings_store.dart';

typedef ActivateRuntimeLocation =
    Future<SettingsSaveResult> Function(RuntimeLocation location);

enum _LocationPhase { editing, checking, preparing, saving }

/// Edits a candidate in isolation. Neither the project screen nor the active
/// runtime changes as the user edits paths, checks them, or downloads models.
class RuntimeLocationDialog extends StatefulWidget {
  const RuntimeLocationDialog({
    super.key,
    required this.preferences,
    required this.activeRuntimeRoot,
    required this.filePicker,
    required this.service,
    required this.activate,
  });

  final RuntimeLocationPreferences preferences;
  final String? activeRuntimeRoot;
  final DesktopFilePicker filePicker;
  final RuntimeLocationService service;
  final ActivateRuntimeLocation activate;

  @override
  State<RuntimeLocationDialog> createState() => _RuntimeLocationDialogState();
}

class _RuntimeLocationDialogState extends State<RuntimeLocationDialog> {
  late final TextEditingController _runtime;
  late final TextEditingController _models;
  _LocationPhase _phase = _LocationPhase.editing;
  RuntimeLocationCheck? _checked;
  RuntimeLocationCheck? _verified;
  Object? _error;
  bool _cancelRequested = false;
  bool _cancelled = false;
  bool _choosingDirectory = false;
  final Map<String, RuntimeSetupMessage> _progress =
      <String, RuntimeSetupMessage>{};

  @override
  void initState() {
    super.initState();
    _runtime = TextEditingController(
      text: widget.preferences.active.runtimeRoot,
    );
    _models = TextEditingController(text: widget.preferences.active.modelsRoot);
  }

  @override
  void dispose() {
    _runtime.dispose();
    _models.dispose();
    super.dispose();
  }

  bool get _busy => _phase != _LocationPhase.editing || _choosingDirectory;

  RuntimeLocation get _selection => RuntimeLocation(
    runtimeRoot: _runtime.text.trim(),
    modelsRoot: _models.text.trim(),
  );

  bool get _targetIsActive {
    final checked = _checked;
    return _selection == widget.preferences.active ||
        (checked != null && checked.runtimeRoot == widget.activeRuntimeRoot);
  }

  void _resetReport() {
    _checked = null;
    _verified = null;
    _error = null;
    _cancelled = false;
    _progress.clear();
  }

  void _setCandidate(RuntimeLocation value) {
    if (_busy) return;
    setState(() {
      _runtime.text = value.runtimeRoot;
      _models.text = value.modelsRoot;
      _resetReport();
    });
  }

  Future<void> _chooseDirectory(
    TextEditingController controller,
    String title,
  ) async {
    if (_busy) return;
    setState(() => _choosingDirectory = true);
    try {
      final path = await widget.filePicker.getDirectoryPath(dialogTitle: title);
      if (!mounted || path == null) return;
      setState(() {
        controller.text = path;
        _resetReport();
      });
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _choosingDirectory = false);
    }
  }

  Future<void> _check() async {
    if (_busy) return;
    final selection = _selection;
    setState(() {
      _resetReport();
      _phase = _LocationPhase.checking;
    });
    try {
      final report = await widget.service.check(selection);
      if (mounted) setState(() => _checked = report);
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _phase = _LocationPhase.editing);
    }
  }

  Future<void> _prepare() async {
    final checked = _checked;
    if (_busy ||
        checked == null ||
        !checked.disk.sufficient ||
        _targetIsActive) {
      return;
    }
    final selection = _selection;
    setState(() {
      _phase = _LocationPhase.preparing;
      _verified = null;
      _cancelRequested = false;
      _cancelled = false;
      _error = null;
      _progress.clear();
    });
    try {
      final verified = await widget.service.prepare(
        selection: selection,
        expected: checked,
        activeRuntimeRoot: widget.activeRuntimeRoot,
        isCancelled: () => !mounted || _cancelRequested,
        onProgress: (message) {
          if (mounted) setState(() => _progress[message.backend!] = message);
        },
      );
      if (mounted) setState(() => _verified = verified);
    } on RuntimePreparationCancelled {
      if (mounted) setState(() => _cancelled = true);
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _phase = _LocationPhase.editing);
    }
  }

  Future<void> _cancel() async {
    if (_phase != _LocationPhase.preparing || _cancelRequested) return;
    setState(() => _cancelRequested = true);
    try {
      await widget.service.cancel();
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Future<void> _activate() async {
    final verified = _verified;
    if (_busy || verified == null) return;
    final candidate = verified.canonicalize(_selection);
    setState(() {
      _phase = _LocationPhase.saving;
      _error = null;
    });
    try {
      // Saving can be retried without downloading again, but we still recheck
      // the same paths and ready states in case a removable disk disappeared.
      final latest = await widget.service.check(candidate);
      if (!latest.sameTarget(verified) || !latest.bothReady) {
        if (mounted) setState(() => _verified = null);
        throw const RuntimeLocationCheckFailure(
          'stale_candidate',
          '候选目录或就绪状态已变化，请重新配置并验证。',
        );
      }
      if (!mounted) return;
      final saved = await widget.activate(candidate);
      if (!mounted) return;
      if (!saved.ok) {
        setState(() => _error = '保存失败，仍在使用原配置：${saved.error}');
        return;
      }
      // Pop after allowing the PopScope to rebuild with its unlocked state.
      setState(() => _phase = _LocationPhase.editing);
      Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _phase = _LocationPhase.editing);
    }
  }

  Widget _pathField({
    required TextEditingController controller,
    required String label,
    required String hint,
    required String help,
    required Key buttonKey,
  }) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: TextField(
              controller: controller,
              enabled: !_busy,
              decoration: InputDecoration(labelText: label, hintText: hint),
              onChanged: (_) => setState(_resetReport),
              onSubmitted: (_) => unawaited(_check()),
              autocorrect: false,
            ),
          ),
          const SizedBox(width: 8),
          OutlinedButton.icon(
            key: buttonKey,
            onPressed: _busy ? null : () => _chooseDirectory(controller, label),
            icon: const Icon(Icons.folder_open),
            label: const Text('浏览'),
          ),
        ],
      ),
      const SizedBox(height: 4),
      Text(help, style: Theme.of(context).textTheme.bodySmall),
    ],
  );

  Widget _buildCandidateReport(RuntimeLocationCheck report) {
    final theme = Theme.of(context);
    final disk = report.disk;
    final headline = !disk.sufficient
        ? '空间不足，不能开始配置'
        : report.bothReady
        ? '发现已有环境，切换前仍需运行示例'
        : report.externalModels
        ? '已找到目录，将验证模型并安装缺少的运行依赖'
        : '可以下载并配置所需引擎';
    return Card(
      margin: EdgeInsets.zero,
      color: disk.sufficient
          ? theme.colorScheme.surfaceContainerHigh
          : theme.colorScheme.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(headline, style: theme.textTheme.titleSmall),
            const SizedBox(height: 8),
            SelectableText('运行环境：${report.runtimeRoot}'),
            SelectableText('模型：${report.modelsRoot}'),
            Text(
              '磁盘预估：约需 ${formatBytes(disk.requiredBytes)}，'
              '可用 ${formatBytes(disk.freeBytes)}。',
            ),
            if (!disk.sufficient) Text(disk.advice),
            for (final entry in report.backends.entries)
              Text('${_backendTitle(entry.key)}：${_stateTitle(entry.value)}'),
            if (report.checkedDirectories.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('预检查找到的模型目录'),
                children: <Widget>[
                  for (final path in report.checkedDirectories)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: SelectableText(path),
                    ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildProgress() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      if (_progress.isEmpty && _phase == _LocationPhase.preparing)
        const Text('正在检查目录、准备安装工具…'),
      for (final name in RuntimeLocationCheck.coreBackends)
        if (_progress[name] case final event?) ...<Widget>[
          const SizedBox(height: 6),
          Text(
            '${_backendTitle(name)}：${_progressTitle(event)} '
            '（${event.completedSteps}/${event.totalSteps} 步）',
          ),
          LinearProgressIndicator(value: event.fractionComplete),
        ],
    ],
  );

  static String _backendTitle(String backend) =>
      backend == 'mineru' ? 'MinerU' : 'PaddleOCR';

  static String _stateTitle(String state) => switch (state) {
    'ready' => '已安装',
    'missing' => '待安装',
    'failed' => '待修复',
    'cancelled' => '上次已取消',
    _ => '待核验（$state）',
  };

  static String _progressTitle(RuntimeSetupMessage event) {
    if (event.state == 'ready') return '示例识别通过';
    final phase = switch (event.phase) {
      'install' => '安装运行依赖',
      'models' => '准备模型',
      'health' => event.step == 'sample-ocr' ? '识别内置示例' : '校验环境与模型',
      _ => event.step ?? '处理候选环境',
    };
    return switch (event.state) {
      'failed' => '$phase失败',
      'cancelled' => '$phase已取消',
      _ => phase,
    };
  }

  @override
  Widget build(BuildContext context) {
    final checked = _checked;
    final previous = widget.preferences.previous;
    final theme = Theme.of(context);
    return PopScope(
      canPop: !_busy,
      child: AlertDialog(
        title: const Text('运行环境与模型位置'),
        content: SizedBox(
          width: 620,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text('先检查、再配置、最后切换。应用会继续使用原配置，直到你确认启用新环境。'),
                if (widget.activeRuntimeRoot case final root?) ...<Widget>[
                  const SizedBox(height: 8),
                  SelectableText('当前运行目录：$root'),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  children: <Widget>[
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => _setCandidate(const RuntimeLocation()),
                      child: const Text('填写默认位置'),
                    ),
                    if (previous != null)
                      TextButton(
                        onPressed: _busy ? null : () => _setCandidate(previous),
                        child: const Text('填写上次使用的位置'),
                      ),
                  ],
                ),
                _pathField(
                  controller: _runtime,
                  label: '运行环境目录',
                  hint: '留空使用系统默认目录',
                  help: '可选另一块磁盘。首次设置请选择空目录；该目录存放解释器、依赖和缓存。',
                  buttonKey: const ValueKey('pick-runtime-location'),
                ),
                const SizedBox(height: 12),
                _pathField(
                  controller: _models,
                  label: '已有的模型目录（选填）',
                  hint: '留空由应用下载所需模型',
                  help: '选择已有安装中包含 mineru 和 paddleocr 的 models 目录，不搬迁或删除来源文件。',
                  buttonKey: const ValueKey('pick-existing-model-location'),
                ),
                if (_busy && _phase != _LocationPhase.preparing) ...<Widget>[
                  const SizedBox(height: 12),
                  const LinearProgressIndicator(),
                ],
                if (checked != null) ...<Widget>[
                  const SizedBox(height: 12),
                  _buildCandidateReport(checked),
                  if (_targetIsActive)
                    const Text('这是当前位置。不需要切换；若有故障，请使用现有环境的修复入口。'),
                ],
                _buildProgress(),
                if (_verified != null) ...<Widget>[
                  const SizedBox(height: 12),
                  Text(
                    '两个引擎均已识别示例。可以切换，原目录中的文件会保留。',
                    style: theme.textTheme.titleSmall,
                  ),
                ],
                if (_cancelled) ...<Widget>[
                  const SizedBox(height: 12),
                  const Text('已停止配置，当前环境未切换。再次配置会复用候选目录里已完成的安装。'),
                ],
                if (_error case final error?) ...<Widget>[
                  const SizedBox(height: 12),
                  SelectableText(
                    '$error',
                    style: TextStyle(color: theme.colorScheme.error),
                  ),
                ],
              ],
            ),
          ),
        ),
        actions: <Widget>[
          if (_phase == _LocationPhase.preparing)
            TextButton(
              onPressed: _cancelRequested ? null : _cancel,
              child: Text(_cancelRequested ? '正在取消…' : '取消配置'),
            ),
          TextButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(false),
            child: const Text('关闭'),
          ),
          OutlinedButton(
            onPressed: _busy ? null : _check,
            child: const Text('检查目录'),
          ),
          if (_verified == null)
            FilledButton(
              onPressed:
                  _busy ||
                      _targetIsActive ||
                      checked == null ||
                      !checked.disk.sufficient
                  ? null
                  : _prepare,
              child: Text(checked?.bothReady == true ? '运行示例' : '配置并运行示例'),
            )
          else
            FilledButton(
              onPressed: _busy ? null : _activate,
              child: const Text('启用此环境'),
            ),
        ],
      ),
    );
  }
}
