import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../services/figure_confirmation_service.dart';
import '../../services/layout_preview_service.dart';

/// Inspection plus the separate human confirmation step for an inline figure.
class LayoutPreviewDialog extends StatefulWidget {
  const LayoutPreviewDialog({
    super.key,
    required this.service,
    required this.page,
    required this.run,
    required this.block,
    required this.blockType,
    this.confirmationService,
  });
  final LayoutPreviewService service;

  /// Injectable so widget tests never touch the real temp directory.
  final FigureConfirmationService? confirmationService;
  final int page, run, block;
  final String blockType;

  @override
  State<LayoutPreviewDialog> createState() => _LayoutPreviewDialogState();
}

class _LayoutPreviewDialogState extends State<LayoutPreviewDialog> {
  bool _loading = false;
  bool _confirming = false;
  String? _error;
  String? _receipt;
  LayoutPreview? _preview;
  final _anchorController = TextEditingController(text: '0');
  final _captionController = TextEditingController();
  final _reasonController = TextEditingController();

  @override
  void dispose() {
    _anchorController.dispose();
    _captionController.dispose();
    _reasonController.dispose();
    super.dispose();
  }

  Future<void> _confirm() async {
    final preview = _preview;
    if (preview == null || _confirming || _loading) return;
    final anchor = int.tryParse(_anchorController.text.trim());
    final reason = _reasonController.text.trim();
    if (anchor == null || anchor < 0 || anchor > preview.alignedPairCount) {
      setState(() => _error = '阅读锚点必须在 0 到 ${preview.alignedPairCount} 之间。');
      return;
    }
    if (reason.isEmpty || reason.length > 1000) {
      setState(() => _error = '请填写 1–1000 字的确认原因。');
      return;
    }
    setState(() {
      _confirming = true;
      _error = null;
      _receipt = null;
    });
    try {
      final service =
          widget.confirmationService ??
          FigureConfirmationService(
            executor: widget.service.executor,
            executable: widget.service.executable,
            storePath: widget.service.storePath,
            projectId: widget.service.projectId,
          );
      final receipt = await service.confirm(
        page: widget.page,
        run: widget.run,
        block: widget.block,
        cropBytes: preview.bytes,
        confirmationToken: preview.confirmationToken,
        anchorPair: anchor,
        reason: reason,
        caption: _captionController.text.trim(),
      );
      if (mounted) {
        setState(
          () => _receipt =
              '已确认插图 #${receipt.id}：物理第 ${receipt.page + 1} 页，'
              '插在第 ${receipt.anchorPair} 对齐段之前。导出 EPUB 时按此阅读顺序嵌入。',
        );
      }
    } on Object catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _confirming = false);
    }
  }

  Future<void> _create() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
      _preview = null;
    });
    try {
      final value = await widget.service.create(
        page: widget.page,
        run: widget.run,
        block: widget.block,
        blockType: widget.blockType,
      );
      if (mounted) setState(() => _preview = value);
    } on Object catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_loading && !_confirming,
    child: AlertDialog(
      title: const Text('检查区域并确认为插图'),
      content: SizedBox(
        width: 640,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                '物理第 ${widget.page + 1} 页 · OCR #${widget.run} · 区域 ${widget.block}（${widget.blockType}）',
              ),
              const SizedBox(height: 8),
              const Text(
                '模型可能漏图、裁掉内容或包含正文。请对照原页。生成预览本身不是人工确认；只有下方“确认为插图”才会把裁切按阅读顺序嵌入 EPUB。',
              ),
              const SizedBox(height: 8),
              const Text(
                '开始后将在系统临时目录生成包含原书内容的 PNG，读取后尝试删除；异常退出可能留下文件。不要在不可信的共享设备上操作。',
              ),
              if (_loading || _confirming) ...[
                const SizedBox(height: 12),
                const LinearProgressIndicator(),
              ],
              if (_error case final error?) ...[
                const SizedBox(height: 12),
                SelectableText(
                  error,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              if (_preview case final result?) ...[
                const SizedBox(height: 12),
                Text(
                  '未经确认 · 最新核验 #${result.verificationId} · ${result.engine}',
                ),
                Text(
                  '原页 ${result.frameSize[0]} × ${result.frameSize[1]}，像素范围 [${result.pixelBox.join(', ')}]（右、下边界不含）',
                ),
                const SizedBox(height: 8),
                const Text('原页概览和模型框（红色）只用于对照，缩略图不代表原始分辨率。可缩放查看。'),
                const SizedBox(height: 8),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final overview = _ImagePane(
                      title: '原页概览 · 模型框',
                      bytes: result.contextBytes,
                      ratio: result.contextSize[0] / result.contextSize[1],
                      frame: result.frameSize,
                      box: result.pixelBox,
                    );
                    final crop = _ImagePane(
                      title: '区域裁切 · 原始像素',
                      bytes: result.bytes,
                      ratio:
                          (result.pixelBox[2] - result.pixelBox[0]) /
                          (result.pixelBox[3] - result.pixelBox[1]),
                    );
                    if (constraints.maxWidth < 500)
                      return Column(children: [overview, crop]);
                    return Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: overview),
                        const SizedBox(width: 12),
                        Expanded(child: crop),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 8),
                SelectableText(
                  '原页 SHA-256：${result.sourceSha256}\n区域 SHA-256：${result.previewSha256}\n概览 SHA-256：${result.contextSha256}',
                ),
                const SizedBox(height: 16),
                Text(
                  '确认为 EPUB 插图',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 4),
                const Text(
                  '仅在裁切内容正确、位置合适时确认。确认会把这份裁切字节按阅读顺序嵌入 EPUB；不会修改 OCR 或决策。',
                ),
                const SizedBox(height: 8),
                TextField(
                  key: const ValueKey('confirm-anchor-pair'),
                  controller: _anchorController,
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(
                    labelText: '阅读锚点（0–${result.alignedPairCount}）',
                    helperText:
                        '插在该对齐段之前；填 ${result.alignedPairCount} 表示本页文字之后。',
                    isDense: true,
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  key: const ValueKey('confirm-caption'),
                  controller: _captionController,
                  maxLength: 1000,
                  decoration: const InputDecoration(
                    labelText: '图题（可选，会成为 figcaption）',
                    isDense: true,
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  key: const ValueKey('confirm-reason'),
                  controller: _reasonController,
                  maxLength: 1000,
                  minLines: 2,
                  maxLines: 4,
                  decoration: const InputDecoration(
                    labelText: '确认原因（必填，1–1000 字）',
                    helperText: '例如：与原页对照，裁切完整、无正文混入。',
                    isDense: true,
                    border: OutlineInputBorder(),
                  ),
                ),
                if (_receipt case final receipt?) ...[
                  const SizedBox(height: 12),
                  SelectableText(
                    receipt,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.primary,
                    ),
                  ),
                ],
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _loading || _confirming
              ? null
              : () => Navigator.of(context).pop(),
          child: const Text('关闭'),
        ),
        FilledButton(
          key: const ValueKey('create-layout-preview'),
          onPressed: _loading || _confirming
              ? null
              : () => unawaited(_create()),
          child: Text(_preview == null ? '生成临时预览' : '重新生成'),
        ),
        if (_preview != null)
          FilledButton.tonal(
            key: const ValueKey('confirm-inline-figure'),
            onPressed: _loading || _confirming
                ? null
                : () => unawaited(_confirm()),
            child: Text(_confirming ? '确认中…' : '确认为插图'),
          ),
      ],
    ),
  );
}

class _ImagePane extends StatelessWidget {
  const _ImagePane({
    required this.title,
    required this.bytes,
    required this.ratio,
    this.frame,
    this.box,
  });
  final String title;
  final Uint8List bytes;
  final double ratio;
  final List<int>? frame, box;
  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(title, style: Theme.of(context).textTheme.titleSmall),
      const SizedBox(height: 4),
      SizedBox(
        height: 280,
        child: InteractiveViewer(
          maxScale: 5,
          child: Center(
            child: AspectRatio(
              aspectRatio: ratio,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  Image.memory(
                    bytes,
                    fit: BoxFit.fill,
                    errorBuilder: (_, _, _) => const Text('无法解码预览图片。'),
                  ),
                  if (frame != null && box != null)
                    CustomPaint(painter: LayoutRegionPainter(frame!, box!)),
                ],
              ),
            ),
          ),
        ),
      ),
    ],
  );
}

/// Draws only on the display layer; the overview PNG remains unannotated.
class LayoutRegionPainter extends CustomPainter {
  const LayoutRegionPainter(this.frame, this.box);
  final List<int> frame, box;
  Rect rectFor(Size size) => Rect.fromLTRB(
    box[0] / frame[0] * size.width,
    box[1] / frame[1] * size.height,
    box[2] / frame[0] * size.width,
    box[3] / frame[1] * size.height,
  );
  @override
  void paint(Canvas canvas, Size size) {
    final rectangle = rectFor(size);
    canvas.drawRect(
      rectangle,
      Paint()
        ..color = Colors.black
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4,
    );
    canvas.drawRect(
      rectangle,
      Paint()
        ..color = Colors.redAccent
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(LayoutRegionPainter oldDelegate) =>
      oldDelegate.frame != frame || oldDelegate.box != box;
}
