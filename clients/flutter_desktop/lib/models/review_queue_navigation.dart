/// Pure navigation rules for the human review queue.
///
/// Navigation intentionally has no decision/acceptance side effects. It only
/// selects an item; the separate "Record human decision" action is the only UI
/// operation that persists a decision.
class ReviewQueueNavigation {
  const ReviewQueueNavigation._();

  static int move(int currentIndex, {required int itemCount, required int delta}) {
    if (itemCount <= 0) return -1;
    return (currentIndex + delta).clamp(0, itemCount - 1).toInt();
  }

  /// Returns the first queue index for a one-based physical page number.
  static int? indexForPage(List<int> pageNumbers, {required int pageNumber}) {
    if (pageNumber < 1) return null;
    final index = pageNumbers.indexOf(pageNumber);
    return index < 0 ? null : index;
  }
}
