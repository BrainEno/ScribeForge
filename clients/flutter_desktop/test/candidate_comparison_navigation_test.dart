import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/candidate_comparison_navigation.dart';
import 'package:scribeforge_desktop/models/epub_candidate.dart';

void main() {
  group('CandidateComparisonNavigation', () {
    EpubPage page(int index, String text) => EpubPage(
          spineIndex: index,
          sourcePageIndex: index,
          title: 'Page $index',
          text: text,
        );

    EpubCandidate candidate(List<String> text) => EpubCandidate(
          path: '/tmp/candidate.epub',
          title: 'Candidate',
          creator: null,
          pages: List<EpubPage>.generate(
            text.length,
            (index) => page(index, text[index]),
          ),
          isSourcePageAligned: true,
          sha256: 'test-hash',
        );

    test('compares extracted lines without considering chapter titles', () {
      expect(
        CandidateComparisonNavigation.pagesDiffer(
          page(0, 'First line\nSecond line'),
          const EpubPage(
            spineIndex: 8,
            sourcePageIndex: 0,
            title: 'Different title',
            text: 'First line\nSecond line',
          ),
        ),
        isFalse,
      );
      expect(
        CandidateComparisonNavigation.pagesDiffer(
          page(0, 'A different line'),
          page(0, 'Another line'),
        ),
        isTrue,
      );
      expect(
        CandidateComparisonNavigation.pagesDiffer(page(0, 'one'), page(0, 'one\ntwo')),
        isTrue,
      );
    });

    test('treats a page present in only one candidate as a difference', () {
      expect(CandidateComparisonNavigation.pagesDiffer(null, null), isFalse);
      expect(CandidateComparisonNavigation.pagesDiffer(page(1, 'text'), null), isTrue);
    });

    test('jumps to the next differing page and wraps around the book', () {
      final a = candidate(['same', 'alpha', 'same', 'gamma', 'same']);
      final b = candidate(['same', 'beta', 'same', 'delta', 'same']);

      expect(
        CandidateComparisonNavigation.nextDifferingPage(
          pageCount: 5,
          currentPage: 0,
          candidateA: a,
          candidateB: b,
        ),
        1,
      );
      expect(
        CandidateComparisonNavigation.nextDifferingPage(
          pageCount: 5,
          currentPage: 1,
          candidateA: a,
          candidateB: b,
        ),
        3,
      );
      expect(
        CandidateComparisonNavigation.nextDifferingPage(
          pageCount: 5,
          currentPage: 3,
          candidateA: a,
          candidateB: b,
        ),
        1,
      );
    });

    test('returns null when there is no other differing page', () {
      final a = candidate(['same', 'different', 'same']);
      final b = candidate(['same', 'other', 'same']);

      expect(
        CandidateComparisonNavigation.nextDifferingPage(
          pageCount: 3,
          currentPage: 1,
          candidateA: a,
          candidateB: b,
        ),
        isNull,
      );
      expect(
        CandidateComparisonNavigation.nextDifferingPage(
          pageCount: 1,
          currentPage: 0,
          candidateA: a,
          candidateB: b,
        ),
        isNull,
      );
    });
  });
}
