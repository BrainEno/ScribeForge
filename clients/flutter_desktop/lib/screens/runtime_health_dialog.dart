import 'package:flutter/material.dart';

import '../models/runtime_health.dart';
import '../services/cli_executor.dart';
import '../services/cli_runner.dart';

/// Explicit sample check: never takes a book path and never installs a model.
class RuntimeHealthDialog extends StatefulWidget {
  const RuntimeHealthDialog({
    super.key,
    required this.executor,
    required this.executable,
  });
  final ScribeForgeCliExecutor executor;
  final String executable;

  @override
  State<RuntimeHealthDialog> createState() => _RuntimeHealthDialogState();
}

class _RuntimeHealthDialogState extends State<RuntimeHealthDialog> {
  bool running = false;
  bool cancelling = false;
  String? current;
  String? error;
  RuntimeHealthReport? report;

  Future<void> _start() async {
    if (running) return;
    setState(() {
      running = true;
      cancelling = false;
      current = null;
      error = null;
      report = null;
    });
    final stream = RuntimeHealthStream((name) {
      if (mounted)
        setState(() => current = name == 'mineru' ? 'MinerU' : 'PaddleOCR');
    });
    var diagnostics = '';
    Object? parseError;
    try {
      final code = await widget.executor.run(
        executable: widget.executable,
        arguments: ScribeForgeCli.runtimeCheckArguments(),
        onOutput: (chunk) {
          if (parseError != null) return;
          try {
            stream.add(chunk);
          } on Object catch (exc) {
            parseError = exc;
          }
        },
        onError: (chunk) {
          if (diagnostics.length < 4000) {
            diagnostics += chunk.substring(
              0,
              chunk.length.clamp(0, 4000 - diagnostics.length),
            );
          }
        },
      );
      if (parseError != null) throw parseError!;
      stream.finish();
      final result = stream.report!;
      if ((code == 0) != result.success || (code == 130) != result.cancelled) {
        throw const FormatException('检查退出码与报告不一致');
      }
      if (mounted) setState(() => report = result);
    } on Object catch (exc) {
      if (mounted)
        setState(
          () => error =
              '无法完成检查：$exc${diagnostics.trim().isEmpty ? '' : '\n${diagnostics.trim()}'}',
        );
    } finally {
      if (mounted)
        setState(() {
          running = false;
          cancelling = false;
          current = null;
        });
    }
  }

  Future<void> _cancel() async {
    if (!running || cancelling) return;
    setState(() => cancelling = true);
    try {
      await widget.executor.cancel();
    } on Object catch (exc) {
      if (mounted)
        setState(() {
          cancelling = false;
          error = '取消请求失败：$exc';
        });
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !running,
    child: AlertDialog(
      title: const Text('测试 OCR 环境'),
      content: SizedBox(
        width: 620,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '使用内置的英文示例图片分别运行 MinerU 和 PaddleOCR，不读取或修改你的书籍，也不会安装模型。检查可能需要几分钟并占用内存或显存。',
              ),
              const SizedBox(height: 8),
              const Text(
                '会使用临时文件和模型缓存，并请求离线模式；第三方组件并非由系统网络隔离。通过只证明此刻示例可运行，不代表整本书或中文识别准确。',
              ),
              if (running) ...[
                const SizedBox(height: 16),
                const LinearProgressIndicator(),
                const SizedBox(height: 8),
                Text(cancelling ? '正在取消，请稍候…' : '正在检查 ${current ?? '运行环境'}…'),
              ],
              if (report != null) ...[
                const SizedBox(height: 16),
                Text(
                  report!.cancelled
                      ? '检查已取消'
                      : report!.success
                      ? '两个引擎的示例识别均通过'
                      : '检查未全部通过',
                  key: const ValueKey('runtime-health-result'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                for (final item in report!.items) ...[
                  const SizedBox(height: 8),
                  Text('${item.label}：${item.stateLabel}'),
                  if (item.detail != null) SelectableText(item.detail!),
                ],
                if (report!.error != null) SelectableText(report!.error!),
                if (!report!.success && !report!.cancelled)
                  const Text('可返回环境设置重新检测，或选择对应引擎进行修复。分享诊断前请检查是否含有私人信息。'),
              ],
              if (error != null) ...[
                const SizedBox(height: 12),
                SelectableText(error!),
              ],
            ],
          ),
        ),
      ),
      actions: [
        if (running)
          TextButton(
            onPressed: cancelling ? null : _cancel,
            child: Text(cancelling ? '正在取消…' : '取消检查'),
          ),
        if (!running)
          TextButton(
            onPressed: () => Navigator.pop(context, report),
            child: const Text('关闭'),
          ),
        if (!running)
          FilledButton(
            onPressed: _start,
            child: Text(report == null && error == null ? '开始示例检查' : '再次检查'),
          ),
      ],
    ),
  );
}
