import 'package:flutter/material.dart';

import '../services/cli_executor.dart';
import '../services/cli_runner.dart';
import '../services/settings_store.dart';

class VlmSettingsDialog extends StatefulWidget {
  const VlmSettingsDialog({
    super.key,
    required this.executable,
    required this.initial,
    this.cliExecutor = const ProcessScribeForgeCliExecutor(),
  });

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
    _apiKeyEnvironmentController =
        TextEditingController(text: initial.apiKeyEnvironmentVariable);
    _timeoutController =
        TextEditingController(text: initial.timeoutSeconds.toString());
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
      throw const FormatException('Timeout must be a number greater than zero.');
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
        throw const FormatException('Enter both an endpoint and a model to test.');
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
      _testOutput = 'Testing the configured model with the selected image…';
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
      append('\n\nExit code: $exitCode');
      if (exitCode == 0) {
        append('\nPreflight succeeded. This output has not been saved as a decision.');
      } else if (exitCode == 1) {
        append('\nThe model response was uncertain or blank; check the sample crop and prompt.');
      } else {
        append('\nPreflight failed. Check endpoint, model, environment token and transport settings.');
      }
    } on Object catch (error) {
      append('\nCould not start VLM preflight: $error');
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
            Text('VLM review settings'),
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
                    title: const Text('Enable VLM review during OCR'),
                    subtitle: const Text(
                      'When enabled, OCR conflicts can be checked by the configured vision model.',
                    ),
                  ),
                  const SizedBox(height: 8),
                  TextFormField(
                    controller: _endpointController,
                    decoration: const InputDecoration(
                      labelText: 'OpenAI-compatible endpoint',
                      hintText: 'http://127.0.0.1:1234/v1',
                      helperText: 'Local loopback endpoints are allowed by default.',
                    ),
                    validator: (value) =>
                        _enabled && (value == null || value.trim().isEmpty)
                            ? 'Enter an endpoint when VLM review is enabled.'
                            : null,
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _modelController,
                    decoration: const InputDecoration(
                      labelText: 'Vision model',
                      hintText: 'local-vision-model',
                    ),
                    validator: (value) =>
                        _enabled && (value == null || value.trim().isEmpty)
                            ? 'Enter a model name when VLM review is enabled.'
                            : null,
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _apiKeyEnvironmentController,
                    decoration: const InputDecoration(
                      labelText: 'API key environment variable (optional)',
                      hintText: 'SCRIBE_VLM_TOKEN',
                      helperText:
                          'Only the variable name is saved. Set the actual secret in the environment before launching the app.',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _timeoutController,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(
                      labelText: 'Request timeout (seconds)',
                    ),
                    validator: (value) {
                      final timeout = double.tryParse(value?.trim() ?? '');
                      if (timeout == null ||
                          !timeout.isFinite ||
                          timeout <= 0) {
                        return 'Enter a timeout greater than zero.';
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
                        : (value) =>
                            setState(() => _allowRemote = value ?? false),
                    title: const Text('Allow remote HTTPS endpoint'),
                    subtitle: const Text(
                      'Only enable this when you intend to send review-crop images to that service. Remote HTTP is refused.',
                    ),
                    controlAffinity: ListTileControlAffinity.leading,
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Preflight sends only the image you explicitly choose. It does not open the project database and never saves the result as accepted text.',
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
                    label: Text(
                      _testing ? 'Testing endpoint…' : 'Test with image…',
                    ),
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
            child: const Text('Cancel'),
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
            child: const Text('Save'),
          ),
        ],
      );
}
