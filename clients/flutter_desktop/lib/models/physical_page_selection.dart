/// Parse human-facing physical page numbers, returning zero-based indices.
/// Printed book page numbers and PDF reader reflow pages are not equivalent.
List<int> parsePhysicalPages(String input, {int? sourcePages}) {
  final text = input.trim();
  if (text.isEmpty) throw const FormatException('请输入 PDF 的物理页码。');
  final pieces = text.split(RegExp(r'[,，、\s]+'));
  if (pieces.length > 100) throw const FormatException('一次最多选择 100 页。');
  final pages = <int>[];
  final seen = <int>{};
  for (final part in pieces) {
    if (!RegExp(r'^[0-9]+$').hasMatch(part)) {
      throw const FormatException('仅支持正整数物理页码，用逗号或空格分隔。');
    }
    final number = int.tryParse(part);
    if (number == null || number < 1 || number > 2147483647) {
      throw const FormatException('物理页码必须是大于零的有效整数。');
    }
    if (sourcePages != null && number > sourcePages) {
      throw FormatException('物理页码 $number 超过原书的 $sourcePages 页。');
    }
    if (!seen.add(number)) throw FormatException('物理页码 $number 重复。');
    pages.add(number - 1);
  }
  return List.unmodifiable(pages);
}
