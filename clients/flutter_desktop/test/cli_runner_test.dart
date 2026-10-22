import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/cli_runner.dart';

void main() {
  group('ScribeForgeCli path and argument helpers', () {
    test('requests structured JSON for project status', () {
      expect(
        ScribeForgeCli.statusArguments(storePath: '/tmp/My Book.scribeforge'),
        <String>['status', '--store', '/tmp/My Book.scribeforge', '--json'],
      );
    });

    test('requests a JSON project audit for the selected store', () {
      expect(
        ScribeForgeCli.auditArguments(storePath: '/tmp/My Book.scribeforge'),
        <String>['audit', '--store', '/tmp/My Book.scribeforge', '--json'],
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
