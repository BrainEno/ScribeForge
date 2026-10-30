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

  test('parses active page and stage details for live processing progress', () {
    final withActiveJob = payload.replaceFirst(
      '"failed_jobs": [',
      '"active_jobs": [{"stage":"secondary_ocr","page_index":8,"attempts":2}],\n' +
          '"failed_jobs": [',
    );

    final result = ProjectStatusSnapshot.fromJsonText(
      withActiveJob,
      sourcePath: '/books/Novel.pdf',
    );

    expect(result?.activeJobs.single.stage, 'secondary_ocr');
    expect(result?.activeJobs.single.pageIndex, 8);
    expect(result?.activeJobs.single.attempts, 2);
    expect(result?.activeJobs.single.location, 'secondary_ocr · page 9');
  });

  test('selects matching source when a project store includes several projects', () {
    const projects = '''
    {
      "schema_version": 1,
      "store": "store",
      "projects": [
        {
          "id": 1, "title": "First", "source_uri": "/books/first.pdf", "source_sha256": "first-hash",
          "pages_total": 1, "pages_with_both_ocr_success": 1, "review_queued": 0,
          "job_counts": {"pending": 0, "running": 0, "succeeded": 3, "failed": 0},
          "failed_jobs": []
        },
        {
          "id": 2, "title": "Second", "source_uri": "/books/second.pdf", "source_sha256": "second-hash",
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
        {
          "id": 1, "title": "A", "source_uri": "/a.pdf", "source_sha256": "a",
          "pages_total": 1, "pages_with_both_ocr_success": 0, "review_queued": 0,
          "job_counts": {"pending": 0, "running": 0, "succeeded": 0, "failed": 0}, "failed_jobs": []
        },
        {
          "id": 2, "title": "B", "source_uri": "/b.pdf", "source_sha256": "b",
          "pages_total": 1, "pages_with_both_ocr_success": 0, "review_queued": 0,
          "job_counts": {"pending": 0, "running": 0, "succeeded": 0, "failed": 0}, "failed_jobs": []
        }
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


  test('identifies source path and SHA-256 mismatches instead of reusing status', () {
    final projects = ProjectStatusSnapshot.listFromJsonText(payload);
    final status = projects.single;

    expect(status.sourcePathMatches('/books/other.pdf'), isFalse);
    expect(status.sourceHashMatches('changed-hash'), isFalse);
    expect(status.sourceHashMatches('abc'), isTrue);
    expect(
      ProjectStatusSnapshot.fromJsonText(
        payload,
        sourcePath: '/books/other.pdf',
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
