import 'dart:convert';
import 'dart:io';

import 'cli_executor.dart';

/// Receipt returned by the CLI's `confirm-figure --json` after a human accepts
/// a previewed crop as an inline EPUB figure (issue #246).
class FigureConfirmation {
  const FigureConfirmation({
    required this.id,
    required this.page,
    required this.anchorPair,
    required this.cropSha256,
    required this.caption,
    required this.reason,
  });
  final int id, page, anchorPair;
  final String cropSha256, caption, reason;

  static FigureConfirmation fromJson(Map<String, dynamic> json) =>
      FigureConfirmation(
        id: json['id'] as int,
        page: json['page_index'] as int,
        anchorPair: json['anchor_pair_index'] as int,
        cropSha256: json['crop_sha256'] as String,
        caption: json['caption'] as String? ?? '',
        reason: json['reason'] as String? ?? '',
      );
}

/// Runs `confirm-figure` against a freshly previewed crop. The crop bytes are
/// written to a private temporary file, verified by the CLI against the
/// preview's confirmation token, and always deleted afterwards.
class FigureConfirmationService {
  const FigureConfirmationService({
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
  static const maxResponseChars = 64 * 1024;

  Future<FigureConfirmation> confirm({
    required int page,
    required int run,
    required int block,
    required List<int> cropBytes,
    required String confirmationToken,
    required int anchorPair,
    required String reason,
    String caption = '',
  }) async {
    if (projectId < 1 ||
        page < 0 ||
        run < 1 ||
        block < 0 ||
        anchorPair < 0 ||
        cropBytes.isEmpty ||
        !confirmationToken.startsWith('fig:') ||
        reason.trim().isEmpty ||
        reason.length > 1000 ||
        caption.length > 1000) {
      throw ArgumentError('确认参数无效：请检查页码、锚点、图题与原因。');
    }
    final dir = await (tempRoot ?? Directory.systemTemp).createTemp(
      'scribeforge-confirm-',
    );
    final cropPath = '${dir.path}${Platform.pathSeparator}confirmed-crop.png';
    final output = StringBuffer(), errors = StringBuffer();
    var oversized = false;
    try {
      // The CLI re-hashes these exact bytes against the token; writing them
      // here never trusts the client-side preview digest.
      await File(cropPath).writeAsBytes(cropBytes, flush: true);
      final code = await executor.run(
        executable: executable,
        arguments: [
          'confirm-figure',
          '--store',
          storePath,
          '--project-id',
          '$projectId',
          '--page',
          '$page',
          '--run',
          '$run',
          '--block',
          '$block',
          '--crop',
          cropPath,
          '--confirmation-token',
          confirmationToken,
          '--anchor-pair',
          '$anchorPair',
          if (caption.isNotEmpty) ...['--caption', caption],
          '--reason',
          reason.trim(),
          '--json',
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
          if (left > 0) {
            errors.write(
              chunk.length > left ? chunk.substring(0, left) : chunk,
            );
          }
        },
      );
      if (code != 0) {
        throw FigureConfirmationFailure(
          '确认失败（退出码 $code）：${errors.toString().trim()}',
        );
      }
      if (oversized) throw const FormatException('确认响应超过读取上限。');
      final json = jsonDecode(output.toString());
      if (json is! Map<String, dynamic> ||
          json['schema_version'] != 1 ||
          json['status'] != 'figure_confirmed' ||
          json['project_id'] != projectId ||
          json['page_index'] != page ||
          json['run_id'] != run ||
          json['block_index'] != block ||
          json['anchor_pair_index'] != anchorPair ||
          json['id'] is! int ||
          (json['id'] as int) < 1 ||
          json['crop_sha256'] is! String ||
          !_hash(json['crop_sha256']) ||
          json['verification_id'] is! int) {
        throw const FormatException('确认回执与所选证据不匹配。');
      }
      return FigureConfirmation.fromJson(json);
    } finally {
      try {
        await dir.delete(recursive: true);
      } on FileSystemException catch (error) {
        throw FigureConfirmationFailure(
          '无法删除临时确认目录 ${dir.path}，请手动删除：${error.message}',
        );
      }
    }
  }

  static bool _hash(Object? value) =>
      value is String && RegExp(r'^[0-9a-f]{64}$').hasMatch(value);
}

class FigureConfirmationFailure implements Exception {
  const FigureConfirmationFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
