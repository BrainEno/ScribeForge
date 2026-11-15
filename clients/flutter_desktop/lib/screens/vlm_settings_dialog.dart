import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/desktop_file_picker.dart';
import '../services/settings_store.dart';

/// A tiny built-in sample crop (1×1 PNG) so users can run the connectivity
/// probe without hunting for a local image file first (issue #217).
const String _builtInSampleImageBase64 =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

/// Writes the built-in sample crop (1×1 PNG) to a temp file and returns it.
///
/// Used both by the dialog's "内置示例" button and by the post-save
/// connectivity probe, so a probe never needs a hand-picked image (issue #217).
Future<File> writeBuiltInVlmSampleImage() async {
  final directory = await Directory.systemTemp.createTemp(
    'scribeforge-vlm-sample-',
  );
  final file = File('${directory.path}${Platform.pathSeparator}sample.png');
  await file.writeAsBytes(base64Decode(_builtInSampleImageBase64));
  return file;
}

class VlmSettingsDialog extends StatefulWidget {
  VlmSettingsDialog({
    super.key,
    required this.executable,
    required this.initial,
    ScribeForgeCliExecutor? cliExecutor,
    DesktopFilePicker? filePicker,
    Map<String, String>? environment,
    String? operatingSystem,
  }) : cliExecutor = cliExecutor ?? ProcessScribeForgeCliExecutor(),
       filePicker = filePicker ?? const PluginDesktopFilePicker(),
       environment = environment ?? Platform.environment,
       operatingSystem = operatingSystem ?? Platform.operatingSystem;

  final String executable;
  final DesktopVlmSettings initial;
  final ScribeForgeCliExecutor cliExecutor;
  final DesktopFilePicker filePicker;

  /// Environment map used to detect whether the API-key variable is set;
  /// injectable for tests. The app reads process env at launch, so a variable
  /// set later needs an app restart.
  final Map<String, String> environment;
  final String operatingSystem;

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
  bool _confirmRemoteSend = false;
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
      setState(() => _testOutput = '请先选择测试图片（可使用内置示例裁切图）。');
      return;
    }

    // Show exactly what will leave the machine before sending (issue #217):
    // a non-loopback endpoint additionally requires an explicit confirmation.
    final host = Uri.tryParse(settings.endpoint.trim())?.host ?? '';
    if (_isRemoteHost(host) && !_confirmRemoteSend) {
      setState(() {
        _testOutput =
            '本次测试会把图片发送到远程端点 $host。'
            '请先勾选“我确认发送到远程端点”，或改用本机地址。';
      });
      return;
    }

    setState(() {
      _testing = true;
      _testOutput =
          '正在测试：图片 $imagePath → ${settings.endpoint.trim()} '
          '(模型 ${settings.model})…';
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

  static bool _isLoopbackHost(String host) {
    final normalized = host.toLowerCase();
    return normalized == 'localhost' ||
        normalized == '::1' ||
        normalized == '0.0.0.0' ||
        normalized.startsWith('127.');
  }

  bool _isRemoteHost(String host) => host.isNotEmpty && !_isLoopbackHost(host);

  String? get _endpointHost {
    final host = Uri.tryParse(_endpointController.text.trim())?.host ?? '';
    return host.isEmpty ? null : host;
  }

  /// Whether the configured API-key environment variable is present.
  /// Null when no variable name is configured.
  bool? get _apiKeyEnvPresent {
    final name = _apiKeyEnvironmentController.text.trim();
    if (name.isEmpty) return null;
    final value = widget.environment[name];
    return value != null && value.isNotEmpty;
  }

  String _apiKeyGuidance(String name) {
    if (widget.operatingSystem == 'windows') {
      return '未检测到环境变量 $name。可在 PowerShell 中运行 '
              'setx $name "你的密钥"（永久生效），或在当前窗口用 '
              r'$env:' +
          '$name="你的密钥"' +
          '；设置后需要重启本应用才能生效。';
    }
    return '未检测到环境变量 $name。可在终端运行 export $name="你的密钥" '
        '（或写入 ~/.zshrc / ~/.bashrc 永久生效）；设置后需要重启本应用。';
  }

  Future<void> _pickTestImage() async {
    final path = await widget.filePicker.pickFile(
      dialogTitle: '选择测试图片',
      allowedExtensions: const <String>['png', 'jpg', 'jpeg', 'webp', 'bmp'],
    );
    if (!mounted || path == null || path.trim().isEmpty) return;
    setState(() => _testImagePathController.text = path.trim());
  }

  /// Writes the built-in sample crop to a temp file and selects it, so the
  /// connectivity probe can run without a hand-picked image (issue #217).
  Future<void> _useBuiltInSample() async {
    try {
      final file = await writeBuiltInVlmSampleImage();
      if (!mounted) return;
      setState(() => _testImagePathController.text = file.path);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _testOutput = '无法生成内置示例图：$error');
    }
  }

  Widget _buildApiKeyStatus() {
    final present = _apiKeyEnvPresent;
    if (present == null) {
      return const Padding(
        padding: EdgeInsets.only(bottom: 8),
        child: Text(
          '未设置变量名；如果服务需要密钥，请填写环境变量名（例如 SCRIBE_VLM_TOKEN）。',
          style: TextStyle(fontSize: 12, color: Color(0xFF68766F)),
        ),
      );
    }
    final name = _apiKeyEnvironmentController.text.trim();
    if (present) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          children: <Widget>[
            Icon(Icons.check_circle, color: Colors.green.shade700, size: 16),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                '已检测到环境变量 $name（值不会显示也不会保存）。',
                style: TextStyle(fontSize: 12, color: Colors.green.shade800),
              ),
            ),
          ],
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(
            Icons.warning_amber_rounded,
            color: Colors.amber.shade800,
            size: 16,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              _apiKeyGuidance(name),
              style: TextStyle(fontSize: 12, color: Colors.amber.shade900),
            ),
          ),
        ],
      ),
    );
  }

  /// What exactly will be sent by the probe: file, endpoint host and model.
  /// A remote host adds an explicit confirmation checkbox (issue #217).
  Widget _buildSendSummary() {
    final host = _endpointHost;
    final image = _testImagePathController.text.trim();
    final model = _modelController.text.trim();
    final children = <Widget>[
      Text(
        '测试将发送：${image.isEmpty ? '（未选择图片）' : image} → '
        '${host == null ? '（未填写接口地址）' : host}，模型 ${model.isEmpty ? '（未填写）' : model}。',
        style: const TextStyle(fontSize: 12, color: Color(0xFF68766F)),
      ),
    ];
    if (host != null && _isRemoteHost(host)) {
      children.addAll(<Widget>[
        const SizedBox(height: 4),
        CheckboxListTile(
          key: const ValueKey('confirm-remote-vlm-send-checkbox'),
          contentPadding: EdgeInsets.zero,
          value: _confirmRemoteSend,
          onChanged: _testing
              ? null
              : (value) => setState(() => _confirmRemoteSend = value ?? false),
          title: Text('我确认将测试图片发送到远程端点 $host'),
          controlAffinity: ListTileControlAffinity.leading,
        ),
      ]);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
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
                  helperText: '只保存变量名，不会保存密钥本身。',
                ),
                onChanged: (_) => setState(() {}),
              ),
              _buildApiKeyStatus(),
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
              Row(
                children: <Widget>[
                  Expanded(
                    child: TextField(
                      controller: _testImagePathController,
                      decoration: const InputDecoration(
                        labelText: '测试图片路径',
                        hintText: '选择图片，或使用内置示例裁切图',
                        prefixIcon: Icon(Icons.image),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    key: const ValueKey('pick-vlm-test-image-button'),
                    onPressed: _testing ? null : _pickTestImage,
                    icon: const Icon(Icons.folder_open, size: 16),
                    label: const Text('选择文件'),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    key: const ValueKey('use-builtin-vlm-sample-button'),
                    onPressed: _testing ? null : _useBuiltInSample,
                    icon: const Icon(Icons.photo_library_outlined, size: 16),
                    label: const Text('内置示例'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              _buildSendSummary(),
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
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    key: const ValueKey('copy-vlm-test-output-button'),
                    onPressed: () =>
                        Clipboard.setData(ClipboardData(text: _testOutput!)),
                    icon: const Icon(Icons.copy, size: 16),
                    label: const Text('复制结果'),
                  ),
                ),
                const SizedBox(height: 4),
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
