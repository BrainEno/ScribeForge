import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/epub_candidate.dart';

void main() {
  group('EpubCandidate archive safety', () {
    late Directory temp;

    setUp(() async {
      temp = await Directory.systemTemp.createTemp('scribeforge-epub-test-');
    });

    tearDown(() async {
      if (await temp.exists()) await temp.delete(recursive: true);
    });

    Future<String> writeEpub(List<({String name, String content})> files) async {
      final archive = Archive();
      for (final file in files) {
        archive.addFile(ArchiveFile.string(file.name, file.content));
      }
      final bytes = ZipEncoder().encode(archive);
      final path = '${temp.path}/test.epub';
      await File(path).writeAsBytes(bytes);
      return path;
    }

    List<({String name, String content})> validEntries() => [
          (
            name: 'META-INF/container.xml',
            content: '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
          ),
          (
            name: 'OPS/book.opf',
            content: '<package><metadata><title>Test</title></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
          ),
          (
            name: 'OPS/chapter.xhtml',
            content: '<html><head><title>Chapter</title></head><body><p>Hello</p></body></html>',
          ),
        ];

    test('loads a valid EPUB and extracts spine text', () async {
      final candidate = await EpubCandidate.load(await writeEpub(validEntries()));

      expect(candidate.title, 'Test');
      expect(candidate.pages, hasLength(1));
      expect(candidate.pages.single.text, contains('Hello'));
    });

    test('rejects duplicate paths after slash normalization', () async {
      final entries = validEntries()
        ..add((
          name: 'OPX/chapter.xhtml',
          content: '<html><body><p>Ambiguous duplicate</p></body></html>',
        ));
      final path = await writeEpub(entries);
      final bytes = await File(path).readAsBytes();
      final name = 'OPX/chapter.xhtml'.codeUnits;
      final occurrences = <int>[];
      for (var i = 0; i <= bytes.length - name.length; i++) {
        var matches = true;
        for (var j = 0; j < name.length; j++) {
          if (bytes[i + j] != name[j]) {
            matches = false;
            break;
          }
        }
        if (matches) occurrences.add(i);
      }
      expect(occurrences, hasLength(2));
      // Keep both ZIP entries distinct during encoding, then make the second
      // entry collide after Windows separator normalization.
      for (final index in occurrences) {
        bytes[index + 2] = 83; // S: OPX -> OPS
        bytes[index + 3] = 92; // backslash
      }
      await File(path).writeAsBytes(bytes);

      await expectLater(
        EpubCandidate.load(path),
        throwsA(isA<FormatException>().having(
          (error) => error.message,
          'message',
          contains('duplicate archive paths'),
        )),
      );
    });
  });
}
