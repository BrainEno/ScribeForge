import 'dart:convert';

const jobStageLabels = <String, String>{
  'import': '导入',
  'preprocess': '图像预处理',
  'primary_ocr': 'OCR 引擎一',
  'secondary_ocr': 'OCR 引擎二',
  'verify': '比对与风险评估',
  'vlm_review': '视觉模型复核',
};

class JobAttempt {
  const JobAttempt({
    required this.id,
    required this.jobId,
    required this.attempt,
    required this.outcome,
    required this.origin,
    required this.stage,
    required this.currentState,
    required this.pageIndex,
    required this.error,
    required this.errorTruncated,
    required this.startedAt,
    required this.finishedAt,
    required this.recordedAt,
  });
  final int id, jobId, attempt;
  final String outcome, origin, stage, currentState;
  final int? pageIndex;
  final String? error, startedAt, finishedAt;
  final bool errorTruncated;
  final String recordedAt;

  String get location => pageIndex == null ? '整书任务' : '物理第 ${pageIndex! + 1} 页';
  String get stageLabel => jobStageLabels[stage] ?? stage;
  String get outcomeLabel => outcome == 'failed' ? '失败' : '中断后被重新接管';
  String get currentLabel => switch (currentState) {
    'pending' => '等待中',
    'running' => '运行中',
    'failed' => '失败',
    'succeeded' => '已成功',
    _ => '未知（$currentState）',
  };

  factory JobAttempt.fromJson(Map<String, dynamic> row) {
    final id = row['id'], job = row['job_id'], attempt = row['attempt'];
    final outcome = row['outcome'],
        origin = row['origin'],
        stage = row['stage'];
    final state = row['current_state'], page = row['page_index'];
    final error = row['error_preview'], truncated = row['error_truncated'];
    final start = row['started_at'],
        end = row['finished_at'],
        recorded = row['recorded_at'];
    if (id is! int ||
        id <= 0 ||
        job is! int ||
        job <= 0 ||
        attempt is! int ||
        attempt <= 0 ||
        !const ['failed', 'interrupted'].contains(outcome) ||
        !const ['observed', 'legacy_snapshot'].contains(origin) ||
        stage is! String ||
        !jobStageLabels.containsKey(stage) ||
        state is! String ||
        !const ['pending', 'running', 'failed', 'succeeded'].contains(state) ||
        (page != null && (page is! int || page < 0)) ||
        (error != null && error is! String) ||
        truncated is! bool ||
        (start != null && start is! String) ||
        (end != null && end is! String) ||
        recorded is! String ||
        (outcome == 'interrupted' && (error != null || truncated)) ||
        (outcome == 'failed' && (error is! String || error.isEmpty))) {
      throw const FormatException('失败历史记录格式错误。');
    }
    return JobAttempt(
      id: id,
      jobId: job,
      attempt: attempt,
      outcome: outcome as String,
      origin: origin as String,
      stage: stage,
      currentState: state,
      pageIndex: page as int?,
      error: error as String?,
      errorTruncated: truncated,
      startedAt: start as String?,
      finishedAt: end as String?,
      recordedAt: recorded,
    );
  }
}

class JobHistoryBatch {
  const JobHistoryBatch({
    required this.projectId,
    required this.available,
    required this.rows,
    required this.nextBefore,
  });
  final int projectId;
  final bool available;
  final List<JobAttempt> rows;
  final int? nextBefore;

  factory JobHistoryBatch.fromText(String text) {
    final json = jsonDecode(text);
    if (json is! Map ||
        json['schema_version'] != 1 ||
        json['project_id'] is! int ||
        (json['project_id'] as int) <= 0 ||
        json['history_available'] is! bool ||
        json['rows'] is! List ||
        (json['next_before'] != null &&
            (json['next_before'] is! int ||
                (json['next_before'] as int) <= 0))) {
      throw const FormatException('失败历史响应格式错误。');
    }
    final rows = <JobAttempt>[];
    for (final row in json['rows'] as List) {
      if (row is! Map) throw const FormatException('失败历史记录格式错误。');
      rows.add(JobAttempt.fromJson(Map<String, dynamic>.from(row)));
    }
    final available = json['history_available'] as bool;
    final next = json['next_before'] as int?;
    for (var i = 1; i < rows.length; i++) {
      if (rows[i - 1].id <= rows[i].id)
        throw const FormatException('失败历史排序无效。');
    }
    if ((!available && (rows.isNotEmpty || next != null)) ||
        (next != null && (rows.isEmpty || next != rows.last.id))) {
      throw const FormatException('失败历史分页无效。');
    }
    return JobHistoryBatch(
      projectId: json['project_id'] as int,
      available: available,
      rows: List.unmodifiable(rows),
      nextBefore: next,
    );
  }
}
