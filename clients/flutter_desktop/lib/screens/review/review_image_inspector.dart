import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/review_queue.dart';

Future<void> showReviewImageInspector(
  BuildContext context,
  ReviewQueueItem item,
) {
  return showDialog<void>(
    context: context,
    builder: (_) => _ReviewImageInspector(item: item),
  );
}

class _EvidenceImage {
  const _EvidenceImage(this.label, this.path, this.description);

  final String label;
  final String path;
  final String description;
}

class _ReviewImageInspector extends StatefulWidget {
  const _ReviewImageInspector({required this.item});

  final ReviewQueueItem item;

  @override
  State<_ReviewImageInspector> createState() => _ReviewImageInspectorState();
}

class _ReviewImageInspectorState extends State<_ReviewImageInspector> {
  late final List<_EvidenceImage> _images;
  late int _left;
  int _right = 0;
  bool _comparing = false;
  var _activePane = 0;
  final _leftTransform = TransformationController();
  final _rightTransform = TransformationController();

  @override
  void initState() {
    super.initState();
    final item = widget.item;
    _images = <_EvidenceImage>[
      if (item.sourceImagePath.isNotEmpty)
        _EvidenceImage('原始页面', item.sourceImagePath, '来源扫描页'),
      if (item.coordinateFrame == 'preprocessed' &&
          item.ocrFrameImagePath?.isNotEmpty == true)
        _EvidenceImage('OCR 坐标帧', item.ocrFrameImagePath!, '引擎使用的预处理图像，不是原图'),
      if (item.cropPath?.isNotEmpty == true)
        _EvidenceImage('候选区域', item.cropPath!, '随证据保存的局部图像'),
    ];
    _left = _images.isEmpty ? 0 : _images.length - 1;
  }

  @override
  void dispose() {
    _leftTransform.dispose();
    _rightTransform.dispose();
    super.dispose();
  }

  TransformationController get _transform =>
      _activePane == 0 ? _leftTransform : _rightTransform;

  void _zoom(double factor) {
    final transform = _transform;
    final current = transform.value.getMaxScaleOnAxis();
    final next = (current * factor).clamp(1.0, 6.0);
    if (next <= 1) {
      transform.value = transform.value.clone()..setIdentity();
    } else {
      final change = next / current;
      transform.value = transform.value.clone()
        ..scaleByDouble(change, change, 1.0, 1.0);
    }
  }

  void _reset() {
    _transform.value = _transform.value.clone()..setIdentity();
  }

  void _selectFrame(int pane, int index) {
    setState(() {
      _activePane = pane;
      if (pane == 0) {
        _left = index;
      } else {
        _right = index;
      }
      _reset();
    });
  }

  void _toggleComparison() {
    setState(() {
      _comparing = !_comparing;
      _activePane = 0;
      if (_comparing && _right == _left) {
        _right = (_left + 1) % _images.length;
        _rightTransform.value = _rightTransform.value.clone()..setIdentity();
      }
    });
  }

  Widget _buildToolbar() => Wrap(
    spacing: 8,
    runSpacing: 6,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: <Widget>[
      if (_images.length > 1)
        FilterChip(
          label: const Text('同时查看两个图像'),
          selected: _comparing,
          avatar: const Icon(Icons.compare_outlined),
          onSelected: (_) => _toggleComparison(),
        ),
      if (_comparing) Text(_activePane == 0 ? '缩放：图像 A' : '缩放：图像 B'),
      IconButton(
        tooltip: '缩小',
        onPressed: () => _zoom(0.8),
        icon: const Icon(Icons.zoom_out),
      ),
      IconButton(
        tooltip: '放大',
        onPressed: () => _zoom(1.25),
        icon: const Icon(Icons.zoom_in),
      ),
      IconButton(
        tooltip: '重置缩放',
        onPressed: _reset,
        icon: const Icon(Icons.fit_screen_outlined),
      ),
      ValueListenableBuilder(
        valueListenable: _transform,
        builder: (context, matrix, child) =>
            Text('${(matrix.getMaxScaleOnAxis() * 100).round()}%'),
      ),
    ],
  );

  Widget _buildPane(int pane) {
    final selected = pane == 0 ? _left : _right;
    final image = _images[selected];
    final transform = pane == 0 ? _leftTransform : _rightTransform;
    final theme = Theme.of(context);
    final border = _comparing && pane == _activePane
        ? theme.colorScheme.primary
        : theme.colorScheme.outlineVariant;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        DropdownButton<int>(
          value: selected,
          isExpanded: true,
          items: <DropdownMenuItem<int>>[
            for (var index = 0; index < _images.length; index++)
              DropdownMenuItem(
                value: index,
                child: Text(
                  '${_comparing
                      ? pane == 0
                            ? 'A · '
                            : 'B · '
                      : ''}${_images[index].label}',
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: (index) {
            if (index != null) _selectFrame(pane, index);
          },
        ),
        Text(image.description, style: theme.textTheme.bodySmall),
        const SizedBox(height: 6),
        Expanded(
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest,
              border: Border.all(color: border),
            ),
            child: Listener(
              onPointerDown: (_) {
                if (_activePane != pane) setState(() => _activePane = pane);
              },
              child: InteractiveViewer(
                key: ValueKey((pane, image.path)),
                transformationController: transform,
                maxScale: 6,
                onInteractionStart: (_) {
                  if (_activePane != pane) setState(() => _activePane = pane);
                },
                child: SizedBox.expand(
                  child: Image.file(
                    File(image.path),
                    fit: BoxFit.contain,
                    cacheWidth: 4096,
                    semanticLabel: image.label,
                    errorBuilder: (context, error, stack) => Center(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Text(
                          '无法载入${image.label}。请检查项目图像是否还在原位置。',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final item = widget.item;
    return Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: SizedBox(
        width: math.min(1100.0, math.max(0.0, size.width - 24)),
        height: math.min(760.0, math.max(0.0, size.height - 20)),
        child: CallbackShortcuts(
          bindings: <ShortcutActivator, VoidCallback>{
            const SingleActivator(
              LogicalKeyboardKey.equal,
              control: true,
            ): () =>
                _zoom(1.25),
            const SingleActivator(
              LogicalKeyboardKey.minus,
              control: true,
            ): () =>
                _zoom(0.8),
            const SingleActivator(LogicalKeyboardKey.digit0, control: true):
                _reset,
          },
          child: Focus(
            autofocus: true,
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: Text(
                          '图像证据 · 第 ${item.pageIndex + 1} 页',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      IconButton(
                        tooltip: '关闭图像证据',
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close),
                      ),
                    ],
                  ),
                  if (_images.isEmpty)
                    const Expanded(
                      child: Center(child: Text('这条记录没有可查看的图像路径。')),
                    )
                  else ...<Widget>[
                    _buildToolbar(),
                    if (item.coordinateFrame == 'unknown')
                      const Text('坐标帧未知：此处不叠加位置框，也不推断候选在原图的位置。'),
                    const SizedBox(height: 6),
                    Expanded(
                      child: LayoutBuilder(
                        builder: (context, constraints) {
                          if (!_comparing) return _buildPane(0);
                          final horizontal =
                              constraints.maxWidth >= 680 ||
                              constraints.maxHeight < 300;
                          return Flex(
                            direction: horizontal
                                ? Axis.horizontal
                                : Axis.vertical,
                            children: <Widget>[
                              Expanded(child: _buildPane(0)),
                              SizedBox(
                                width: horizontal ? 10 : 0,
                                height: horizontal ? 0 : 10,
                              ),
                              Expanded(child: _buildPane(1)),
                            ],
                          );
                        },
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
