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

  test('parses page, pair, OCR alternatives, VLM uncertainty and image evidence', () {
    final queue = ReviewQueueSnapshot.fromJsonText(payload);
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
    expect(item.cropPath, '/books/Novel.scribeforge/crops/page-000004-pair-0001.png');
    expect(item.sourceImagePath, '/books/Novel.scribeforge/assets/page-000004.png');
  });

  test('keeps legacy schema-v1 payloads without a furniture reason compatible', () {
    final legacyPayload = payload.replaceFirst(
      '"furniture_reason": "repeated_in_margin",',
      '',
    );

    expect(
      ReviewQueueSnapshot.fromJsonText(legacyPayload).items.single.furnitureReason,
      isNull,
    );
  });

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
  });
}
