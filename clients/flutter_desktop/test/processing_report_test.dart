import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/processing_report.dart';

const String _reportJson = '''
{
  "schema_version": 1,
  "project_id": 7,
  "title": "Study",
  "pages_total": 4,
  "ocr_complete_pages": 3,
  "failed_pages": 1,
  "running_pages": 0,
  "review": {
    "pairs": 10,
    "accepted_automatic": 6,
    "accepted_vlm": 1,
    "accepted_human": 1,
    "open_pairs": 2,
    "open_high_risk": 1,
    "open_blocked": 0,
    "review_required_ratio": 0.4
  },
  "risk_reasons": {"low_confidence": 3},
  "risk_distribution": [
    {"low": 5, "high": 0, "count": 5},
    {"low": 0, "high": 2, "count": 2}
  ],
  "high_risk_pairs_total": 2,
  "timing": {
    "scope": "job wall-clock",
    "observed_seconds": 123.4,
    "timed_jobs": 8,
    "unavailable_jobs": 1,
    "bottleneck_stage": "primary_ocr",
    "engines": [
      {
        "engine": "mineru",
        "stage": "primary_ocr",
        "timed_jobs": 4,
        "unavailable_jobs": 0,
        "observed_seconds": 100.0,
        "share": 0.81,
        "mean_seconds": 25.0
      },
      {
        "engine": "paddleocr",
        "stage": "secondary_ocr",
        "timed_jobs": 4,
        "unavailable_jobs": 1,
        "observed_seconds": null,
        "share": null,
        "mean_seconds": null
      }
    ]
  },
  "slowest_pages": [
    {"page_index": 2, "observed_seconds": 60.5},
    {"page_index": 0, "observed_seconds": null}
  ],
  "stages": [
    {
      "stage": "primary_ocr",
      "jobs": 4,
      "pending": 0,
      "running": 0,
      "succeeded": 3,
      "failed": 1,
      "attempts": 5,
      "observed_seconds": 100.0
    }
  ],
  "pages": [
    {
      "page_index": 0,
      "status": "done",
      "aligned_pairs": 3,
      "high_risk_pairs": 0,
      "open_pairs": 0,
      "risk_reasons": {},
      "observed_seconds": 30.0,
      "stage_timings": []
    },
    {
      "page_index": 2,
      "status": "needs_review",
      "aligned_pairs": 4,
      "high_risk_pairs": 2,
      "open_pairs": 2,
      "risk_reasons": {"low_confidence": 2},
      "observed_seconds": null,
      "stage_timings": []
    }
  ]
}
''';

void main() {
  group('ProcessingReportSnapshot', () {
    test('parses the schema-v1 payload and keeps N/A fields nullable', () {
      final report = ProcessingReportSnapshot.fromJsonText(_reportJson);
      expect(report.projectId, 7);
      expect(report.title, 'Study');
      expect(report.pagesTotal, 4);
      expect(report.failedPages, 1);
      expect(report.review.openPairs, 2);
      expect(report.review.reviewRequiredRatio, 0.4);
      expect(report.riskReasons, {'low_confidence': 3});
      expect(report.riskDistribution.length, 2);
      expect(report.highRiskPairsTotal, 2);
      expect(report.timing.observedSeconds, 123.4);
      expect(report.timing.bottleneckStage, 'primary_ocr');
      expect(report.timing.engines.length, 2);
      expect(report.timing.engines[0].share, 0.81);
      // Unmeasured engine stays N/A (null), never 0.
      expect(report.timing.engines[1].observedSeconds, isNull);
      expect(report.timing.engines[1].share, isNull);
      expect(report.slowestPages[1].observedSeconds, isNull);
      expect(report.pages[1].observedSeconds, isNull);
      expect(report.rawJson, _reportJson);
    });

    test('pagesNeedingReview selects pages with open or high-risk pairs', () {
      final report = ProcessingReportSnapshot.fromJsonText(_reportJson);
      expect(report.pagesNeedingReview.map((page) => page.pageIndex), [2]);
    });

    test('rejects a wrong schema version', () {
      expect(
        () => ProcessingReportSnapshot.fromJsonText('{"schema_version": 2}'),
        throwsFormatException,
      );
    });

    test('markdown renders N/A explicitly instead of 0', () {
      final report = ProcessingReportSnapshot.fromJsonText(_reportJson);
      final markdown = processingReportMarkdown(report);
      expect(markdown, contains('# 处理报告：Study'));
      expect(markdown, contains('实测总耗时：123.4 秒'));
      expect(markdown, contains('瓶颈阶段：primary_ocr'));
      expect(markdown, contains('复核率：40.0%'));
      expect(markdown, contains('| paddleocr |'));
      expect(markdown, contains('N/A（未测量）'));
      expect(markdown, contains('建议抽查页'));
      expect(markdown, contains('第 3 页'));
    });
  });
}
