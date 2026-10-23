/// Counts human review decisions and locates the next page that still needs
/// attention. Pages marked "review" are deferred, not finalized, so navigation
/// returns to them as well as to pages with no decision.
class EvaluationProgress {
  EvaluationProgress._();

  static bool _isFinal(String? decision) =>
      decision == 'a' || decision == 'b' || decision == 'tie';

  static int decidedCount(int pageCount, Map<int, String> decisions) {
    if (pageCount <= 0) return 0;
    var count = 0;
    for (var page = 0; page < pageCount; page++) {
      if (_isFinal(decisions[page])) count++;
    }
    return count;
  }

  static int deferredCount(int pageCount, Map<int, String> decisions) {
    if (pageCount <= 0) return 0;
    var count = 0;
    for (var page = 0; page < pageCount; page++) {
      if (decisions[page] == 'review') count++;
    }
    return count;
  }

  /// The number of pages without a final A/B/Tie decision. This includes
  /// explicitly deferred pages and pages that have not been classified yet.
  static int pendingCount(int pageCount, Map<int, String> decisions) =>
      pageCount <= 0 ? 0 : pageCount - decidedCount(pageCount, decisions);

  /// Finds the next pending page after [currentPage], wrapping at the end.
  /// Returns null when every page has a final decision or there are no pages.
  static int? nextPendingPage({
    required int pageCount,
    required int currentPage,
    required Map<int, String> decisions,
  }) {
    if (pageCount <= 0) return null;
    final current = ((currentPage % pageCount) + pageCount) % pageCount;
    for (var offset = 1; offset <= pageCount; offset++) {
      final candidate = (current + offset) % pageCount;
      if (!_isFinal(decisions[candidate])) return candidate;
    }
    return null;
  }
}
