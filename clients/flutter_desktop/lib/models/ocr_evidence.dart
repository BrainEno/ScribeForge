import 'dart:convert';

enum OcrEvidenceSection { pages, page, lines, pairs }

class OcrEvidenceRequest {
  const OcrEvidenceRequest(this.section, {this.page, this.run, this.after});

  final OcrEvidenceSection section;
  final int? page;
  final int? run;
  final int? after;
}

class OcrEvidencePayload {
  OcrEvidencePayload.fromText(
    String text,
    int projectId,
    OcrEvidenceRequest request,
  ) : data = EvidenceFields(jsonDecode(text)) {
    if (data.index('schema_version') != 1 ||
        data.index('project_id') != projectId ||
        data.text('section') != request.section.name ||
        data.optionalIndex('page_index') != request.page ||
        data.optionalIndex('run_id') != request.run) {
      throw const FormatException('证据响应与当前项目、页码或批次不匹配。');
    }
  }

  final EvidenceFields data;
  String get projectTitle => data.text('project_title');

  OcrEvidenceBatch<T> batch<T>(
    String indexField,
    OcrEvidenceRequest request,
    T Function(EvidenceFields) decode,
  ) {
    final fields = data.rows('rows');
    var last = request.after ?? -1;
    for (final row in fields) {
      final index = row.index(indexField);
      if (index <= last) throw const FormatException('证据列表顺序有误。');
      last = index;
    }
    final cursor = data.optionalIndex('next_after');
    if (cursor != null && (fields.isEmpty || cursor != last)) {
      throw const FormatException('证据列表分页标记有误。');
    }
    return OcrEvidenceBatch<T>(
      fields.map(decode).toList(growable: false),
      cursor,
    );
  }
}

class OcrEvidenceBatch<T> {
  const OcrEvidenceBatch(this.rows, this.nextAfter);

  final List<T> rows;
  final int? nextAfter;
}

class OcrEvidencePage {
  OcrEvidencePage(EvidenceFields fields)
    : index = fields.index('page_index'),
      width = fields.index('width'),
      height = fields.index('height'),
      runCount = fields.index('ocr_runs'),
      latestVerification = fields.optionalIndex('verification');

  final int index;
  final int width;
  final int height;
  final int runCount;
  final int? latestVerification;

  String get statusLabel => switch ((runCount, latestVerification)) {
    (_, final id?) => '核验 #$id',
    (0, _) => '尚无 OCR',
    _ => '未核验',
  };
}

class OcrPageOverview {
  OcrPageOverview(EvidenceFields fields)
    : width = fields.index('width'),
      height = fields.index('height'),
      runs = List<OcrEvidenceRun>.unmodifiable(
        fields.rows('ocr_runs').map(OcrEvidenceRun.new),
      ),
      verifications = List<OcrVerificationRun>.unmodifiable(
        fields.rows('verifications').map(OcrVerificationRun.new),
      ),
      olderRunsOmitted = fields.flag('older_ocr_runs_omitted'),
      olderVerificationsOmitted = fields.flag('older_verifications_omitted');

  final int width;
  final int height;
  final List<OcrEvidenceRun> runs;
  final List<OcrVerificationRun> verifications;
  final bool olderRunsOmitted;
  final bool olderVerificationsOmitted;
}

class OcrEvidenceRun {
  OcrEvidenceRun(EvidenceFields fields)
    : id = fields.index('id'),
      engine = fields.text('engine'),
      version = fields.text('engine_version'),
      createdAt = fields.text('created_at'),
      lineCount = fields.index('line_count'),
      frameKind = fields.optionalText('frame_kind'),
      frameHash = fields.optionalText('frame_sha256'),
      frameWidth = fields.optionalIndex('frame_width'),
      frameHeight = fields.optionalIndex('frame_height');

  final int id;
  final String engine;
  final String version;
  final String createdAt;
  final int lineCount;
  final String? frameKind;
  final String? frameHash;
  final int? frameWidth;
  final int? frameHeight;

  String get title => '$engine · $version · #$id';
  String get frameLabel => switch (frameKind) {
    'original' => '原页坐标',
    'preprocessed' => '预处理图坐标',
    null => '未记录坐标系',
    final kind => kind,
  };
}

class OcrVerificationRun {
  OcrVerificationRun(EvidenceFields fields)
    : id = fields.index('id'),
      createdAt = fields.text('created_at'),
      primary = fields.index('primary_run_id'),
      secondary = fields.index('secondary_run_id'),
      pairCount = fields.index('pair_count');

  final int id;
  final String createdAt;
  final int primary;
  final int secondary;
  final int pairCount;
}

class OcrEvidenceLine {
  OcrEvidenceLine(EvidenceFields fields)
    : index = fields.index('line_index'),
      text = fields.text('text'),
      textTruncated = fields.flag('text_truncated'),
      x = fields.number('x'),
      y = fields.number('y'),
      width = fields.number('width'),
      height = fields.number('height'),
      tokenCount = fields.index('token_count'),
      minConfidence = fields.optionalNumber('min_confidence');

  final int index;
  final String text;
  final bool textTruncated;
  final double x;
  final double y;
  final double width;
  final double height;
  final int tokenCount;
  final double? minConfidence;

  String get coordinates =>
      <double>[x, y, width, height].map((v) => v.toStringAsFixed(4)).join(', ');
}

class OcrPairMember {
  OcrPairMember(EvidenceFields fields)
    : index = fields.index('line_index'),
      text = fields.optionalText('text'),
      truncated = fields.flag('text_truncated');

  final int index;
  final String? text;
  final bool truncated;
}

class OcrPairSide {
  OcrPairSide(EvidenceFields fields)
    : lines = List<OcrPairMember>.unmodifiable(
        fields.rows('lines').map(OcrPairMember.new),
      ),
      additional = fields.flag('additional_members');

  final List<OcrPairMember> lines;
  final bool additional;
}

class OcrEvidencePair {
  OcrEvidencePair(EvidenceFields fields)
    : index = fields.index('pair_index'),
      similarity = fields.number('text_similarity'),
      verticalDistance = fields.optionalNumber('vertical_distance'),
      risk = fields.optionalNumber('risk_score'),
      conflicts = fields.index('conflicts'),
      ambiguous = fields.flag('ambiguous'),
      primary = OcrPairSide(fields.object('primary')),
      secondary = OcrPairSide(fields.object('secondary'));

  final int index;
  final double similarity;
  final double? verticalDistance;
  final double? risk;
  final int conflicts;
  final bool ambiguous;
  final OcrPairSide primary;
  final OcrPairSide secondary;
}

class EvidenceFields {
  EvidenceFields(Object? value)
    : _fields = value is Map<String, dynamic>
          ? value
          : throw const FormatException('证据记录不是 JSON 对象。');

  final Map<String, dynamic> _fields;

  int index(String key) => optionalIndex(key) ?? (throw _invalid(key));
  int? optionalIndex(String key) {
    final value = _fields[key];
    if (value == null) return null;
    if (value is int && value >= 0) return value;
    throw _invalid(key);
  }

  double number(String key) => optionalNumber(key) ?? (throw _invalid(key));
  double? optionalNumber(String key) {
    final value = _fields[key];
    if (value == null) return null;
    if (value is num && value.isFinite) return value.toDouble();
    throw _invalid(key);
  }

  bool flag(String key) {
    final value = _fields[key];
    if (value is bool) return value;
    throw _invalid(key);
  }

  String text(String key) => optionalText(key) ?? (throw _invalid(key));
  String? optionalText(String key) {
    final value = _fields[key];
    if (value == null || value is String) return value as String?;
    throw _invalid(key);
  }

  EvidenceFields object(String key) => EvidenceFields(_fields[key]);

  EvidenceFields? optionalObject(String key) {
    final value = _fields[key];
    return value == null ? null : EvidenceFields(value);
  }

  List<EvidenceFields> rows(String key) {
    final value = _fields[key];
    if (value is! List) throw _invalid(key);
    return value.map(EvidenceFields.new).toList(growable: false);
  }

  List<String> strings(String key) {
    final value = _fields[key];
    if (value is! List || value.any((item) => item is! String)) {
      throw _invalid(key);
    }
    return List<String>.unmodifiable(value.cast<String>());
  }

  List<double> numbers(String key) {
    final value = _fields[key];
    if (value is! List || value.any((item) => item is! num || !item.isFinite)) {
      throw _invalid(key);
    }
    return List<double>.unmodifiable(
      value.cast<num>().map((item) => item.toDouble()),
    );
  }

  FormatException _invalid(String key) => FormatException('无效的证据字段：$key。');
}
