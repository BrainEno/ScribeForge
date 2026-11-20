import 'dart:convert';

import 'ocr_evidence.dart';

enum ExportVerificationStatus {
  consistent('consistent', '导出凭据一致'),
  mismatch('mismatch', '文件或凭据不一致'),
  legacyMatch('legacy_match', '旧版规范化报告匹配'),
  noReceipt('no_receipt', '没有可用的导出凭据');

  const ExportVerificationStatus(this.value, this.label);
  final String value;
  final String label;

  static ExportVerificationStatus parse(String text) {
    final result = values.where((status) => status.value == text).firstOrNull;
    if (result == null) throw const FormatException('未知的导出验证结果。');
    return result;
  }

  String get explanation => switch (this) {
    consistent => '当前成品和规范化报告的字节与本地凭据相符。',
    mismatch => '一份或多份文件与记录不符，或记录本身无法解析。请保留这些文件，另从可信来源找回成品。',
    legacyMatch => '旧版报告所记的成品摘要相符，但没有完整凭据，不能核对项目来源。',
    noReceipt => '可以读取文件指纹，但没有可比较的记录。不要仅因文件可打开就判定它来自本项目。',
  };
}

class ExportFileFingerprint {
  ExportFileFingerprint(EvidenceFields fields)
    : size = fields.index('size'),
      digest = fields.text('sha256') {
    if (!isSha256(digest)) throw const FormatException('文件摘要不是 SHA-256。');
  }

  static final _pattern = RegExp(r'^[a-f0-9]{64}$');
  static bool isSha256(String value) =>
      value.length == 64 && _pattern.hasMatch(value);

  final int size;
  final String digest;

  bool sameAs(ExportFileFingerprint other) =>
      size == other.size && digest == other.digest;
}

class ExportReceiptDetails {
  ExportReceiptDetails(EvidenceFields fields)
    : file = fields.text('file'),
      format = fields.text('format'),
      artifact = ExportFileFingerprint(fields.object('artifact')),
      normalization = ExportFileFingerprint(fields.object('normalization')),
      policy = fields.text('policy'),
      projectId = fields.index('project_id'),
      title = fields.text('title'),
      sourceDigest = fields.optionalText('source_sha256'),
      modified = fields.text('modified'),
      strict = fields.flag('strict_acceptance'),
      furniture = fields.flag('include_page_furniture'),
      pages = fields.index('pages'),
      acceptedPairs = fields.index('accepted_pairs') {
    if (artifact.size == 0 ||
        normalization.size == 0 ||
        pages == 0 ||
        projectId == 0 ||
        (sourceDigest != null &&
            !ExportFileFingerprint.isSha256(sourceDigest!))) {
      throw const FormatException('导出凭据包含无效的计数或来源。');
    }
  }

  final String file;
  final String format;
  final ExportFileFingerprint artifact;
  final ExportFileFingerprint normalization;
  final String policy;
  final int projectId;
  final String title;
  final String? sourceDigest;
  final String modified;
  final bool strict;
  final bool furniture;
  final int pages;
  final int acceptedPairs;

  String get policyLabel => switch (policy) {
    'preserve' => '保留来源文字',
    'canonical_variants' => '统一已知字形变体',
    'simplified' => '转换为简体',
    _ => policy,
  };
}

enum ExportFindingSeverity { error, warning, info }

class ExportVerificationFinding {
  ExportVerificationFinding(EvidenceFields fields)
    : code = fields.text('code'),
      message = fields.text('message'),
      severity = _severity(fields.text('severity'));

  static ExportFindingSeverity _severity(String text) {
    final severity = ExportFindingSeverity.values
        .where((value) => value.name == text)
        .firstOrNull;
    if (severity == null) throw const FormatException('未知的导出验证诊断等级。');
    return severity;
  }

  final String code;
  final String message;
  final ExportFindingSeverity severity;

  String get title => switch (code) {
    'receipt_missing' => '找不到导出凭据',
    'receipt_invalid' => '导出凭据不可读',
    'artifact_digest_mismatch' => '成品字节已变化',
    'format_mismatch' => '文件扩展名与导出格式不同',
    'filename_differs' || 'normalization_filename_differs' => '文件名与导出时不同',
    'project_source_mismatch' => '来源与当前项目不符',
    'project_source_unknown' => '无法核对项目来源',
    'normalization_missing' => '找不到规范化报告',
    'normalization_invalid' => '规范化报告不可读',
    'normalization_artifact_mismatch' => '规范化报告引用了不同成品',
    'normalization_digest_mismatch' => '规范化报告字节已变化',
    'normalization_policy_mismatch' => '规范化策略不一致',
    _ => code,
  };
}

class ExportVerificationReport {
  ExportVerificationReport.fromText(
    String source, {
    required String filename,
    required String? expectedSource,
  }) : this._parse(
         EvidenceFields(jsonDecode(source)),
         filename,
         expectedSource,
       );

  ExportVerificationReport._parse(
    EvidenceFields fields,
    String filename,
    String? expectedSource,
  ) : file = fields.text('file'),
      format = fields.text('format'),
      status = ExportVerificationStatus.parse(fields.text('status')),
      actual = ExportFileFingerprint(fields.object('artifact')),
      normalizationMatchesArtifact = fields.flag(
        'normalization_matches_artifact',
      ),
      sourceMatches = _optionalFlag(fields, 'project_source_matches'),
      receipt = _receipt(fields),
      findings = List<ExportVerificationFinding>.unmodifiable(
        fields.rows('findings').map(ExportVerificationFinding.new),
      ) {
    final hasError = findings.any(
      (item) => item.severity == ExportFindingSeverity.error,
    );
    final details = receipt;
    final source = details?.sourceDigest;
    if (fields.index('schema_version') != 1 ||
        file != filename ||
        !const {'epub', 'docx'}.contains(format) ||
        !filename.toLowerCase().endsWith('.$format') ||
        hasError != (status == ExportVerificationStatus.mismatch) ||
        (status == ExportVerificationStatus.consistent &&
            (details == null ||
                details.format != format ||
                !actual.sameAs(details.artifact) ||
                !normalizationMatchesArtifact)) ||
        (status == ExportVerificationStatus.legacyMatch &&
            (details != null || !normalizationMatchesArtifact)) ||
        (status == ExportVerificationStatus.noReceipt &&
            (details != null || normalizationMatchesArtifact)) ||
        sourceMatches !=
            (source == null || expectedSource == null
                ? null
                : source == expectedSource)) {
      throw const FormatException('导出校验返回的文件或状态不一致。');
    }
  }

  static bool? _optionalFlag(EvidenceFields fields, String key) =>
      fields.optionalFlag(key);

  static ExportReceiptDetails? _receipt(EvidenceFields fields) {
    final value = fields.optionalObject('receipt');
    return value == null ? null : ExportReceiptDetails(value);
  }

  final String file;
  final String format;
  final ExportVerificationStatus status;
  final ExportFileFingerprint actual;
  final bool normalizationMatchesArtifact;
  final bool? sourceMatches;
  final ExportReceiptDetails? receipt;
  final List<ExportVerificationFinding> findings;

  String describe() => <String>[
    'ScribeForge 导出文件校验（不是数字签名）',
    '$file · ${status.label}',
    '实测：${actual.size} 字节 · SHA-256 ${actual.digest}',
    if (receipt case final saved?) ...<String>[
      '导出时记录：${saved.artifact.size} 字节 · SHA-256 ${saved.artifact.digest}',
      '凭据项目：${saved.title} · #${saved.projectId}',
      if (saved.sourceDigest != null) '凭据来源 SHA-256：${saved.sourceDigest}',
    ],
    if (sourceMatches != null) '与当前项目来源：${sourceMatches! ? '一致' : '不一致'}',
    for (final finding in findings) '${finding.title}：${finding.message}',
  ].join('\n');
}
