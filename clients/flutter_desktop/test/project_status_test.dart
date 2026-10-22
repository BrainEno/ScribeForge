import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/project_status.dart';

void main() {
  const payload = '''
  {
    "schema_version": 1,
    "store": "/books/Novel.scribeforge",
    "projects": [
      {
        "id": 1,
        "title": "Novel",
        "source_uri": "/books/Novel.pdf",
        "source_sha256": "abc",
        "pages_total": 20,
        "pages_with_both_ocr_success": 14,
        "review_queued": 6,
        "job_counts": {
          "pending": 2,
          "running": 1,
          "succeeded": 42,
          "failed": 1
        },
        "failed_jobs": [
          {
            "stage": "secondary_ocr",
            "page_index": 7,
            "attempts": 2,
            "last_error": "worker timed out"
          }
        ]
      }
    ]
  }
  ''';

  test('parses status counts and useful failed-job context', () {
    final result = ProjectStatusSnapshot.fromJsonText(
      payload,
      sourcePath: '/books/Novel.pdf',
    );

    expect(result, isNotNull);
    expect(result!.pagesTotal, 20);
    expect(result.pagesWithBothOcrSuccess, 14);
    expect(result.ocrProgress, closeTo(0.7, 0.0001));
    expect(result.reviewQueued, 6);
    expect(result.runningJobs, 1);
    expect(result.failedJobs, 1);
    expect(result.failedJobDetails.single.location, 'secondary_ocr · page 8');
    expect(result.failedJobDetails.single.description, contains('worker timed out'));
  });

  test('selects matching source when a project store includes several projects', () {
    const projects = '''
    {
      "schema_version": 1,
      "store": "store",
      "projects": [
        {
          "id": 1, "title": "First", "source_uri": "/books/first.pdf",
          "pages_total": 1, "pages_with_both_ocr_success": 1, "review_queued": 0,
          "job_counts": {"pending": 0, "running": 0, "succeeded": 3, "failed": 0},
          "failed_jobs": []
        },
        {
          "id": 2, "title": "Second", "source_uri": "/books/second.pdf",
          "pages_total": 2, "pages_with_both_ocr_success": 0, "review_queued": 1,
          "job_counts": {"pending": 1, "running": 0, "succeeded": 1, "failed": 1},
          "failed_jobs": []
        }
      ]
    }
    ''';

    final result = ProjectStatusSnapshot.fromJsonText(
      projects,
      sourcePath: '/books/second.pdf',
    );

    expect(result?.projectId, 2);
    expect(result?.title, 'Second');
  });

  test('returns null for an empty store or ambiguous unmatched projects', () {
    expect(
      ProjectStatusSnapshot.fromJsonText(
        '{"schema_version":1,"projects":[]}',
        sourcePath: '/books/novel.pdf',
      ),
      isNull,
    );
    const unmatched = '''
    {
      "schema_version": 1,
      "projects": [
        {"id": 1, "source_uri": "/a.pdf"},
        {"id": 2, "source_uri": "/b.pdf"}
      ]
    }
    ''';
    expect(
      ProjectStatusSnapshot.fromJsonText(
        unmatched,
        sourcePath: '/books/novel.pdf',
      ),
      isNull,
    );
  });

  test('rejects an unsupported schema', () {
    expect(
      () => ProjectStatusSnapshot.fromJsonText(
        '{"schema_version":2,"projects":[]}',
        sourcePath: '/books/novel.pdf',
      ),
      throwsFormatException,
    );
  });
}
