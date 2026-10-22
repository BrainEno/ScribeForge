import 'dart:async';

import 'package:flutter/material.dart';

import '../models/runtime_status.dart';
import '../services/cli_runner.dart';

class RuntimeModelsDialog extends StatefulWidget {
  const RuntimeModelsDialog({
    super.key,
    required this.executable,
    required this.onOpenSetup,
  });

  final String executable;
  final Future<void> Function() onOpenSetup;

  @override
  State<RuntimeModelsDialog> createState() => _RuntimeModelsDialogState();
}

class _RuntimeModelsDialogState extends State<RuntimeModelsDialog> {
  RuntimeStatusSnapshot? _snapshot;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    unawaited(_refresh());
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final stdout = StringBuffer();
    final stderr = StringBuffer();
    try {
      final exitCode = await ScribeForgeCli.run(
        executable: widget.executable,
        arguments: ScribeForgeCli.runtimeStatusArguments(),
        onOutput: stdout.write,
        onError: stderr.write,
      );
      if (exitCode != 0) {
        final detail = stderr.toString().trim();
        throw StateError(
          detail.isEmpty
              ? 'Runtime status command exited with code $exitCode.'
              : detail,
        );
      }
      final snapshot = RuntimeStatusSnapshot.fromJsonText(stdout.toString());
      if (!mounted) return;
      setState(() {
        _snapshot = snapshot;
        _error = null;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _openSetup() async {
    Navigator.of(context).pop();
    await widget.onOpenSetup();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.memory_outlined),
            SizedBox(width: 10),
            Text('Runtime & Models'),
          ],
        ),
        content: SizedBox(
          width: 610,
          child: SingleChildScrollView(
            child: _content(),
          ),
        ),
        actions: [
          TextButton.icon(
            onPressed: _loading ? null : _refresh,
            icon: _loading
                ? const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
            label: const Text('Refresh status'),
          ),
          OutlinedButton.icon(
            onPressed: _openSetup,
            icon: const Icon(Icons.build_outlined),
            label: const Text('Install / repair…'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      );

  Widget _content() {
    final snapshot = _snapshot;
    if (snapshot == null) {
      if (_loading) {
        return const Padding(
          padding: EdgeInsets.symmetric(vertical: 36),
          child: Center(child: CircularProgressIndicator()),
        );
      }
      return Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Could not read the runtime status.'),
          const SizedBox(height: 8),
          SelectableText(_error ?? 'Unknown error'),
          const SizedBox(height: 8),
          const Text(
            'Check the CLI executable in Settings, then refresh or open the setup wizard.',
          ),
        ],
      );
    }

    final hardware = snapshot.hardware;
    final paths = snapshot.paths;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _sectionTitle('Hardware'),
        _detailRow('System', _joinNonEmpty([
          hardware['os']?.toString(),
          hardware['architecture']?.toString(),
        ])),
        _detailRow('GPU', _display(hardware['gpu_name'])),
        _detailRow('NVIDIA driver', _display(hardware['nvidia_driver'])),
        _detailRow('VRAM', _memory(hardware['vram_mb'])),
        _detailRow('System RAM', _memory(hardware['ram_mb'])),
        const SizedBox(height: 12),
        _sectionTitle('Managed OCR backends'),
        for (final name in const ['mineru', 'paddleocr'])
          _backendTile(name, snapshot.backends[name]),
        if (_loading)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: LinearProgressIndicator(minHeight: 2),
          ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Text(
            'Refresh failed: $_error',
            style: const TextStyle(color: Color(0xFF9A4E12), fontSize: 12),
          ),
        ],
        const SizedBox(height: 12),
        _sectionTitle('Runtime locations'),
        _detailRow('Private runtime root', snapshot.runtimeRoot),
        _detailRow('uv bootstrap', snapshot.uvAvailable ? 'Available' : 'Not found'),
        _detailRow('Model directory', _path(paths, 'models')),
        _detailRow('Cache directory', _path(paths, 'cache')),
        _detailRow('Tools directory', _path(paths, 'tools')),
        const SizedBox(height: 8),
        const Text(
          'Status inspection is read-only. Install / repair opens the setup wizard; '
          'it does not remove project data or review evidence.',
          style: TextStyle(fontSize: 11, color: Color(0xFF68766F)),
        ),
      ],
    );
  }

  Widget _sectionTitle(String title) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Text(
          title.toUpperCase(),
          style: const TextStyle(
            fontSize: 10,
            letterSpacing: 1.1,
            fontWeight: FontWeight.w800,
            color: Color(0xFF718079),
          ),
        ),
      );

  Widget _detailRow(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 5),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 135,
              child: Text(
                label,
                style: const TextStyle(fontSize: 12, color: Color(0xFF68766F)),
              ),
            ),
            Expanded(
              child: SelectableText(
                value,
                style: const TextStyle(fontSize: 12),
              ),
            ),
          ],
        ),
      );

  Widget _backendTile(String name, RuntimeBackendSnapshot? backend) {
    final reportedState = backend?.state ?? 'unknown';
    final folderMissing = backend != null && !backend.environmentExists;
    final state = reportedState == 'ready' && folderMissing
        ? 'missing-folder'
        : reportedState;
    final statusLabel = state == 'missing-folder'
        ? 'READY STATE · FOLDER MISSING'
        : state.toUpperCase();
    final color = switch (state) {
      'ready' => const Color(0xFF237354),
      'failed' => const Color(0xFFAE5425),
      'installing' || 'models' || 'health' || 'missing-folder' =>
        const Color(0xFF94601A),
      _ => const Color(0xFF68766F),
    };
    final icon = switch (state) {
      'ready' => Icons.check_circle_outline,
      'failed' => Icons.error_outline,
      'installing' || 'models' || 'health' => Icons.downloading_outlined,
      'missing-folder' => Icons.warning_amber_outlined,
      _ => Icons.radio_button_unchecked,
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 7),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        border: Border.all(color: const Color(0xFFE2E7E4)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 19),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  name == 'mineru' ? 'MinerU' : 'PaddleOCR',
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
                ),
                Text(
                  backend?.environmentPath ?? 'No environment path reported',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 10, color: Color(0xFF68766F)),
                ),
                if (backend != null)
                  Text(
                    folderMissing
                        ? 'Environment directory not found'
                        : 'Environment directory exists',
                    style: TextStyle(
                      fontSize: 10,
                      color: folderMissing
                          ? const Color(0xFF94601A)
                          : const Color(0xFF68766F),
                    ),
                  ),
                if (backend?.failedStep != null)
                  Text(
                    'Failed step: ${backend!.failedStep}',
                    style: const TextStyle(fontSize: 10, color: Color(0xFFAE5425)),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(
            statusLabel,
            style: TextStyle(
              color: color,
              fontSize: 10,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }

  static String _display(Object? value) =>
      value is String && value.trim().isNotEmpty ? value : 'Not detected';

  static String _memory(Object? value) => value is num
      ? '${(value / 1024).toStringAsFixed(1)} GiB'
      : 'Unknown';

  static String _joinNonEmpty(List<String?> values) =>
      values.whereType<String>().where((value) => value.isNotEmpty).join(' · ');

  static String _path(Map<String, dynamic> paths, String name) {
    final value = paths[name];
    final exists = paths['${name}_exists'] == true;
    if (value is! String || value.isEmpty) return 'Not reported';
    return exists ? value : '$value (not created yet)';
  }
}
