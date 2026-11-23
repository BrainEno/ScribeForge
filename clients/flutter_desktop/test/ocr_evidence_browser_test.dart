import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/evidence/ocr_evidence_browser.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/ocr_evidence_service.dart';

void main() {
  testWidgets('opens saved OCR and historical alignment in a narrow window', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(430, 730);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final executor = _EvidenceExecutor();
    await tester.pumpWidget(
      MaterialApp(
        home: OcrEvidenceBrowser(
          service: OcrEvidenceService(
            executor: executor,
            executable: 'scribeforge',
            storePath: '/project',
            projectId: 9,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('ocr-evidence-page-0')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('核验 #3 · 历史批次'));
    await tester.pumpAndSettle();
    expect(find.text('鸦'), findsOneWidget);
    expect(find.text('雅'), findsOneWidget);
    expect(find.textContaining('对齐有歧义'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('PaddleOCR · 1.0 · #5'));
    await tester.pumpAndSettle();
    expect(find.text('鸦'), findsOneWidget);
    expect(find.textContaining('最低已报告置信度：90.0%'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('查看区域'));
    await tester.pumpAndSettle();
    expect(find.text('区域索引 0 · figure'), findsOneWidget);
    expect(find.text('此区域没有关联文字行。'), findsOneWidget);
    expect(find.textContaining('未检测到不代表没有图表'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const ValueKey('layout-preview-0')));
    await tester.tap(find.byKey(const ValueKey('layout-preview-0')));
    await tester.pumpAndSettle();
    expect(find.textContaining('不会将插图加入 EPUB'), findsOneWidget);
    expect(executor.sections, ['pages', 'page', 'pairs', 'lines', 'blocks']);
    await tester.tap(find.text('关闭'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(executor.sections, ['pages', 'page', 'pairs', 'lines', 'blocks']);
  });
}

class _EvidenceExecutor implements ScribeForgeCliExecutor {
  final sections = <String>[];

  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String) onOutput,
    required void Function(String) onError,
  }) async {
    String? argument(String key) {
      final index = arguments.indexOf(key);
      return index < 0 ? null : arguments[index + 1];
    }

    final section = argument('--section')!;
    sections.add(section);
    expect(arguments.first, 'ocr-evidence');
    expect(argument('--project-id'), '9');
    final data = switch (section) {
      'pages' => <String, Object?>{
        'rows': [
          {
            'page_index': 0,
            'width': 100,
            'height': 200,
            'ocr_runs': 2,
            'verification': 4,
          },
        ],
      },
      'page' => <String, Object?>{
        'width': 100,
        'height': 200,
        'ocr_runs': [
          {
            'id': 5,
            'engine': 'PaddleOCR',
            'engine_version': '1.0',
            'created_at': '2026-11-20',
            'line_count': 1,
          },
        ],
        'verifications': [
          for (final id in [4, 3])
            {
              'id': id,
              'created_at': '2026-11-20',
              'primary_run_id': 5,
              'secondary_run_id': 2,
              'pair_count': 1,
            },
        ],
        'older_ocr_runs_omitted': false,
        'older_verifications_omitted': false,
      },
      'pairs' => <String, Object?>{
        'rows': [
          {
            'pair_index': 0,
            'text_similarity': 0.5,
            'risk_score': 0.6,
            'conflicts': 1,
            'ambiguous': true,
            for (final side in ['primary', 'secondary'])
              side: {
                'lines': [
                  {
                    'line_index': 0,
                    'text': side == 'primary' ? '鸦' : '雅',
                    'text_truncated': false,
                  },
                ],
                'additional_members': false,
              },
          },
        ],
      },
      'blocks' => <String, Object?>{
        'rows': [
          {
            'block_index': 0,
            'block_type': 'figure',
            'x': 0.1,
            'y': 0.2,
            'width': 0.5,
            'height': 0.3,
            'line_start': 0,
            'line_end': 0,
          },
        ],
      },
      'lines' => <String, Object?>{
        'rows': [
          {
            'line_index': 0,
            'text': '鸦',
            'text_truncated': false,
            'x': 0.1,
            'y': 0.1,
            'width': 0.5,
            'height': 0.05,
            'token_count': 1,
            'min_confidence': 0.9,
          },
        ],
      },
      _ => throw StateError('Unexpected section: $section'),
    };
    onOutput(
      jsonEncode(<String, Object?>{
        'schema_version': 1,
        'project_id': 9,
        'project_title': 'Example',
        'section': section,
        'page_index': int.tryParse(argument('--page') ?? ''),
        'run_id': int.tryParse(argument('--run') ?? ''),
        ...data,
      }),
    );
    return 0;
  }
}
