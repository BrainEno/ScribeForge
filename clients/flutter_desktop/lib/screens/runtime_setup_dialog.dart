import 'package:flutter/material.dart';

import '../models/runtime_setup.dart';
import '../models/runtime_status.dart';

typedef RuntimeSetupRunner = Future<int> Function({
  required String? backend,
  required void Function(RuntimeSetupMessage message) onMessage,
  required void Function(String diagnostic) onDiagnostic,
});

class RuntimeSetupDialog extends StatefulWidget {
  const RuntimeSetupDialog({
    super.key,
    required this.runSetup,
    this.runtimeStatus,
  });

  final RuntimeSetupRunner runSetup;
  final RuntimeStatusSnapshot? runtimeStatus;

  @override
  State<RuntimeSetupDialog> createState() => _RuntimeSetupDialogState();
}

class _RuntimeSetupDialogState extends State<RuntimeSetupDialog> {
  static const List<String> _backendNames = <String>['mineru', 'paddleocr'];

  final Map<String, RuntimeSetupMessage> _latestProgress =
      <String, RuntimeSetupMessage>{};
  final Map<String, String> _stateOverrides = <String, String>{};
  final Map<String, String?> _failedSteps = <String, String?>{};
  final List<String> _activity = <String>[];

  bool _running = false;
  bool _attempted = false;
  String? _activeTarget;
  String? _activeBackend;
  RuntimeSetupMessage? _result;
  int? _exitCode;
  String? _diagnostic;

  Future<void> _startSetup({String? backend}) async {
    if (_running) return;
    setState(() {
      _running = true;
      _attempted = true;
      _activeTarget = backend;
      _activeBackend = backend;
      _result = null;
      _exitCode = null;
      _diagnostic = null;
      _latestProgress.clear();
      _activity.clear();
      final targets = backend == null ? _backendNames : <String>[backend];
      for (final name in targets) {
        _stateOverrides[name] = 'installing';
        _failedSteps.remove(name);
      }
    });

    try {
      final exitCode = await widget.runSetup(
        backend: backend,
        onMessage: _acceptMessage,
        onDiagnostic: _acceptDiagnostic,
      );
      if (!mounted) return;
      setState(() {
        _running = false;
        _exitCode = exitCode;
        if (_result == null) {
          _diagnostic = _joinDiagnostic(
            _diagnostic,
            '命令行工具未返回最终运行时状态；请检查配置与命令行工具路径后重试。',
          );
        } else if (exitCode != 0 && _result!.success == true) {
          _diagnostic = _joinDiagnostic(
            _diagnostic,
            '命令行工具退出码为 $exitCode，与返回的成功状态不一致。',
          );
        }
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _running = false;
        _diagnostic = _joinDiagnostic(_diagnostic, '启动或运行配置失败：$error');
      });
    }
  }

  void _acceptMessage(RuntimeSetupMessage message) {
    if (!mounted) return;
    setState(() {
      if (message.isProgress) {
        final backend = message.backend!;
        _latestProgress[backend] = message;
        _activeBackend = backend;
        if (message.state == 'ready') {
          _stateOverrides[backend] = 'ready';
          _failedSteps.remove(backend);
        } else if (message.state == 'failed') {
          _stateOverrides[backend] = 'failed';
          _failedSteps[backend] = _qualifiedStep(message);
        } else {
          _stateOverrides[backend] = 'installing';
        }
        _addActivity(_formatProgress(message));
      } else {
        _result = message;
        for (final entry in message.backends.entries) {
          _stateOverrides[entry.key] = entry.value.state;
          _failedSteps[entry.key] = entry.value.failedStep;
        }
        final resultText = message.success == true
            ? '配置完成：所有目标后端均已就绪。'
            : '配置未完成：${message.message ?? '请查看失败后端与步骤。'}';
        _addActivity(resultText);
      }
    });
  }

  void _acceptDiagnostic(String diagnostic) {
    if (!mounted || diagnostic.trim().isEmpty) return;
    setState(() {
      _diagnostic = _joinDiagnostic(_diagnostic, diagnostic.trim());
    });
  }

  void _addActivity(String value) {
    if (value.trim().isEmpty) return;
    _activity.add(value);
    if (_activity.length > 12) _activity.removeRange(0, _activity.length - 12);
  }

  String? _qualifiedStep(RuntimeSetupMessage message) {
    final step = message.step;
    if (step == null || step.isEmpty) return message.phase;
    return '${message.phase}:$step';
  }

  String _joinDiagnostic(String? current, String next) {
    final combined = <String>[if (current != null && current.isNotEmpty) current, next]
        .join('\n');
    return combined.length <= 1800
        ? combined
        : combined.substring(combined.length - 1800);
  }

  double get _overallFraction {
    final targets = _activeTarget == null
        ? _backendNames
        : <String>[_activeTarget!];
    if (targets.isEmpty) return 0;
    final total = targets.fold<double>(
      0,
      (sum, backend) => sum + _backendFraction(backend),
    );
    return (total / targets.length).clamp(0.0, 1.0).toDouble();
  }

  double _backendFraction(String backend) {
    final fraction = _latestProgress[backend]?.fractionComplete;
    if (fraction != null) return fraction.clamp(0.0, 1.0).toDouble();
    return _backendState(backend) == 'ready' ? 1 : 0;
  }

  String _backendState(String backend) =>
      _stateOverrides[backend] ??
      widget.runtimeStatus?.backends[backend]?.state ??
      'missing';

  RuntimeSetupMessage? get _activeProgress =>
      _activeBackend == null ? null : _latestProgress[_activeBackend];

  String _stateLabel(String state) => switch (state) {
    'ready' => '就绪',
    'failed' => '失败',
    'installing' => '安装中',
    'models' => '准备模型',
    'health' => '健康检查中',
    'missing' => '未安装',
    _ => '状态未知（$state）',
  };

  String _backendLabel(String backend) => switch (backend) {
    'mineru' => 'MinerU',
    'paddleocr' => 'PaddleOCR',
    _ => backend,
  };

  String _phaseLabel(String? phase) => switch (phase) {
    'install' => '安装 OCR 依赖',
    'models' => '准备模型',
    'health' => '健康检查',
    'complete' => '完成',
    null => '准备中',
    _ => phase,
  };

  String _stepLabel(String? step) => switch (step) {
    'python' => '准备 Python 解释器',
    'environment' => '创建隔离 Python 环境',
    'engine' => '安装 OCR 引擎',
    'install' => '安装 OCR 依赖',
    'download' => '下载模型',
    'standard-models' => '下载 MinerU 标准模型',
    'model-integrity' => '校验 MinerU 模型',
    'runtime-version' => '检查 MinerU 版本',
    'pp-ocrv6-models' => '下载 PaddleOCR 模型',
    'runtime-and-models' => '校验 PaddleOCR 运行环境与模型',
    'verify' => '验证运行环境',
    null => '等待步骤开始',
    _ => step,
  };

  String _formatProgress(RuntimeSetupMessage message) {
    final backend = _backendLabel(message.backend!);
    final phase = _phaseLabel(message.phase);
    final step = message.step == null ? null : _stepLabel(message.step);
    final count = message.totalSteps > 0
        ? '（${message.completedSteps}/${message.totalSteps} 步）'
        : '';
    return switch (message.state) {
      'step_started' => '$backend：开始$step $count'.trim(),
      'step_completed' => '$backend：完成$step $count'.trim(),
      'failed' => '$backend：$phase的${step ?? '当前步骤'}失败',
      'ready' => '$backend：运行时已就绪',
      'started' => '$backend：开始$phase',
      'completed' => '$backend：$phase已完成',
      _ => '$backend：$phase $count'.trim(),
    };
  }

  String _failedStepLabel(String? failedStep) {
    if (failedStep == null || failedStep.isEmpty) return '未知步骤';
    final separator = failedStep.lastIndexOf(':');
    final step = separator < 0 ? failedStep : failedStep.substring(separator + 1);
    return '${_stepLabel(step)}（$failedStep）';
  }

  String _progressDescription(RuntimeSetupMessage? progress) {
    if (!_running) return '';
    if (progress == null) {
      return '正在准备运行时工具，首次配置可能需要一些时间…';
    }
    final backend = _backendLabel(progress.backend!);
    final phase = _phaseLabel(progress.phase);
    final step = progress.step == null ? '' : ' · ${_stepLabel(progress.step)}';
    final count = progress.totalSteps > 0
        ? '（${progress.completedSteps}/${progress.totalSteps} 步已完成）'
        : '';
    return '$backend · $phase$step $count'.trim();
  }

  String _hardwareSummary() {
    final hardware = widget.runtimeStatus?.hardware;
    if (hardware == null || hardware.isEmpty) return '硬件信息暂不可用';
    final gpu = hardware['gpu_name'];
    final ram = hardware['ram_mb'];
    final parts = <String>[
      '系统 ${hardware['os'] ?? '未知'}',
      'GPU ${gpu is String && gpu.isNotEmpty ? gpu : '未检测到'}',
      if (ram is num) '内存 ${(ram / 1024).toStringAsFixed(1)} GB',
    ];
    return parts.join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final runtimeRoot = widget.runtimeStatus?.runtimeRoot;
    return AlertDialog(
      title: const Text('首次 OCR 运行时配置'),
      content: SizedBox(
        width: 640,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 570),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                const Text(
                  'ScribeForge 会在本机配置隔离的 OCR 环境并下载模型。首次安装可能需要较长时间、网络连接和额外磁盘空间；所有后端与步骤都会显示在这里。',
                ),
                const SizedBox(height: 12),
                Text('硬件：${_hardwareSummary()}'),
                if (runtimeRoot != null && runtimeRoot.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  SelectableText('运行目录：$runtimeRoot'),
                ],
                if (widget.runtimeStatus?.uvAvailable == false) ...[
                  const SizedBox(height: 4),
                  const Text('运行时工具尚未安装；配置期间会自动准备所需工具。'),
                ],
                const SizedBox(height: 16),
                if (_running) ...<Widget>[
                  const LinearProgressIndicator(),
                  const SizedBox(height: 6),
                  LinearProgressIndicator(value: _overallFraction),
                  const SizedBox(height: 8),
                  Text(_progressDescription(_activeProgress)),
                  const SizedBox(height: 8),
                ],
                _backendCard('mineru'),
                const SizedBox(height: 8),
                _backendCard('paddleocr'),
                if (_result != null && !_running) ...<Widget>[
                  const SizedBox(height: 12),
                  _resultBanner(),
                ],
                if (_activity.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 16),
                  const Text('最近进度', style: TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 4),
                  for (final activity in _activity.reversed.take(6))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(activity, style: Theme.of(context).textTheme.bodySmall),
                    ),
                ],
                if (_diagnostic != null) ...<Widget>[
                  const SizedBox(height: 12),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: SelectableText(_diagnostic!),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: _running
              ? null
              : () => Navigator.of(context).pop(_attempted),
          child: Text(_attempted ? '完成' : '稍后配置'),
        ),
        FilledButton.icon(
          onPressed: _running ? null : () => _startSetup(),
          icon: _running
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.download),
          label: Text(_running ? '配置中…' : _attempted ? '重新安装全部后端' : '开始安装全部后端'),
        ),
      ],
    );
  }

  Widget _backendCard(String backend) {
    final progress = _latestProgress[backend];
    final state = _backendState(backend);
    final snapshot = widget.runtimeStatus?.backends[backend];
    final failedStep = _failedSteps[backend] ?? snapshot?.failedStep;
    final isTarget = _activeTarget == null || _activeTarget == backend;
    final statusColor = switch (state) {
      'ready' => Colors.green.shade700,
      'failed' => Theme.of(context).colorScheme.error,
      'installing' => Theme.of(context).colorScheme.primary,
      _ => Colors.grey.shade700,
    };

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(
                  state == 'ready'
                      ? Icons.check_circle_outline
                      : state == 'failed'
                      ? Icons.error_outline
                      : Icons.memory,
                  color: statusColor,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _backendLabel(backend),
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
                Text(_stateLabel(state), style: TextStyle(color: statusColor)),
              ],
            ),
            if (snapshot != null && snapshot.environmentPath.isNotEmpty) ...<Widget>[
              const SizedBox(height: 4),
              SelectableText(
                '环境目录：${snapshot.environmentPath}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            if (progress != null) ...<Widget>[
              const SizedBox(height: 8),
              Text('阶段：${_phaseLabel(progress.phase)}'),
              if (progress.step != null)
                Text('当前步骤：${_stepLabel(progress.step)}'),
              if (progress.totalSteps > 0) ...<Widget>[
                const SizedBox(height: 4),
                LinearProgressIndicator(value: progress.fractionComplete),
                const SizedBox(height: 3),
                Text(
                  '已完成 ${progress.completedSteps} / ${progress.totalSteps} 步',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ] else if (_running && isTarget && state == 'installing') ...<Widget>[
              const SizedBox(height: 6),
              Text(
                _activeBackend == backend
                    ? '正在准备该后端…'
                    : '等待开始',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            if (state == 'failed' && failedStep != null) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                '失败步骤：${_failedStepLabel(failedStep)}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                onPressed: _running ? null : () => _startSetup(backend: backend),
                icon: const Icon(Icons.build_outlined),
                label: Text('只修复 ${_backendLabel(backend)}'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _resultBanner() {
    final success = _result?.success == true && _exitCode == 0;
    final colorScheme = Theme.of(context).colorScheme;
    final background = success ? Colors.green.shade50 : colorScheme.errorContainer;
    final foreground = success ? Colors.green.shade900 : colorScheme.onErrorContainer;
    final message = _result?.message;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(success ? Icons.check_circle_outline : Icons.warning_amber_outlined,
              color: foreground),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  success ? '配置完成' : '配置未完成',
                  style: TextStyle(color: foreground, fontWeight: FontWeight.w600),
                ),
                if (message != null && message.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 4),
                  SelectableText(message, style: TextStyle(color: foreground)),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
