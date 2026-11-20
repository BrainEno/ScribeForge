import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'ocr_evidence.dart';

enum TextlessPageKind {
  blank('blank', '空白页', '没有需要转录的文字，也没有需要随导出保留的图像。'),
  nonText('non_text', '无文字插图', '有应保留的图片内容，但没有标题、题注或其他待转录文字。');

  const TextlessPageKind(this.value, this.label, this.description);
  final String value;
  final String label;
  final String description;

  static TextlessPageKind parse(String value) {
    final match = values.where((kind) => kind.value == value).firstOrNull;
    if (match == null) throw const FormatException('未知的无文字页类型。');
    return match;
  }
}

class TextlessHistoryEntry {
  TextlessHistoryEntry(EvidenceFields fields)
    : id = fields.index('id'),
      kind = TextlessPageKind.parse(fields.text('kind')),
      actor = fields.text('actor'),
      reason = fields.text('reason'),
      reasonTruncated = fields.flag('reason_truncated'),
      createdAt = fields.text('created_at'),
      verification = fields.optionalIndex('verification_id'),
      binding = fields.text('binding') {
    if (id < 1 ||
        !const {'current', 'historical', 'legacy'}.contains(binding) ||
        (verification == null) != (binding == 'legacy')) {
      throw const FormatException('无文字页历史记录的证据绑定无效。');
    }
  }

  final int id;
  final TextlessPageKind kind;
  final String actor;
  final String reason;
  final bool reasonTruncated;
  final String createdAt;
  final int? verification;
  final String binding;

  String get bindingLabel => switch (binding) {
    'current' => '引用当前核验和页图',
    'historical' => '引用历史证据',
    _ => '旧记录，未绑定核验和页图',
  };
}

class TextlessPreview {
  TextlessPreview.fromText(
    String text, {
    required int project,
    required int page,
  }) : this._parse(EvidenceFields(jsonDecode(text)), project, page);

  TextlessPreview._parse(EvidenceFields fields, this.project, this.page)
    : verification = fields.index('verification_id'),
      digest = fields.text('raster_sha256'),
      width = fields.index('width'),
      height = fields.index('height'),
      token = fields.optionalText('confirmation_token'),
      blockedReason = fields.optionalText('blocked_reason'),
      history = List<TextlessHistoryEntry>.unmodifiable(
        fields.rows('history').map(TextlessHistoryEntry.new),
      ),
      truncated = fields.flag('history_truncated'),
      raster = _decode(fields.text('raster_base64')) {
    if (fields.index('schema_version') != 1 ||
        fields.index('project_id') != project ||
        fields.index('page_index') != page ||
        verification < 1 ||
        width < 1 ||
        height < 1 ||
        width * height > maximumPixels ||
        !_digestPattern.hasMatch(digest) ||
        sha256.convert(raster).toString() != digest ||
        (token == null) == (blockedReason == null) ||
        (token != null &&
            (!_tokenPattern.hasMatch(token!) ||
                !token!.endsWith(':v$verification:$digest')))) {
      throw const FormatException('原页校验、项目范围或确认版本不匹配。');
    }
    var previousId = 0;
    for (final entry in history.reversed) {
      if (entry.id <= previousId ||
          (entry.binding == 'current' && entry.verification != verification)) {
        throw const FormatException('无文字页历史的记录顺序或绑定无效。');
      }
      previousId = entry.id;
    }
  }

  static const maximumRasterBytes = 12 * 1024 * 1024;
  static const maximumPixels = 24000000;
  static const maximumReasonLength = 1000;
  static final _digestPattern = RegExp(r'^[a-f0-9]{64}$');
  static final _tokenPattern = RegExp(
    r'^p[1-9][0-9]{0,18}:v[1-9][0-9]{0,18}:[a-f0-9]{64}$',
  );

  final int project;
  final int page;
  final int verification;
  final int width;
  final int height;
  final String digest;
  final String? token;
  final String? blockedReason;
  final List<TextlessHistoryEntry> history;
  final bool truncated;
  final Uint8List raster;

  static Uint8List _decode(String source) {
    if (source.isEmpty || source.length > (maximumRasterBytes * 4 ~/ 3 + 4)) {
      throw const FormatException('原页预览超出大小限制。');
    }
    final data = base64Decode(source);
    if (data.isEmpty || data.length > maximumRasterBytes) {
      throw const FormatException('原页预览无效。');
    }
    return data;
  }
}

class TextlessReceipt {
  TextlessReceipt.fromText(
    String text,
    TextlessPreview preview,
    TextlessPageKind expected,
  ) : this._parse(EvidenceFields(jsonDecode(text)), preview, expected);

  TextlessReceipt._parse(
    EvidenceFields fields,
    TextlessPreview preview,
    this.kind,
  ) : id = fields.index('id') {
    if (id < 1 ||
        fields.index('schema_version') != 1 ||
        fields.index('project_id') != preview.project ||
        fields.index('page_index') != preview.page ||
        fields.text('kind') != kind.value) {
      throw const FormatException('已返回的确认回执无法核对，请刷新历史后再操作。');
    }
  }

  final int id;
  final TextlessPageKind kind;
}
