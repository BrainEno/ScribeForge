import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/review_queue_navigation.dart';

void main() {
  group('ReviewQueueNavigation', () {
    test('moves one item at a time and clamps at either end', () {
      expect(ReviewQueueNavigation.move(0, itemCount: 3, delta: -1), 0);
      expect(ReviewQueueNavigation.move(0, itemCount: 3, delta: 1), 1);
      expect(ReviewQueueNavigation.move(2, itemCount: 3, delta: 1), 2);
      expect(ReviewQueueNavigation.move(0, itemCount: 0, delta: 1), -1);
    });

    test('quick page jump selects the first queued pair on that page', () {
      const pageNumbers = <int>[2, 5, 5, 9];

      expect(
        ReviewQueueNavigation.indexForPage(pageNumbers, pageNumber: 5),
        1,
      );
      expect(
        ReviewQueueNavigation.indexForPage(pageNumbers, pageNumber: 1),
        isNull,
      );
      expect(
        ReviewQueueNavigation.indexForPage(pageNumbers, pageNumber: 0),
        isNull,
      );
    });
  });
}
