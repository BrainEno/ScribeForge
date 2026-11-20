import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/export/export_verification_screen.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';
import 'package:scribeforge_desktop/services/export_verification_service.dart';

void main() {
  testWidgets(
    'reads adjacent receipt and rechecks with optional project comparison',
    (tester) async {
      tester.view.physicalSize = const Size(900, 1050);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final cli = _Verifier();
      await tester.pumpWidget(
        MaterialApp(
          home: ExportVerificationScreen(
            service: ExportVerificationService(
              executor: cli,
              executable: 'scribeforge',
            ),
            filePicker: const _Picker(),
            sourceDigest: 'a' * 64,
          ),
        ),
      );
      await tester.tap(find.byKey(const ValueKey('choose-export-to-verify')));
      await tester.pumpAndSettle();
      expect(cli.calls.single, [
        'verify-export',
        '--file=book.epub',
        '--source-sha256',
        'a' * 64,
      ]);
      expect(find.text('文件或凭据不一致'), findsOneWidget);
      expect(find.text('来源与当前项目不符'), findsOneWidget);
      await tester.tap(find.byType(CheckboxListTile));
      await tester.pumpAndSettle();
      expect(cli.calls.last, ['verify-export', '--file=book.epub']);
      expect(find.text('导出凭据一致'), findsOneWidget);
    },
  );
}

class _Picker implements DesktopFilePicker {
  const _Picker();

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async => null;

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    expect(allowedExtensions, ['epub', 'docx']);
    return 'book.epub';
  }
}

class _Verifier implements ScribeForgeCliExecutor {
  final calls = <List<String>>[];

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
    final compare = arguments.contains('--source-sha256');
    final fingerprint = {'size': 300, 'sha256': 'c' * 64};
    onOutput(
      jsonEncode({
        'schema_version': 1,
        'file': 'book.epub',
        'format': 'epub',
        'status': compare ? 'mismatch' : 'consistent',
        'artifact': fingerprint,
        'normalization_matches_artifact': true,
        'project_source_matches': compare ? false : null,
        'receipt': {
          'file': 'book.epub',
          'format': 'epub',
          'artifact': fingerprint,
          'normalization': {'size': 250, 'sha256': 'd' * 64},
          'project_id': 3,
          'title': 'Book',
          'policy': 'preserve',
          'source_sha256': 'b' * 64,
          'modified': '2026-11-20',
          'strict_acceptance': false,
          'include_page_furniture': false,
          'pages': 2,
          'accepted_pairs': 10,
        },
        'findings': [
          if (compare)
            {
              'severity': 'error',
              'code': 'project_source_mismatch',
              'message': 'Different source',
            },
        ],
      }),
    );
    return 0;
  }
}
