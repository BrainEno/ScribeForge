import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../../models/textless_confirmation.dart';
import '../../services/textless_confirmation_service.dart';
import '../evidence/ocr_evidence_list.dart';

class TextlessConfirmationScreen extends StatefulWidget {
  const TextlessConfirmationScreen({super.key, required this.service});

  final TextlessConfirmationService service;

  @override
  State<TextlessConfirmationScreen> createState() =>
      _TextlessConfirmationScreenState();
}

class _TextlessConfirmationScreenState
    extends State<TextlessConfirmationScreen> {
  final _form = GlobalKey<FormState>();
  final _reason = TextEditingController();
  final _zoom = TransformationController();
  TextlessPreview? _preview;
  TextlessPageKind? _kind;
  TextlessReceipt? _receipt;
  ui.Image? _image;
  String? _readError;
  String? _writeError;
  bool _loading = false;
  bool _saving = false;
  bool _inspected = false;
  bool _needsReload = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _reason.dispose();
    _zoom.dispose();
    _image?.dispose();
    super.dispose();
  }

  Future<ui.Image> _decode(TextlessPreview preview) async {
    final codec = await ui.instantiateImageCodec(preview.raster);
    try {
      if (codec.frameCount != 1) throw const FormatException('原页不是单帧图像。');
      final frame = await codec.getNextFrame();
      if (frame.image.width != preview.width ||
          frame.image.height != preview.height) {
        frame.image.dispose();
        throw const FormatException('原页显示尺寸与证据记录不符。');
      }
      return frame.image;
    } finally {
      codec.dispose();
    }
  }

  Future<void> _load() async {
    if (_loading || _saving || _receipt != null) return;
    final oldImage = _image;
    setState(() {
      _loading = true;
      _preview = null;
      _image = null;
      _readError = null;
      _writeError = null;
      _inspected = false;
      _kind = null;
      _needsReload = false;
    });
    _zoom.value = Matrix4.identity();
    if (oldImage != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) => oldImage.dispose());
    }
    try {
      final preview = await widget.service.preview();
      if (!mounted) return;
      final image = await _decode(preview);
      if (!mounted) {
        image.dispose();
        return;
      }
      setState(() {
        _preview = preview;
        _image = image;
      });
    } on Object catch (error) {
      if (mounted) setState(() => _readError = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  bool get _canSubmit =>
      !_loading &&
      !_saving &&
      !_needsReload &&
      _receipt == null &&
      _preview?.token != null &&
      _image != null &&
      _inspected &&
      _kind != null;

  String? _validateReason(String? value) {
    final reason = value?.trim() ?? '';
    if (reason.isEmpty) return '请写明为什么这一页没有待转录文字。';
    if (reason.contains('\u0000') ||
        reason.runes.length > TextlessPreview.maximumReasonLength) {
      return '理由须在 1000 个字符以内，且不能包含空字符。';
    }
    return null;
  }

  Future<void> _submit() async {
    if (!_canSubmit || _form.currentState?.validate() != true) return;
    final preview = _preview!;
    final kind = _kind!;
    final reason = _reason.text.trim();
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        scrollable: true,
        title: const Text('记录无文字页确认？'),
        content: Text(
          '第 ${preview.page + 1} 页 · ${kind.label}\n核验 #${preview.verification}\n\n$reason\n\n这条确认会追加到项目历史，不会改写既有 OCR 或决策。',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('继续核对'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('确认写入记录'),
          ),
        ],
      ),
    );
    if (!mounted ||
        approved != true ||
        !_canSubmit ||
        !identical(_preview, preview))
      return;
    setState(() {
      _saving = true;
      _writeError = null;
    });
    try {
      final receipt = await widget.service.confirm(preview, kind, reason);
      if (mounted) setState(() => _receipt = receipt);
    } on Object catch (error) {
      if (mounted)
        setState(() {
          _needsReload = true;
          _inspected = false;
          _writeError = '无法核实本次写入结果。请刷新原页及历史，不要重复提交。\n$error';
        });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _changeZoom(double factor) {
    final scale = _zoom.value.getMaxScaleOnAxis();
    final target = (scale * factor).clamp(1.0, 8.0);
    _zoom.value = target <= 1
        ? Matrix4.identity()
        : (_zoom.value.clone()
            ..scaleByDouble(target / scale, target / scale, 1, 1));
  }

  Widget _raster(TextlessPreview preview, ui.Image image) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      Text(
        '原页 · ${preview.width} × ${preview.height} 像素 · 核验 #${preview.verification}',
      ),
      const Text('请放大检查边缘、小字、印章和题注。OCR 没有结果不表示空白。'),
      Wrap(
        children: <Widget>[
          IconButton(
            tooltip: '缩小原页',
            onPressed: () => _changeZoom(1 / 1.5),
            icon: const Icon(Icons.zoom_out),
          ),
          IconButton(
            tooltip: '放大原页',
            onPressed: () => _changeZoom(1.5),
            icon: const Icon(Icons.zoom_in),
          ),
          TextButton(
            onPressed: () => _zoom.value = Matrix4.identity(),
            child: const Text('适应窗口'),
          ),
        ],
      ),
      SizedBox(
        height: 400,
        width: double.infinity,
        child: ColoredBox(
          color: Colors.black87,
          child: InteractiveViewer(
            transformationController: _zoom,
            maxScale: 8,
            child: RawImage(
              image: image,
              fit: BoxFit.contain,
              filterQuality: FilterQuality.medium,
            ),
          ),
        ),
      ),
      const SizedBox(height: 8),
      SelectableText('已核对页图 SHA-256：${preview.digest}'),
    ],
  );

  Widget _editor() => Form(
    key: _form,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const Divider(),
        const Text('如果能看到漏读文字，请返回重新处理；不要把这页标成无文字。'),
        CheckboxListTile(
          controlAffinity: ListTileControlAffinity.leading,
          value: _inspected,
          onChanged: _saving || _needsReload
              ? null
              : (value) => setState(() => _inspected = value ?? false),
          title: const Text('我已查看原页，确认没有待转录文字'),
        ),
        DropdownButtonFormField<TextlessPageKind>(
          initialValue: _kind,
          isExpanded: true,
          decoration: const InputDecoration(labelText: '选择页类型'),
          items: <DropdownMenuItem<TextlessPageKind>>[
            for (final kind in TextlessPageKind.values)
              DropdownMenuItem(value: kind, child: Text(kind.label)),
          ],
          onChanged: _saving || _needsReload
              ? null
              : (kind) => setState(() => _kind = kind),
        ),
        if (_kind case final kind?) Text(kind.description),
        const SizedBox(height: 10),
        TextFormField(
          key: const ValueKey('textless-confirmation-reason'),
          controller: _reason,
          enabled: !_saving,
          validator: _validateReason,
          minLines: 2,
          maxLines: 5,
          decoration: const InputDecoration(
            labelText: '确认理由（必填）',
            hintText: '描述你在原页看到的内容',
          ),
        ),
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.icon(
            key: const ValueKey('textless-confirmation-submit'),
            onPressed: _canSubmit ? _submit : null,
            icon: const Icon(Icons.draw_outlined),
            label: const Text('核对并记录'),
          ),
        ),
      ],
    ),
  );

  Widget _history(TextlessPreview preview) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: <Widget>[
      const SizedBox(height: 12),
      Text('此页确认历史', style: Theme.of(context).textTheme.titleMedium),
      if (preview.history.isEmpty) const Text('尚未保存过此页的无文字确认。'),
      for (final entry in preview.history)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  '#${entry.id} · ${entry.kind.label} · ${entry.actor == 'human' ? '人工' : '证据测量'}',
                ),
                Text('${entry.createdAt} · ${entry.bindingLabel}'),
                if (entry.verification != null)
                  Text('核验 #${entry.verification}'),
                SelectableText(entry.reason),
                if (entry.reasonTruncated) const Text('理由仅显示节选。'),
              ],
            ),
          ),
        ),
      if (preview.truncated) const Text('只显示最近 20 条记录。'),
    ],
  );

  @override
  Widget build(BuildContext context) {
    final preview = _preview;
    final image = _image;
    final receipt = _receipt;
    return PopScope(
      canPop: !_saving,
      child: Scaffold(
        appBar: AppBar(
          title: Text('第 ${widget.service.page + 1} 页 · 无文字确认'),
          actions: <Widget>[
            IconButton(
              tooltip: '重新读取页图与历史',
              onPressed: _loading || _saving || receipt != null ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            if (_loading || _saving) const LinearProgressIndicator(),
            if (_readError case final error?)
              OcrEvidenceError(message: error, onRetry: _load),
            if (_writeError case final error?)
              OcrEvidenceError(message: error, onRetry: _load),
            if (receipt != null) ...<Widget>[
              Text('已记录确认 #${receipt.id} · ${receipt.kind.label}'),
              TextButton(
                onPressed: () => Navigator.of(context).pop(true),
                child: const Text('返回导出页'),
              ),
            ],
            if (preview != null && image != null) ...<Widget>[
              _raster(preview, image),
              if (preview.blockedReason case final reason?)
                SelectableText('不能提交此页：$reason'),
              if (preview.token != null && receipt == null) _editor(),
              _history(preview),
            ],
          ],
        ),
      ),
    );
  }
}
