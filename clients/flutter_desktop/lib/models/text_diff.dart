class TextDiffRow {
  const TextDiffRow({required this.left, required this.right, required this.changed});

  final String? left;
  final String? right;
  final bool changed;
}

List<TextDiffRow> diffLines(List<String> left, List<String> right) {
  if (left.length > 500 || right.length > 500) {
    final length = left.length > right.length ? left.length : right.length;
    return List<TextDiffRow>.generate(length, (index) {
      final a = index < left.length ? left[index] : null;
      final b = index < right.length ? right[index] : null;
      return TextDiffRow(left: a, right: b, changed: a != b);
    }, growable: false);
  }
  final dp = List<List<int>>.generate(
    left.length + 1,
    (_) => List<int>.filled(right.length + 1, 0),
    growable: false,
  );
  for (var i = left.length - 1; i >= 0; i--) {
    for (var j = right.length - 1; j >= 0; j--) {
      dp[i][j] = left[i] == right[j]
          ? dp[i + 1][j + 1] + 1
          : (dp[i + 1][j] >= dp[i][j + 1] ? dp[i + 1][j] : dp[i][j + 1]);
    }
  }

  final result = <TextDiffRow>[];
  var i = 0;
  var j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] == right[j]) {
      result.add(TextDiffRow(left: left[i], right: right[j], changed: false));
      i++;
      j++;
    } else if (i < left.length &&
        (j >= right.length || dp[i + 1][j] >= dp[i][j + 1])) {
      result.add(TextDiffRow(left: left[i], right: null, changed: true));
      i++;
    } else {
      result.add(TextDiffRow(left: null, right: right[j], changed: true));
      j++;
    }
  }
  return List.unmodifiable(result);
}

class TextDiffSummary {
  const TextDiffSummary({
    required this.leftLineCount,
    required this.rightLineCount,
    required this.sharedLines,
    required this.changedRows,
  });

  final int leftLineCount;
  final int rightLineCount;
  final int sharedLines;
  final int changedRows;
}

TextDiffSummary summarizeDiff(List<TextDiffRow> rows, List<String> left, List<String> right) {
  return TextDiffSummary(
    leftLineCount: left.length,
    rightLineCount: right.length,
    sharedLines: rows.where((row) => !row.changed).length,
    changedRows: rows.where((row) => row.changed).length,
  );
}
