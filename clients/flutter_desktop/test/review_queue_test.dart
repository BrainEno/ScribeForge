import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/review_queue.dart';

void main() {
  const payload = '''
  {
    "schema_version": 1,
    "store": "/books/Novel.scribeforge",
    "projects": [
      {
        "id": 42,
        "title": "Novel",
        "source_uri": "/books/Novel.pdf",
        "items": [
          {
            "page_index": 3,
            "pair_index": 1,
            "score": 0.72,
            "reasons": ["text_disagreement", "low_confidence"],
            "furniture_reason": "repeated_in_margin",
            "primary_text": "第一种读法",
            "secondary_text": "第二种读法",
            "vlm_text": "模型读法",
            "vlm_uncertain": true,
            "crop_path": "/books/Novel.scribeforge/crops/page-000004-pair-0001.png",
            "source_image_path": "/books/Novel.scribeforge/assets/page-000004.png"
          }
        ]
      }
    ]
  }
  ''';

  test(
    'parses page, pair, OCR alternatives, VLM uncertainty and image evidence',
    () {
      final structuredPayload = payload.replaceFirst(
        '"primary_text": "第一种读法",',
        '"primary_line_indexes": [0, 1],'
            '"secondary_line_indexes": [2],'
            '"alignment_status": "ambiguous",'
            '"ambiguity": {'
            '"reason": "column_mapping", "score_margin": 0.04,'
            '"alternatives": ['
            '{"primary_line_indexes":[0,1],"secondary_line_indexes":[2],'
            '"score":4.1,"text_similarity":0.96,"cross_axis_distance":0.01},'
            '{"primary_line_indexes":[0],"secondary_line_indexes":[2],'
            '"score":4.06,"text_similarity":0.71,"cross_axis_distance":0.02}'
            ']},'
            '"primary_text": "第一种读法",',
      );
      final queue = ReviewQueueSnapshot.fromJsonText(structuredPayload);
      final item = queue.items.single;

      expect(item.projectId, 42);
      expect(item.projectTitle, 'Novel');
      expect(item.pageIndex, 3);
      expect(item.pairIndex, 1);
      expect(item.score, closeTo(0.72, 0.0001));
      expect(item.reasons, <String>['text_disagreement', 'low_confidence']);
      expect(item.furnitureReason, 'repeated_in_margin');
      expect(item.primaryText, '第一种读法');
      expect(item.secondaryText, '第二种读法');
      expect(item.vlmText, '模型读法');
      expect(item.vlmUncertain, isTrue);
      expect(
        item.cropPath,
        '/books/Novel.scribeforge/crops/page-000004-pair-0001.png',
      );
      expect(
        item.sourceImagePath,
        '/books/Novel.scribeforge/assets/page-000004.png',
      );
      expect(item.coordinateFrame, 'unknown');
      expect(item.ocrFrameImagePath, isNull);
      expect(item.alignmentStatus, 'ambiguous');
      expect(item.primaryLineIndexes, <int>[0, 1]);
      expect(item.secondaryLineIndexes, <int>[2]);
      expect(item.ambiguity, isNotNull);
      expect(item.ambiguity!.reason, 'column_mapping');
      expect(item.ambiguity!.scoreMargin, closeTo(0.04, 0.0001));
      expect(item.ambiguity!.alternatives, hasLength(2));
      expect(item.ambiguity!.alternatives[0].primaryLineIndexes, <int>[0, 1]);
      expect(item.ambiguity!.alternatives[0].secondaryLineIndexes, <int>[2]);
      expect(item.ambiguity!.alternatives[0].score, closeTo(4.1, 0.0001));
      expect(
        item.ambiguity!.alternatives[0].textSimilarity,
        closeTo(0.96, 0.0001),
      );
      expect(
        item.ambiguity!.alternatives[0].crossAxisDistance,
        closeTo(0.01, 0.0001),
      );
    },
  );

  test('parses a verified preprocessed coordinate frame', () {
    final withFrame = payload.replaceFirst(
      '"source_image_path": "/books/Novel.scribeforge/assets/page-000004.png"',
      '"source_image_path": "/books/Novel.scribeforge/assets/page-000004.png",'
          '"coordinate_frame": "preprocessed",'
          '"ocr_frame_image_path": "/books/Novel.scribeforge/preprocessed/page.png"',
    );
    final item = ReviewQueueSnapshot.fromJsonText(withFrame).items.single;
    expect(item.coordinateFrame, 'preprocessed');
    expect(
      item.ocrFrameImagePath,
      '/books/Novel.scribeforge/preprocessed/page.png',
    );
  });

  test(
    'keeps legacy schema-v1 payloads without a furniture reason compatible',
    () {
      final legacyPayload = payload.replaceFirst(
        '"furniture_reason": "repeated_in_margin",',
        '',
      );

      final legacyItem = ReviewQueueSnapshot.fromJsonText(legacyPayload)
          .items
          .single;
      expect(legacyItem.furnitureReason, isNull);
      expect(legacyItem.alignmentStatus, 'unknown');
      expect(legacyItem.primaryLineIndexes, isEmpty);
      expect(legacyItem.secondaryLineIndexes, isEmpty);
      expect(legacyItem.ambiguity, isNull);
    },
  );

  test('returns an empty queue for an empty store or no unresolved pairs', () {
    expect(
      ReviewQueueSnapshot.fromJsonText(
        '{"schema_version":1,"store":"store","projects":[]}',
      ).items,
      isEmpty,
    );
    expect(
      ReviewQueueSnapshot.fromJsonText(
        '{"schema_version":1,"projects":[{"id":1,"title":"Book","source_uri":"book.pdf","items":[]}]}',
      ).items,
      isEmpty,
    );
  });

  test('rejects unsupported schemas and malformed review evidence', () {
    expect(
      () => ReviewQueueSnapshot.fromJsonText(
        '{"schema_version":2,"projects":[]}',
      ),
      throwsFormatException,
    );
    expect(
      () => ReviewQueueSnapshot.fromJsonText(
        '{"schema_version":1,"projects":[{"id":1,"title":"Book","source_uri":"book.pdf","items":[{"page_index":0}]}]}',
      ),
      throwsFormatException,
    );
    final negativeLineIndex = payload.replaceFirst(
      '"primary_text": "第一种读法",',
      '"primary_line_indexes":[-1],"secondary_line_indexes":[0],'
          '"alignment_status":"matched","primary_text":"第一种读法",',
    );
    expect(
      () => ReviewQueueSnapshot.fromJsonText(negativeLineIndex),
      throwsFormatException,
    );
  });
}
