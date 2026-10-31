import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

void main() {
  group('ScribeForgeCli path and argument helpers', () {
    test('requests structured JSON for runtime status', () {
      expect(
        ScribeForgeCli.runtimeStatusArguments(),
        <String>['runtime-status', '--json'],
      );
      expect(
        ScribeForgeCli.runtimeStatusArguments(runtimeRoot: '/tmp/ScribeForge runtime'),
        <String>[
          'runtime-status',
          '--runtime-root',
          '/tmp/ScribeForge runtime',
          '--json',
        ],
      );
    });

    test('requests streamed JSON progress and optional single-backend repair', () {
      expect(
        ScribeForgeCli.setupArguments(),
        <String>['setup', '--json'],
      );
      expect(
        ScribeForgeCli.setupArguments(
          runtimeRoot: '/tmp/ScribeForge runtime',
          backend: 'paddleocr',
        ),
        <String>[
          'setup',
          '--runtime-root',
          '/tmp/ScribeForge runtime',
          '--backend',
          'paddleocr',
          '--json',
        ],
      );
    });

    test('requests structured JSON for project status', () {
      expect(
        ScribeForgeCli.statusArguments(storePath: '/tmp/My Book.scribeforge'),
        <String>['status', '--store', '/tmp/My Book.scribeforge', '--json'],
      );
    });

    test('requests structured review evidence and resolves the selected project', () {
      expect(
        ScribeForgeCli.reviewArguments(storePath: '/tmp/My Book.scribeforge'),
        <String>['review', '--store', '/tmp/My Book.scribeforge', '--json'],
      );
      expect(
        ScribeForgeCli.resolveArguments(
          storePath: '/tmp/My Book.scribeforge',
          projectId: 42,
          page: 3,
          pair: 1,
          text: 'accepted literal text',
        ),
        <String>[
          'resolve',
          '--store',
          '/tmp/My Book.scribeforge',
          '--project-id',
          '42',
          '--page',
          '3',
          '--pair',
          '1',
          '--text',
          'accepted literal text',
        ],
      );
    });

    test('requests a JSON project audit for the selected store', () {
      expect(
        ScribeForgeCli.auditArguments(storePath: '/tmp/My Book.scribeforge'),
        <String>['audit', '--store', '/tmp/My Book.scribeforge', '--json'],
      );
    });

    test('builds backup, verification and restore arguments without splitting paths', () {
      expect(
        ScribeForgeCli.backupArguments(
          storePath: '/tmp/My Book.scribeforge',
          outputPath: '/backup/My Book.zip',
        ),
        <String>[
          'backup',
          '--store',
          '/tmp/My Book.scribeforge',
          '--out',
          '/backup/My Book.zip',
          '--json',
        ],
      );
      expect(
        ScribeForgeCli.backupArguments(
          storePath: '/tmp/My Book.scribeforge',
          outputPath: '/backup/My Book.zip',
          overwrite: true,
        ),
        contains('--overwrite'),
      );
      expect(
        ScribeForgeCli.verifyBackupArguments(backupPath: '/backup/My Book.zip'),
        <String>['verify-backup', '--backup', '/backup/My Book.zip', '--json'],
      );
      expect(
        ScribeForgeCli.restoreBackupArguments(
          backupPath: '/backup/My Book.zip',
          targetPath: '/restored/My Book.scribeforge',
        ),
        <String>[
          'restore',
          '--backup',
          '/backup/My Book.zip',
          '--to',
          '/restored/My Book.scribeforge',
          '--json',
        ],
      );
    });

    test('builds verified EPUB/DOCX export arguments and optional metadata safely', () {
      expect(
        ScribeForgeCli.exportArguments(
          storePath: '/tmp/My Book.scribeforge',
          outputPath: '/tmp/Verified Book.epub',
          title: 'Verified Book',
          author: 'A. Writer',
          language: 'zh-Hant',
          modified: '2026-10-22T00:00:00Z',
        ),
        <String>[
          'export',
          '--store',
          '/tmp/My Book.scribeforge',
          '--out',
          '/tmp/Verified Book.epub',
          '--language',
          'zh-Hant',
          '--title',
          'Verified Book',
          '--author',
          'A. Writer',
          '--modified',
          '2026-10-22T00:00:00Z',
        ],
      );
      expect(
        ScribeForgeCli.exportArguments(
          storePath: '/tmp/My Book.scribeforge',
          outputPath: '/tmp/Book.docx',
          title: 'Book',
          language: 'und',
          author: '  ',
          modified: '',
        ),
        <String>[
          'export',
          '--store',
          '/tmp/My Book.scribeforge',
          '--out',
          '/tmp/Book.docx',
          '--language',
          'und',
          '--title',
          'Book',
        ],
      );
    });

    test('keeps store and source paths as separate relink arguments', () {
      expect(
        ScribeForgeCli.relinkSourceArguments(
          storePath: '/tmp/My Book.scribeforge',
          sourcePath: '/archive/My Book.pdf',
        ),
        <String>[
          'relink-source',
          '--store',
          '/tmp/My Book.scribeforge',
          '--source',
          '/archive/My Book.pdf',
        ],
      );
    });

    test('builds process arguments with opt-in VLM configuration only when enabled', () {
      const defaultArguments = <String>[
        'process',
        '/tmp/My Book.pdf',
        '--store',
        '/tmp/My Book.scribeforge',
      ];
      expect(
        ScribeForgeCli.processArguments(
          pdfPath: '/tmp/My Book.pdf',
          storePath: '/tmp/My Book.scribeforge',
        ),
        defaultArguments,
      );
      expect(
        ScribeForgeCli.processArguments(
          pdfPath: '/tmp/My Book.pdf',
          storePath: '/tmp/My Book.scribeforge',
          cancelable: true,
        ),
        <String>[...defaultArguments, '--cancelable'],
      );
      expect(
        ScribeForgeCli.cancelControlMessage,
        '{"schema_version":1,"type":"cancel"}',
      );
      expect(
        ScribeForgeCli.processArguments(
          pdfPath: '/tmp/My Book.pdf',
          storePath: '/tmp/My Book.scribeforge',
          vlmSettings: const DesktopVlmSettings(
            enabled: false,
            endpoint: 'https://vision.example.test/v1',
            model: 'configured-but-disabled',
          ),
        ),
        defaultArguments,
      );
      expect(
        ScribeForgeCli.processArguments(
          pdfPath: '/tmp/My Book.pdf',
          storePath: '/tmp/My Book.scribeforge',
          vlmSettings: const DesktopVlmSettings(
            enabled: true,
            endpoint: 'http://127.0.0.1:1234/v1',
            model: 'local-vision-model',
            apiKeyEnvironmentVariable: 'SCRIBE_VLM_TOKEN',
            timeoutSeconds: 35,
          ),
        ),
        <String>[
          'process',
          '/tmp/My Book.pdf',
          '--store',
          '/tmp/My Book.scribeforge',
          '--vlm-endpoint',
          'http://127.0.0.1:1234/v1',
          '--vlm-model',
          'local-vision-model',
          '--vlm-api-key-env',
          'SCRIBE_VLM_TOKEN',
          '--vlm-timeout',
          '35.0',
        ],
      );
    });

    test('builds VLM preflight arguments without embedding secret values', () {
      expect(
        ScribeForgeCli.vlmCheckArguments(
          imagePath: '/tmp/Review crop.png',
          vlmSettings: const DesktopVlmSettings(
            endpoint: 'https://vision.example.test/v1',
            model: 'vision-model',
            apiKeyEnvironmentVariable: 'SCRIBE_VLM_TOKEN',
            timeoutSeconds: 30,
            allowRemote: true,
          ),
        ),
        <String>[
          'vlm-check',
          '--image',
          '/tmp/Review crop.png',
          '--vlm-endpoint',
          'https://vision.example.test/v1',
          '--vlm-model',
          'vision-model',
          '--vlm-api-key-env',
          'SCRIBE_VLM_TOKEN',
          '--vlm-timeout',
          '30.0',
          '--allow-remote-vlm',
          '--json',
        ],
      );
    });

    test('uses the source PDF stem for the project store', () {
      expect(
        ScribeForgeCli.defaultStorePath('books/My Book.pdf'),
        ScribeForgeCli.joinPath('books', 'My Book.scribeforge'),
      );
    });

    test('uses a separate sibling directory for candidate outputs', () {
      expect(
        ScribeForgeCli.candidateOutputDirectoryForPdf('/tmp/Book.scan.pdf'),
        ScribeForgeCli.joinPath('/tmp', 'Book.scan-comparison-candidates'),
      );
      expect(
        ScribeForgeCli.candidateEpubPath('/tmp/candidates', 'mineru'),
        ScribeForgeCli.joinPath('/tmp/candidates', 'candidate-mineru.epub'),
      );
    });

    test('keeps paths with spaces as individual process arguments', () {
      expect(
        ScribeForgeCli.processArguments(
          pdfPath: '/tmp/My Book.pdf',
          storePath: '/tmp/My Book.scribeforge',
        ),
        <String>[
          'process',
          '/tmp/My Book.pdf',
          '--store',
          '/tmp/My Book.scribeforge',
        ],
      );
      expect(
        ScribeForgeCli.exportCandidateArguments(
          storePath: '/tmp/My Book.scribeforge',
          outputDirectory: '/tmp/My Book-candidates',
        ),
        <String>[
          'export-candidates',
          '--store',
          '/tmp/My Book.scribeforge',
          '--out-dir',
          '/tmp/My Book-candidates',
        ],
      );
    });
  });
}
