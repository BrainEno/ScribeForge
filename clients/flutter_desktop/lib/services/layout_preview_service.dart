import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'cli_executor.dart';

/// An inspection snapshot, not an approved image, caption, or export asset.
class LayoutPreview {
  const LayoutPreview({
    required this.bytes,
    required this.verificationId,
    required this.engine,
    required this.sourceSha256,
    required this.previewSha256,
    required this.pixelBox,
    required this.frameSize,
  });
  final Uint8List bytes;
  final int verificationId;
  final String engine, sourceSha256, previewSha256;
  final List<int> pixelBox, frameSize;
}

/// Invokes the core's existing digest-checked, exclusive-create preview command.
/// The output is loaded into memory and the temporary directory is removed,
/// including on command/protocol failures. OS crashes can leave temporary files.
class LayoutPreviewService {
  const LayoutPreviewService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.projectId,
    this.tempRoot,
  });
  final ScribeForgeCliExecutor executor;
  final String executable, storePath;
  final int projectId;
  final Directory? tempRoot;
  static const maxImageBytes = 32 * 1024 * 1024;
  static const maxResponseChars = 64 * 1024;
  static const maxPixels = 32 * 1024 * 1024;

  Future<LayoutPreview> create({
    required int page,
    required int run,
    required int block,
    required String blockType,
  }) async {
    if (projectId < 1 ||
        page < 0 ||
        run < 1 ||
        block < 0 ||
        !const {'figure', 'table'}.contains(blockType)) {
      throw ArgumentError('无效的项目、页、批次或图表区域。');
    }
    final dir = await (tempRoot ?? Directory.systemTemp).createTemp(
      'scribeforge-preview-',
    );
    final path = '${dir.path}${Platform.pathSeparator}preview.png';
    final output = StringBuffer(), errors = StringBuffer();
    var oversized = false;
    try {
      final code = await executor.run(
        executable: executable,
        arguments: [
          'layout-preview',
          '--store',
          storePath,
          '--project',
          '$projectId',
          '--page',
          '$page',
          '--run',
          '$run',
          '--block',
          '$block',
          '--out',
          path,
        ],
        onOutput: (chunk) {
          if (output.length + chunk.length > maxResponseChars) {
            oversized = true;
            return;
          }
          if (!oversized) output.write(chunk);
        },
        onError: (chunk) {
          final left = 4000 - errors.length;
          if (left > 0)
            errors.write(
              chunk.length > left ? chunk.substring(0, left) : chunk,
            );
        },
      );
      if (code != 0)
        throw LayoutPreviewFailure(
          '无法创建预览（退出码 $code）：${errors.toString().trim()}',
        );
      if (oversized) throw const FormatException('预览响应超过读取上限。');
      final json = jsonDecode(output.toString());
      if (json is! Map<String, dynamic> ||
          json['schema_version'] != 1 ||
          json['status'] != 'unverified_preview' ||
          json['project_id'] != projectId ||
          json['page_index'] != page ||
          json['run_id'] != run ||
          json['block_index'] != block ||
          json['block_type'] != blockType ||
          json['frame_kind'] != 'original' ||
          json['output'] != path ||
          json['verification_id'] is! int ||
          (json['verification_id'] as int) < 1 ||
          json['engine'] is! String ||
          (json['engine'] as String).isEmpty ||
          !_hash(json['source_sha256']) ||
          !_hash(json['preview_sha256'])) {
        throw const FormatException('预览响应与所选证据不匹配。');
      }
      final size = _ints(json['frame_size'], 2);
      final box = _ints(json['pixel_box'], 4);
      if (size[0] <= 0 ||
          size[1] <= 0 ||
          box[0] < 0 ||
          box[1] < 0 ||
          box[2] <= box[0] ||
          box[3] <= box[1] ||
          box[2] > size[0] ||
          box[3] > size[1] ||
          (box[2] - box[0]) * (box[3] - box[1]) > maxPixels) {
        throw const FormatException('预览像素坐标无效。');
      }
      final file = File(path);
      final length = await file.length();
      if (length <= 0 || length > maxImageBytes)
        throw const FormatException('预览图片大小无效或超过 32 MiB。');
      // A file can grow after length() returns. Bound the stream as well.
      final buffer = BytesBuilder(copy: false);
      await for (final chunk in file.openRead()) {
        if (buffer.length + chunk.length > maxImageBytes) {
          throw const FormatException('预览图片超过 32 MiB。');
        }
        buffer.add(chunk);
      }
      final bytes = buffer.takeBytes();
      if (bytes.length != length ||
          sha256.convert(bytes).toString() != json['preview_sha256']) {
        throw const FormatException('预览文件与返回摘要不符。');
      }
      // Verify PNG signature and IHDR dimensions before handing bytes to the
      // platform image decoder. Compressed input must not imply a huge bitmap.
      const signature = [137, 80, 78, 71, 13, 10, 26, 10];
      if (bytes.length < 24 ||
          !List.generate(8, (i) => bytes[i]).asMap().entries.every(
            (entry) => entry.value == signature[entry.key],
          ) ||
          bytes[12] != 73 ||
          bytes[13] != 72 ||
          bytes[14] != 68 ||
          bytes[15] != 82) {
        throw const FormatException('预览不是有效的 PNG 图片。');
      }
      final header = ByteData.sublistView(bytes);
      if (header.getUint32(16) != box[2] - box[0] ||
          header.getUint32(20) != box[3] - box[1]) {
        throw const FormatException('预览图片尺寸与像素范围不符。');
      }
      return LayoutPreview(
        bytes: bytes,
        verificationId: json['verification_id'] as int,
        engine: json['engine'] as String,
        sourceSha256: json['source_sha256'] as String,
        previewSha256: json['preview_sha256'] as String,
        pixelBox: box,
        frameSize: size,
      );
    } finally {
      try {
        await dir.delete(recursive: true);
      } on FileSystemException catch (error) {
        // Do not claim cleanup succeeded: the path may contain book content.
        throw LayoutPreviewFailure(
          '无法删除临时预览目录 ${dir.path}，请手动删除：${error.message}',
        );
      }
    }
  }

  static bool _hash(Object? value) =>
      value is String && RegExp(r'^[0-9a-f]{64}$').hasMatch(value);
  static List<int> _ints(Object? value, int count) {
    if (value is! List ||
        value.length != count ||
        value.any((item) => item is! int)) {
      throw const FormatException('预览尺寸或像素坐标无效。');
    }
    return List<int>.unmodifiable(value.cast<int>());
  }
}

class LayoutPreviewFailure implements Exception {
  const LayoutPreviewFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
