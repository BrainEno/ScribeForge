import 'dart:convert';
import 'dart:io';

class FailedJobSummary {
  const FailedJobSummary({
    required this.stage,
    required this.pageIndex,
    required this.attempts,
    required this.lastError,
  });

  final String stage;
  final int? pageIndex;
  final int attempts;
  final String? lastError;

  String get location =>
      pageIndex == null ? stage : '$stage · page ${pageIndex! + 1}';

  String get description {
    final detail = lastError?.trim();
    if (detail == null || detail.isEmpty) return '$location failed';
    return '$location: $detail';
  }

  static FailedJobSummary fromJson(Map<String, dynamic> json) {
    final stage = json['stage'];
    final pageIndex = json['page_index'];
    final attempts = json['attempts'];
    final lastError = json['last_error'];
    if (stage is! String || (pageIndex != null && pageIndex is! int) || attempts is! int) {
      throw const FormatException('Invalid failed-job status record.');
    }
    return FailedJobSummary(
      stage: stage,
      pageIndex: pageIndex as int?,
      attempts: attempts,
      lastError: lastError is String ? lastError : null,
    );
  }
}

class ActiveJobSummary {
  const ActiveJobSummary({
    required this.stage,
    required this.pageIndex,
    required this.attempts,
  });

  final String stage;
  final int? pageIndex;
  final int attempts;

  String get location =>
      pageIndex == null ? stage : '$stage · page ${pageIndex! + 1}';

  static ActiveJobSummary fromJson(Map<String, dynamic> json) {
    final stage = json['stage'];
    final pageIndex = json['page_index'];
    final attempts = json['attempts'];
    if (stage is! String || (pageIndex != null && pageIndex is! int) || attempts is! int) {
      throw const FormatException('Invalid active-job status record.');
    }
    return ActiveJobSummary(
      stage: stage,
      pageIndex: pageIndex as int?,
      attempts: attempts,
    );
  }
}

class ProjectStatusSnapshot {
  const ProjectStatusSnapshot({
    required this.projectId,
    required this.title,
    required this.sourceUri,
    required this.sourceSha256,
    required this.pagesTotal,
    required this.pagesWithBothOcrSuccess,
    required this.reviewQueued,
    required this.pendingJobs,
    required this.runningJobs,
    required this.succeededJobs,
    required this.failedJobs,
    required this.failedJobDetails,
    required this.activeJobs,
  });

  final int projectId;
  final String title;
  final String sourceUri;
  final String? sourceSha256;
  final int pagesTotal;
  final int pagesWithBothOcrSuccess;
  final int reviewQueued;
  final int pendingJobs;
  final int runningJobs;
  final int succeededJobs;
  final int failedJobs;
  final List<FailedJobSummary> failedJobDetails;
  final List<ActiveJobSummary> activeJobs;

  ActiveJobSummary? get currentJob => activeJobs.isEmpty ? null : activeJobs.first;

  double get ocrProgress {
    if (pagesTotal <= 0) return 0;
    return (pagesWithBothOcrSuccess / pagesTotal).clamp(0.0, 1.0).toDouble();
  }

  String get progressLabel => '$pagesWithBothOcrSuccess / $pagesTotal pages have both OCR results';

  String get jobsLabel =>
      '$succeededJobs succeeded · $pendingJobs pending · $runningJobs running · $failedJobs failed';

  bool sourcePathMatches(String path) =>
      _normalizedPath(sourceUri) == _normalizedPath(path);

  bool sourceHashMatches(String? hash) =>
      sourceSha256 != null && hash != null && sourceSha256 == hash;

  static List<ProjectStatusSnapshot> listFromJsonText(String raw) {
    final decoded = jsonDecode(raw);
    if (decoded is! Map<String, dynamic> || decoded['schema_version'] != 1) {
      throw const FormatException('Unsupported ScribeForge status JSON schema.');
    }
    final rawProjects = decoded['projects'];
    if (rawProjects is! List) {
      throw const FormatException('Status JSON is missing the projects collection.');
    }
    return rawProjects
        .whereType<Map>()
        .map((item) => _fromProject(Map<String, dynamic>.from(item)))
        .toList(growable: false);
  }

  static ProjectStatusSnapshot? fromJsonText(
    String raw, {
    required String sourcePath,
  }) {
    final projects = listFromJsonText(raw);
    final matching = projects
        .where((project) => project.sourcePathMatches(sourcePath))
        .toList(growable: false);
    return matching.length == 1 ? matching.single : null;
  }

  static ProjectStatusSnapshot _fromProject(Map<String, dynamic> selected) {
    final id = selected['id'];
    final title = selected['title'];
    final sourceUri = selected['source_uri'];
    final sourceSha256 = selected['source_sha256'];
    final pagesTotal = selected['pages_total'];
    final pagesWithBothOcrSuccess = selected['pages_with_both_ocr_success'];
    final reviewQueued = selected['review_queued'];
    final rawJobCounts = selected['job_counts'];
    final rawFailedJobs = selected['failed_jobs'];
    final rawActiveJobs = selected['active_jobs'];
    if (id is! int ||
        title is! String ||
        sourceUri is! String ||
        (sourceSha256 != null && sourceSha256 is! String) ||
        pagesTotal is! int ||
        pagesWithBothOcrSuccess is! int ||
        reviewQueued is! int ||
        rawJobCounts is! Map ||
        rawFailedJobs is! List ||
        (rawActiveJobs != null && rawActiveJobs is! List)) {
      throw const FormatException('Status JSON has invalid project fields.');
    }
    final counts = Map<String, dynamic>.from(rawJobCounts);
    int count(String key) {
      final value = counts[key];
      if (value is! int) {
        throw FormatException('Status JSON is missing job count "$key".');
      }
      return value;
    }

    final failedDetails = rawFailedJobs
        .whereType<Map>()
        .map((item) => FailedJobSummary.fromJson(Map<String, dynamic>.from(item)))
        .toList(growable: false);
    final activeJobDetails = (rawActiveJobs is List ? rawActiveJobs : const <Object?>[])
        .whereType<Map>()
        .map((item) => ActiveJobSummary.fromJson(Map<String, dynamic>.from(item)))
        .toList(growable: false);
    return ProjectStatusSnapshot(
      projectId: id,
      title: title,
      sourceUri: sourceUri,
      sourceSha256: sourceSha256 as String?,
      pagesTotal: pagesTotal,
      pagesWithBothOcrSuccess: pagesWithBothOcrSuccess,
      reviewQueued: reviewQueued,
      pendingJobs: count('pending'),
      runningJobs: count('running'),
      succeededJobs: count('succeeded'),
      failedJobs: count('failed'),
      failedJobDetails: failedDetails,
      activeJobs: activeJobDetails,
    );
  }

  static String _normalizedPath(String path) {
    final absolute = File(path).absolute.path.replaceAll(r'\', '/');
    return Platform.isWindows ? absolute.toLowerCase() : absolute;
  }
}
