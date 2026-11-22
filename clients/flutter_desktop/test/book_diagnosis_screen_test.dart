import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/export/book_diagnosis_screen.dart';
import 'package:scribeforge_desktop/services/book_diagnosis_service.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/desktop_file_picker.dart';

const report = '''
{
 "schema_version":1, "source_sha256_matches_project":true,
 "source_physical_pages":562, "recorded_source_count_matches":null,
 "imported_pages":22, "missing_imported_count":540,
 "missing_imported_pages_1based":[23,24], "import_state":"failed",
 "both_ocr_succeeded_pages":20, "verified_pages":19, "failed_jobs":2,
 "epub":{"spine_documents":22,"source_page_markers":22,
 "missing_source_page_marker_count":540,
 "missing_source_page_markers_1based":[23,24],
 "evidence_styles":["unrecognized_document"]}
}
''';

class Picker implements DesktopFilePicker {
  String? selection;
  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    return selection;
  }

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) async => null;
}

class Executor implements ScribeForgeCliExecutor {
  final gate = Completer<void>();
  bool fail = false;
  int calls = 0;
  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    calls++;
    await gate.future;
    if (fail) {
      onError('source mismatch');
      return 2;
    }
    onOutput(report);
    return 0;
  }

  @override
  Future<void> cancel() async {}
}

void main() {
  Future<void> showScreen(
    WidgetTester tester,
    Picker picker,
    Executor executor,
  ) async {
    tester.view.physicalSize = const Size(1200, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: BookDiagnosisScreen(
          service: BookDiagnosisService(
            executor: executor,
            executable: 'backend',
          ),
          filePicker: picker,
          storePath: 'project',
          sourcePath: 'book.pdf',
        ),
      ),
    );
  }

  testWidgets('cancelled file picker does not invoke the backend', (
    tester,
  ) async {
    final picker = Picker();
    final executor = Executor();
    await showScreen(tester, picker, executor);
    await tester.tap(find.byKey(const ValueKey('choose-diagnosis-epub')));
    await tester.pump();
    expect(executor.calls, 0);
    expect(find.text('原书物理页：562'), findsNothing);
  });

  testWidgets('shows progress then counts and explicit limitations', (
    tester,
  ) async {
    final picker = Picker()..selection = 'book.epub';
    final executor = Executor();
    await showScreen(tester, picker, executor);
    await tester.tap(find.byKey(const ValueKey('choose-diagnosis-epub')));
    await tester.pump();
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    executor.gate.complete();
    await tester.pumpAndSettle();
    expect(find.text('原书物理页：562'), findsOneWidget);
    expect(find.text('项目已导入：22 页 · 未导入：540 页'), findsOneWidget);
    expect(find.text('导入状态：failed'), findsOneWidget);
    expect(find.textContaining('部分 EPUB 文档的页标格式未识别'), findsOneWidget);
    expect(find.textContaining('页标数量不能证明'), findsOneWidget);
  });

  testWidgets('shows backend error without displaying a stale report', (
    tester,
  ) async {
    final picker = Picker()..selection = 'book.epub';
    final executor = Executor()..fail = true;
    await showScreen(tester, picker, executor);
    await tester.tap(find.byKey(const ValueKey('choose-diagnosis-epub')));
    await tester.pump();
    executor.gate.complete();
    await tester.pumpAndSettle();
    expect(find.textContaining('source mismatch'), findsOneWidget);
    expect(find.text('原书物理页：562'), findsNothing);
  });
}
