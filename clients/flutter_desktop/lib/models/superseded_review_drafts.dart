import 'review_triage.dart';

/// Local scratch text, never a decision or an input to the CLI.
class SupersededReviewDraft {
  const SupersededReviewDraft({
    required this.key,
    required this.token,
    required this.text,
  });

  final ReviewItemKey key;
  final String? token;
  final String text;

  String get location => '项目 ${key.$1} · 第 ${key.$2 + 1} 页 · 第 ${key.$3 + 1} 组';
}

/// Session-only, bounded recovery buffer. It has no restore-to-editor API.
class SupersededReviewDrafts {
  final _entries = <SupersededReviewDraft>[];
  static const capacity = 30;

  bool get isNotEmpty => _entries.isNotEmpty;
  List<SupersededReviewDraft> get newestFirst =>
      List<SupersededReviewDraft>.unmodifiable(_entries.reversed);

  void remember(SupersededReviewDraft draft) {
    if (draft.text.isEmpty) return;
    _entries.removeWhere(
      (old) =>
          old.key == draft.key &&
          old.token == draft.token &&
          old.text == draft.text,
    );
    _entries.add(draft);
    if (_entries.length > capacity) _entries.removeAt(0);
  }

  void remove(SupersededReviewDraft draft) => _entries.remove(draft);

  void clear() => _entries.clear();
}
