import '../models/textless_confirmation.dart';
import 'cli_executor.dart';

class TextlessConfirmationService {
  const TextlessConfirmationService({
    required this.executor,
    required this.executable,
    required this.storePath,
    required this.project,
    required this.page,
  });

  final ScribeForgeCliExecutor executor;
  final String executable;
  final String storePath;
  final int project;
  final int page;

  List<String> get _scope => [
    '--store',
    storePath,
    '--project-id',
    '$project',
    '--page',
    '$page',
  ];

  Future<TextlessPreview> preview() async {
    final result = await _read([
      'textless-preview',
      ..._scope,
    ], limit: 18 * 1024 * 1024);
    return TextlessPreview.fromText(result, project: project, page: page);
  }

  Future<TextlessReceipt> confirm(
    TextlessPreview preview,
    TextlessPageKind kind,
    String reason,
  ) async {
    final token = preview.token;
    if (preview.project != project || preview.page != page || token == null) {
      throw const FormatException('当前页不可确认，请重新读取已校验原页。');
    }
    final result = await _read([
      'confirm',
      ..._scope,
      '--kind',
      kind.value,
      '--reason=${reason.trim()}',
      '--confirmation-token',
      token,
      '--json',
    ], limit: 8192);
    return TextlessReceipt.fromText(result, preview, kind);
  }

  Future<String> _read(List<String> arguments, {required int limit}) async {
    final output = StringBuffer();
    final errors = StringBuffer();
    var oversized = false;
    final code = await executor.run(
      executable: executable,
      arguments: arguments,
      onOutput: (chunk) {
        if (oversized || output.length + chunk.length > limit) {
          oversized = true;
          return;
        }
        output.write(chunk);
      },
      onError: (chunk) {
        final remaining = 4000 - errors.length;
        if (remaining > 0)
          errors.write(
            chunk.length > remaining ? chunk.substring(0, remaining) : chunk,
          );
      },
    );
    if (code != 0)
      throw TextlessConfirmationFailure(code, errors.toString().trim());
    if (oversized) throw const FormatException('核心返回的数据超过读取上限。');
    return output.toString();
  }
}

class TextlessConfirmationFailure implements Exception {
  const TextlessConfirmationFailure(this.code, this.diagnostic);

  final int code;
  final String diagnostic;

  @override
  String toString() =>
      '无文字页操作失败（退出码 $code）${diagnostic.isEmpty ? '。' : '：$diagnostic'}';
}
