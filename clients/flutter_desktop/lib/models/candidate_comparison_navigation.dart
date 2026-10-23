import 'epub_candidate.dart';

/// Finds pages where two candidate EPUBs contain different extracted text.
/// This is a navigation aid only; it does not determine which reading is
/// correct.
class CandidateComparisonNavigation {
  CandidateComparisonNavigation._();

  static bool pagesDiffer(EpubPage? a, EpubPage? b) {
    if (a == null || b == null) return a != b;

    final linesA = a.lines;
    final linesB = b.lines;
    if (linesA.length != linesB.length) return true;
    for (var index = 0; index < linesA.length; index++) {
      if (linesA[index] != linesB[index]) return true;
    }
    return false;
  }

  /// Returns the next differing page after [currentPage], wrapping at the
  /// end. The current page is excluded so a one-page book or a book whose
  /// only differing page is already selected does not produce a no-op jump.
  static int? nextDifferingPage({
    required int pageCount,
    required int currentPage,
    required EpubCandidate candidateA,
    required EpubCandidate candidateB,
  }) {
    if (pageCount <= 1) return null;
    final current = ((currentPage % pageCount) + pageCount) % pageCount;
    for (var offset = 1; offset < pageCount; offset++) {
      final candidatePage = (current + offset) % pageCount;
      if (pagesDiffer(
        candidateA.pageForSourceIndex(candidatePage),
        candidateB.pageForSourceIndex(candidatePage),
      )) {
        return candidatePage;
      }
    }
    return null;
  }
}
