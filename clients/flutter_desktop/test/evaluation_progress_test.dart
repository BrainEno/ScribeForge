import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/evaluation_progress.dart';

void main() {
  group('EvaluationProgress', () {
    test('counts only final decisions inside the valid page range', () {
      const decisions = <int, String>{
        0: 'a',
        1: 'review',
        2: 'tie',
        3: 'unexpected',
        6: 'b',
        -1: 'b',
      };

      expect(EvaluationProgress.decidedCount(5, decisions), 2);
      expect(EvaluationProgress.deferredCount(5, decisions), 1);
      expect(EvaluationProgress.pendingCount(5, decisions), 3);
    });

    test('jumps to the next unresolved page, including deferred pages', () {
      const decisions = <int, String>{
        0: 'a',
        1: 'b',
        2: 'review',
        3: 'tie',
      };

      expect(
        EvaluationProgress.nextPendingPage(
          pageCount: 5,
          currentPage: 0,
          decisions: decisions,
        ),
        2,
      );
    });

    test('wraps to an earlier pending page after reaching the end', () {
      const decisions = <int, String>{
        0: 'review',
        1: 'a',
        2: 'tie',
      };

      expect(
        EvaluationProgress.nextPendingPage(
          pageCount: 3,
          currentPage: 2,
          decisions: decisions,
        ),
        0,
      );
    });

    test('returns null when every page is final or there are no pages', () {
      const decisions = <int, String>{
        0: 'a',
        1: 'b',
        2: 'tie',
      };

      expect(EvaluationProgress.pendingCount(3, decisions), 0);
      expect(
        EvaluationProgress.nextPendingPage(
          pageCount: 3,
          currentPage: 1,
          decisions: decisions,
        ),
        isNull,
      );
      expect(
        EvaluationProgress.nextPendingPage(
          pageCount: 0,
          currentPage: 0,
          decisions: decisions,
        ),
        isNull,
      );
    });
  });
}
