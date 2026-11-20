import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/superseded_review_drafts.dart';

void main() {
  test('keeps text and evidence provenance without duplicating drafts', () {
    final archive = SupersededReviewDrafts();
    const old = SupersededReviewDraft(
      key: (1, 0, 4),
      token: 'v1:r0',
      text: '舊稿',
    );
    const newVersion = SupersededReviewDraft(
      key: (1, 0, 4),
      token: 'v2:r0',
      text: '舊稿',
    );
    archive.remember(old);
    archive.remember(old);
    archive.remember(newVersion);
    expect(archive.newestFirst, <SupersededReviewDraft>[newVersion, old]);
    expect(old.location, contains('第 1 页 · 第 5 组'));
    archive.remove(old);
    expect(archive.newestFirst, <SupersededReviewDraft>[newVersion]);
    archive.clear();
    expect(archive.isNotEmpty, isFalse);
  });

  test('bounds the session archive and ignores empty edits', () {
    final archive = SupersededReviewDrafts();
    for (var index = 0; index <= SupersededReviewDrafts.capacity; index++) {
      archive.remember(
        SupersededReviewDraft(key: (1, index, 0), token: null, text: '$index'),
      );
    }
    archive.remember(
      const SupersededReviewDraft(key: (1, 99, 0), token: null, text: ''),
    );
    expect(archive.newestFirst, hasLength(SupersededReviewDrafts.capacity));
    expect(archive.newestFirst.first.key.$2, SupersededReviewDrafts.capacity);
    expect(archive.newestFirst.last.key.$2, 1);
  });
}
