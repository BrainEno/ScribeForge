import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/export_verification.dart';
import '../../services/desktop_file_picker.dart';
import '../../services/export_verification_service.dart';
import '../evidence/ocr_evidence_list.dart';

class ExportVerificationScreen extends StatefulWidget {
  const ExportVerificationScreen({
    super.key,
    required this.service,
    required this.filePicker,
    this.lastOutput,
    this.sourceDigest,
  });

  final ExportVerificationService service;
  final DesktopFilePicker filePicker;
  final String? lastOutput;
  final String? sourceDigest;

  @override
  State<ExportVerificationScreen> createState() =>
      _ExportVerificationScreenState();
}

class _ExportVerificationScreenState extends State<ExportVerificationScreen> {
  String? _path;
  ExportVerificationReport? _report;
  String? _error;
  bool _reading = false;
  bool _picking = false;
  bool _compareSource = false;
  int _generation = 0;

  String? get _knownSource {
    final digest = widget.sourceDigest;
    return digest != null && ExportFileFingerprint.isSha256(digest)
        ? digest
        : null;
  }

  @override
  void initState() {
    super.initState();
    _compareSource = _knownSource != null;
  }

  Future<void> _choose() async {
    if (_picking) return;
    setState(() => _picking = true);
    try {
      final selected = await widget.filePicker.pickFile(
        allowedExtensions: const <String>['epub', 'docx'],
      );
      if (!mounted || selected == null) return;
      await _verify(selected);
    } on Object catch (error) {
      if (mounted) setState(() => _error = '无法选择导出文件：$error');
    } finally {
      if (mounted) setState(() => _picking = false);
    }
  }

  Future<void> _verify(String path) async {
    final version = ++_generation;
    final digest = _compareSource ? _knownSource : null;
    setState(() {
      _path = path;
      _reading = true;
      _error = null;
      _report = null;
    });
    try {
      final report = await widget.service.verify(path, sourceDigest: digest);
      if (mounted && version == _generation) setState(() => _report = report);
    } on Object catch (error) {
      if (mounted && version == _generation) setState(() => _error = '$error');
    } finally {
      if (mounted && version == _generation) setState(() => _reading = false);
    }
  }

  void _setCompare(bool? value) {
    setState(() => _compareSource = value ?? false);
    if (_path case final path?) unawaited(_verify(path));
  }

  Future<void> _copy(ExportVerificationReport report) async {
    var message = '已复制导出校验摘要。';
    try {
      await Clipboard.setData(ClipboardData(text: report.describe()));
    } on PlatformException {
      message = '剪贴板不可用，可手动选择摘要。';
    }
    if (!mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  Widget _fileActions() {
    final path = _path;
    final output = widget.lastOutput;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text(
          '读取成品和同目录下的 .receipt.json、.normalization.json。不会解包、打开或修改图书。',
        ),
        const SizedBox(height: 6),
        const Text('本地凭据不是签名，也不代表项目目前仍可导出。只要成品和凭据被一起替换，本检查就不能证明出处。'),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: <Widget>[
            FilledButton.icon(
              key: const ValueKey('choose-export-to-verify'),
              onPressed: _picking ? null : _choose,
              icon: const Icon(Icons.file_open_outlined),
              label: const Text('选择 EPUB 或 DOCX'),
            ),
            if (output != null && output.isNotEmpty && output != path)
              OutlinedButton.icon(
                onPressed: _reading ? null : () => _verify(output),
                icon: const Icon(Icons.output),
                label: const Text('检查表单中的输出文件'),
              ),
            if (path != null)
              OutlinedButton.icon(
                onPressed: _reading ? null : () => _verify(path),
                icon: const Icon(Icons.refresh),
                label: const Text('重新校验此文件'),
              ),
          ],
        ),
        if (_knownSource != null)
          CheckboxListTile(
            value: _compareSource,
            controlAffinity: ListTileControlAffinity.leading,
            onChanged: _setCompare,
            title: const Text('同时核对当前项目的来源 SHA-256'),
            subtitle: const Text('只比较来源文件指纹，不比较最新OCR 或人工决策。'),
          ),
        if (path != null) SelectableText(path),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final report = _report;
    return Scaffold(
      appBar: AppBar(
        title: const Text('校验导出文件'),
        actions: <Widget>[
          IconButton(
            tooltip: '复制可见校验摘要',
            onPressed: report == null ? null : () => _copy(report),
            icon: const Icon(Icons.copy_outlined),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          _fileActions(),
          const SizedBox(height: 10),
          if (_reading) const LinearProgressIndicator(),
          if (_error case final error?)
            OcrEvidenceError(
              message: error,
              onRetry: () {
                final path = _path;
                if (path == null) {
                  unawaited(_choose());
                } else {
                  unawaited(_verify(path));
                }
              },
            ),
          if (report != null) ...<Widget>[
            _VerificationVerdict(report: report),
            _FingerprintPanel(title: '实测成品指纹', fingerprint: report.actual),
            if (report.receipt case final receipt?)
              _ReceiptPanel(receipt: receipt, actual: report.actual),
            if (report.findings.isNotEmpty) ...<Widget>[
              const SizedBox(height: 12),
              Text('检查明细', style: Theme.of(context).textTheme.titleMedium),
              for (final finding in report.findings)
                _FindingTile(finding: finding),
            ],
          ],
        ],
      ),
    );
  }
}

class _VerificationVerdict extends StatelessWidget {
  const _VerificationVerdict({required this.report});

  final ExportVerificationReport report;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (icon, color) = switch (report.status) {
      ExportVerificationStatus.consistent => (
        Icons.fact_check_outlined,
        theme.colorScheme.secondaryContainer,
      ),
      ExportVerificationStatus.mismatch => (
        Icons.error_outline,
        theme.colorScheme.errorContainer,
      ),
      _ => (Icons.info_outline, theme.colorScheme.surfaceContainerHigh),
    };
    return Card(
      color: color,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(icon),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(report.status.label, style: theme.textTheme.titleMedium),
                  Text(report.status.explanation),
                  if (report.sourceMatches case final matches?)
                    Text(matches ? '凭据所列来源与当前项目来源指纹一致。' : '凭据所列来源与当前项目不同。'),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _FingerprintPanel extends StatelessWidget {
  const _FingerprintPanel({required this.title, required this.fingerprint});

  final String title;
  final ExportFileFingerprint fingerprint;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          Text('${fingerprint.size} 字节'),
          SelectableText('SHA-256：${fingerprint.digest}'),
        ],
      ),
    ),
  );
}

class _ReceiptPanel extends StatelessWidget {
  const _ReceiptPanel({required this.receipt, required this.actual});

  final ExportReceiptDetails receipt;
  final ExportFileFingerprint actual;

  @override
  Widget build(BuildContext context) => Card(
    child: ExpansionTile(
      initiallyExpanded: !receipt.artifact.sameAs(actual),
      title: const Text('导出时写入的凭据信息'),
      subtitle: Text('${receipt.title} · ${receipt.format.toUpperCase()}'),
      expandedCrossAxisAlignment: CrossAxisAlignment.start,
      childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      children: <Widget>[
        Text('原文件名：${receipt.file}'),
        Text('导出时间元数据：${receipt.modified}'),
        Text('物理页 ${receipt.pages} · 已接受配对 ${receipt.acceptedPairs}'),
        Text(
          '严格检查：${receipt.strict ? '是' : '否'} · 保留页边文字：${receipt.furniture ? '是' : '否'}',
        ),
        Text('规范化策略：${receipt.policyLabel} · 项目 #${receipt.projectId}'),
        const Divider(),
        SelectableText(
          '记录的成品：${receipt.artifact.size} 字节 · ${receipt.artifact.digest}',
        ),
        SelectableText(
          '记录的规范化报告：${receipt.normalization.size} 字节 · ${receipt.normalization.digest}',
        ),
        if (receipt.sourceDigest case final source?)
          SelectableText('记录的来源：$source')
        else
          const Text('没有记录可用的来源摘要。'),
      ],
    ),
  );
}

class _FindingTile extends StatelessWidget {
  const _FindingTile({required this.finding});

  final ExportVerificationFinding finding;

  @override
  Widget build(BuildContext context) => ListTile(
    leading: Icon(switch (finding.severity) {
      ExportFindingSeverity.error => Icons.error_outline,
      ExportFindingSeverity.warning => Icons.warning_amber_outlined,
      ExportFindingSeverity.info => Icons.info_outline,
    }),
    title: Text(finding.title),
    subtitle: SelectableText(finding.message),
  );
}
