import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/text_diff.dart';

void main() {
  group('diffLines', () {
    test('marks changed lines in both candidates and keeps equal lines', () {
      final rows = diffLines(
        const ['Opening line', 'wrong word', 'Last line'],
        const ['Opening line', 'right word', 'Last line'],
      );

      expect(rows, hasLength(3));
      expect(rows[0].changed, isFalse);
      expect(rows[1].changed, isTrue);
      expect(rows[1].left, 'wrong word');
      expect(rows[1].right, 'right word');
      expect(rows[2].changed, isFalse);
      final summary = summarizeDiff(rows, const ['Opening line', 'wrong word', 'Last line'],
          const ['Opening line', 'right word', 'Last line']);
      expect(summary.sharedLines, 2);
      expect(summary.changedRows, 1);
    });

    test('represents inserted lines as a missing-side row', () {
      final rows = diffLines(const ['A', 'C'], const ['A', 'B', 'C']);

      expect(rows.map((row) => row.changed), [false, true, false]);
      expect(rows[1].left, isNull);
      expect(rows[1].right, 'B');
    });

    test('does not explode on very long pages', () {
      final left = List<String>.generate(501, (i) => 'left-$i');
      final right = List<String>.generate(501, (i) => 'right-$i');

      final rows = diffLines(left, right);

      expect(rows, hasLength(501));
      expect(rows.every((row) => row.changed), isTrue);
    });

    test('keeps alignment after an insertion on a long page', () {
      final left = List<String>.generate(600, (i) => 'line-$i');
      final right = ['new opening line', ...left];

      final rows = diffLines(left, right);

      expect(rows, hasLength(601));
      expect(rows.first.changed, isTrue);
      expect(rows.first.left, isNull);
      expect(rows.first.right, 'new opening line');
      expect(rows.skip(1).every((row) => !row.changed), isTrue);
    });
  });
}
