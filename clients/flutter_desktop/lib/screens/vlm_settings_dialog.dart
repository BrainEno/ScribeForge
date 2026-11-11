import 'package:flutter/material.dart';

import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/settings_store.dart';

class VlmSettingsDialog extends StatefulWidget {
  VlmSettingsDialog({
    super.key,
    required this.executable,
    required this.initial,
    ScribeForgeCliExecutor? cliExecutor,
  }) : cliExecutor = cliExecutor ?? ProcessScribeForgeCliExecutor();

  final String executable;
  final DesktopVlmSettings initial;
  final ScribeForgeCliExecutor cliExecutor;

  @override
  State<VlmSettingsDialog> createState() => _VlmSettingsDialogState();
}

class _VlmSettingsDialogState extends State<VlmSettingsDialog> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _endpointController;
  late final TextEditingController _modelController;
  late final TextEditingController _apiKeyEnvironmentController;
  late final TextEditingController _timeoutController;
  late bool _enabled;
  late bool _allowRemote;
  bool _testing = false;
  String? _testOutput;
  late final TextEditingController _testImagePathController;

  @override
  void initState() {
    super.initState();
    final initial = widget.initial;
    _endpointController = TextEditingController(text: initial.endpoint);
    _modelController = TextEditingController(text: initial.model);
    _apiKeyEnvironmentController = TextEditingController(
      text: initial.apiKeyEnvironmentVariable,
    );
    _timeoutController = TextEditingController(
      text: initial.timeoutSeconds.toString(),
    );
    _enabled = initial.enabled;
    _allowRemote = initial.allowRemote;
    _testImagePathController = TextEditingController();
  }

  @override
  void dispose() {
    _endpointController.dispose();
    _modelController.dispose();
    _apiKeyEnvironmentController.dispose();
    _timeoutController.dispose();
    _testImagePathController.dispose();
    super.dispose();
  }

  DesktopVlmSettings _settingsFromFields() {
    final timeout = double.tryParse(_timeoutController.text.trim());
    if (timeout == null || !timeout.isFinite || timeout <= 0) {
      throw const FormatException('超时时间必须是大于 0 的数字。');
    }
    return DesktopVlmSettings(
      enabled: _enabled,
      endpoint: _endpointController.text.trim(),
      model: _modelController.text.trim(),
      apiKeyEnvironmentVariable: _apiKeyEnvironmentController.text.trim(),
      timeoutSeconds: timeout,
      allowRemote: _allowRemote,
    );
  }

  Future<void> _testEndpoint() async {
    if (!_formKey.currentState!.validate()) return;
    DesktopVlmSettings settings;
    try {
      settings = _settingsFromFields();
      if (!settings.isConfigured) {
        throw const FormatException('请输入接口地址和模型名称后再测试。');
      }
    } on FormatException catch (error) {
      setState(() => _testOutput = error.message);
      return;
    }

    final imagePath = _testImagePathController.text.trim();
    if (imagePath.isEmpty) {
      setState(() => _testOutput = '请输入测试图片的本地路径。');
      return;
    }

    setState(() {
      _testing = true;
      _testOutput = '正在用所选图片测试模型…';
    });
    final output = StringBuffer();
    void append(String chunk) {
      output.write(chunk);
      if (mounted) setState(() => _testOutput = output.toString());
    }

    try {
      final exitCode = await widget.cliExecutor.run(
        executable: widget.executable,
        arguments: ScribeForgeCli.vlmCheckArguments(
          imagePath: imagePath,
          vlmSettings: settings,
        ),
        onOutput: append,
        onError: append,
      );
      append('\n\n退出码：$exitCode');
      if (exitCode == 0) {
        append('\n预检成功。本次输出不会保存为裁决结果。');
      } else if (exitCode == 1) {
        append('\n模型返回不确定或为空；请检查示例裁切图与提示词。');
      } else {
        append('\n预检失败。请检查接口地址、模型、环境变量令牌与网络设置。');
      }
    } on Object catch (error) {
      append('\n无法启动 VLM 预检：$error');
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Row(
      children: [
        Icon(Icons.visibility_outlined),
        SizedBox(width: 10),
        Text('视觉模型（VLM）复核设置'),
      ],
    ),
    content: SizedBox(
      width: 640,
      child: SingleChildScrollView(
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                value: _enabled,
                onChanged: _testing
                    ? null
                    : (value) => setState(() => _enabled = value),
                title: const Text('在 OCR 时启用 VLM 复核'),
                subtitle: const Text('启用后，OCR 冲突可交由所配置的视觉模型检查。'),
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: _endpointController,
                decoration: const InputDecoration(
                  labelText: 'OpenAI 兼容接口地址',
                  hintText: 'http://127.0.0.1:1234/v1',
                  helperText: '默认允许本机回环地址（127.0.0.1 / localhost）。',
                ),
                validator: (value) =>
                    _enabled && (value == null || value.trim().isEmpty)
                    ? '启用 VLM 复核时必须填写接口地址。'
                    : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _modelController,
                decoration: const InputDecoration(
                  labelText: '视觉模型名称',
                  hintText: 'local-vision-model',
                ),
                validator: (value) =>
                    _enabled && (value == null || value.trim().isEmpty)
                    ? '启用 VLM 复核时必须填写模型名称。'
                    : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _apiKeyEnvironmentController,
                decoration: const InputDecoration(
                  labelText: 'API 密钥环境变量名（可选）',
                  hintText: 'SCRIBE_VLM_TOKEN',
                  helperText: '只保存变量名。真正的密钥请在启动应用前通过环境变量设置，不要写入界面。',
                ),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _timeoutController,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: const InputDecoration(labelText: '请求超时（秒）'),
                validator: (value) {
                  final timeout = double.tryParse(value?.trim() ?? '');
                  if (timeout == null || !timeout.isFinite || timeout <= 0) {
                    return '请输入大于 0 的超时秒数。';
                  }
                  return null;
                },
              ),
              const SizedBox(height: 8),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _allowRemote,
                onChanged: _testing
                    ? null
                    : (value) => setState(() => _allowRemote = value ?? false),
                title: const Text('允许远程 HTTPS 接口'),
                subtitle: const Text(
                  '仅当你确实要把复核裁切图发送到该服务时才启用。纯 HTTP 的远程地址会被拒绝。',
                ),
                controlAffinity: ListTileControlAffinity.leading,
              ),
              const SizedBox(height: 4),
              const Text(
                '预检只发送你明确选择的那张图片，不会打开项目数据库，也不会把结果保存为已采纳文本。',
                style: TextStyle(fontSize: 12, color: Color(0xFF68766F)),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _testImagePathController,
                decoration: const InputDecoration(
                  labelText: '测试图片路径',
                  hintText: '输入本地图片文件的完整路径',
                  prefixIcon: Icon(Icons.image),
                ),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: _testing ? null : _testEndpoint,
                icon: _testing
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.fact_check_outlined),
                label: Text(_testing ? '正在测试接口…' : '用图片测试…'),
              ),
              if (_testOutput != null) ...[
                const SizedBox(height: 8),
                Container(
                  constraints: const BoxConstraints(maxHeight: 150),
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: const Color(0xFFF7F9F8),
                    border: Border.all(color: const Color(0xFFE2E7E4)),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: SingleChildScrollView(
                    child: SelectableText(
                      _testOutput!,
                      style: const TextStyle(
                        fontSize: 11,
                        fontFamily: 'monospace',
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: _testing ? null : () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: _testing
            ? null
            : () {
                if (!_formKey.currentState!.validate()) return;
                try {
                  Navigator.of(context).pop(_settingsFromFields());
                } on FormatException catch (error) {
                  setState(() => _testOutput = error.message);
                }
              },
        child: const Text('保存'),
      ),
    ],
  );
}
