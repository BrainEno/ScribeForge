import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/textless_confirmation.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/textless_confirmation_service.dart';

void main() {
  test(
    'exact preview digest and scoped receipt are required for confirmation',
    () async {
      final cli = _ConfirmationCli();
      final service = TextlessConfirmationService(
        executor: cli,
        executable: 'scribeforge',
        storePath: '/book',
        project: 2,
        page: 5,
      );
      final preview = await service.preview();
      expect(preview.raster, [1, 2, 3]);
      expect(cli.calls.single.first, 'textless-preview');
      final receipt = await service.confirm(
        preview,
        TextlessPageKind.nonText,
        '--checked',
      );
      expect(receipt.id, 44);
      expect(cli.calls.last, contains('--reason=--checked'));
      expect(
        cli.calls.last,
        containsAllInOrder(['--confirmation-token', preview.token]),
      );
      cli.wrongPage = true;
      await expectLater(
        service.confirm(preview, TextlessPageKind.nonText, 'checked'),
        throwsFormatException,
      );
      cli.badDigest = true;
      await expectLater(service.preview(), throwsFormatException);
    },
  );

  test('blocked previews cannot be submitted', () async {
    final cli = _ConfirmationCli()..blocked = true;
    final service = TextlessConfirmationService(
      executor: cli,
      executable: 'scribeforge',
      storePath: '/book',
      project: 2,
      page: 5,
    );
    final preview = await service.preview();
    expect(preview.token, isNull);
    expect(preview.blockedReason, isNotEmpty);
    await expectLater(
      service.confirm(preview, TextlessPageKind.blank, 'checked'),
      throwsFormatException,
    );
    expect(cli.calls, hasLength(1));
  });
}

class _ConfirmationCli implements ScribeForgeCliExecutor {
  final calls = <List<String>>[];
  bool wrongPage = false;
  bool badDigest = false;
  bool blocked = false;

  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String) onOutput,
    required void Function(String) onError,
  }) async {
    calls.add(arguments);
    const bytes = [1, 2, 3];
    final digest = sha256.convert(badDigest ? [9] : bytes).toString();
    if (arguments.first == 'textless-preview') {
      onOutput(
        jsonEncode({
          'schema_version': 1,
          'project_id': 2,
          'page_index': 5,
          'verification_id': 11,
          'raster_sha256': digest,
          'width': 1,
          'height': 1,
          'raster_base64': base64Encode(bytes),
          'confirmation_token': blocked ? null : 'p6:v11:$digest',
          'blocked_reason': blocked ? 'already recorded' : null,
          'history': [],
          'history_truncated': false,
        }),
      );
    } else {
      onOutput(
        jsonEncode({
          'schema_version': 1,
          'id': 44,
          'project_id': 2,
          'page_index': wrongPage ? 6 : 5,
          'kind': 'non_text',
        }),
      );
    }
    return 0;
  }
}
