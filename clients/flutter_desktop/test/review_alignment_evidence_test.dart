import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/review_queue.dart';
import 'package:scribeforge_desktop/widgets/review_alignment_evidence.dart';

void main() {
  ReviewQueueItem parseItem(String structuralFields) {
    final payload = '''
    {"schema_version":1,"projects":[{"id":1,"title":"Book","items":[{
      "page_index":0,"pair_index":0,"score":1.0,
      "reasons":["alignment_ambiguity"],
      "primary_text":"The quick brown fox",
      "secondary_text":"The quick brown fox",
      "vlm_text":null,"vlm_uncertain":false,"crop_path":null,
      "source_image_path":"page.png"$structuralFields
    }]}]}
    ''';
    return ReviewQueueSnapshot.fromJsonText(payload).items.single;
  }

  Future<void> showEvidence(WidgetTester tester, ReviewQueueItem item) async {
    tester.view.physicalSize = const Size(1200, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(child: ReviewAlignmentEvidence(item: item)),
        ),
      ),
    );
  }

  testWidgets('shows structural status, original lines and scored alternatives',
      (tester) async {
    final item = parseItem(''',
      "primary_line_indexes":[0,1],"secondary_line_indexes":[2],
      "alignment_status":"ambiguous",
      "ambiguity":{"reason":"column_mapping","score_margin":0.04,
        "alternatives":[
          {"primary_line_indexes":[0,1],"secondary_line_indexes":[2],
            "score":4.1,"text_similarity":0.96,"cross_axis_distance":0.01},
          {"primary_line_indexes":[0],"secondary_line_indexes":[2],
            "score":4.06,"text_similarity":0.71,"cross_axis_distance":0.02}
        ]}
    ''');

    await showEvidence(tester, item);

    expect(find.text('对齐状态：结构歧义'), findsOneWidget);
    expect(find.text('原始行索引（0-based）：OCR 1 [0, 1] → OCR 2 [2]'), findsOneWidget);
    expect(find.text('多行组合：2 → 1'), findsOneWidget);
    expect(find.textContaining('VLM 或机器共识不能绕过'), findsOneWidget);
    expect(
      find.text('候选 1：OCR 1 [0, 1] → OCR 2 [2] · 分数 4.100 · 文本相似度 96.0%'),
      findsOneWidget,
    );
    expect(
      find.text('候选 2：OCR 1 [0] → OCR 2 [2] · 分数 4.060 · 文本相似度 71.0%'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('distinguishes an ordinary matched text disagreement', (tester) async {
    final item = parseItem(''',
      "primary_line_indexes":[4],"secondary_line_indexes":[7],
      "alignment_status":"matched","ambiguity":null
    ''');

    await showEvidence(tester, item);

    expect(find.text('对齐状态：已匹配'), findsOneWidget);
    expect(find.text('原始行索引（0-based）：OCR 1 [4] → OCR 2 [7]'), findsOneWidget);
    expect(find.text('单行配对：1 → 1'), findsOneWidget);
    expect(find.textContaining('VLM 或机器共识不能绕过'), findsNothing);
  });

  testWidgets('does not invent line indexes for a legacy payload', (tester) async {
    final item = parseItem('');

    await showEvidence(tester, item);

    expect(find.text('对齐状态：未知'), findsOneWidget);
    expect(find.text('原始行索引未提供'), findsOneWidget);
    expect(find.textContaining('[0]'), findsNothing);
  });
}
