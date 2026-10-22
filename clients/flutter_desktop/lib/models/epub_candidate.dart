import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:crypto/crypto.dart';
import 'package:xml/xml.dart';

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull {
    final iterator = this.iterator;
    return iterator.moveNext() ? iterator.current : null;
  }
}

class EpubPage {
  const EpubPage({
    required this.spineIndex,
    required this.title,
    required this.text,
    this.sourcePageIndex,
  });

  final int spineIndex;
  final int? sourcePageIndex;
  final String title;
  final String text;

  List<String> get lines => text
      .split('\n')
      .map((line) => line.trim())
      .where((line) => line.isNotEmpty)
      .toList(growable: false);
}

class EpubCandidate {
  const EpubCandidate({
    required this.path,
    required this.title,
    required this.creator,
    required this.pages,
    required this.isSourcePageAligned,
    required this.sha256,
  });

  final String path;
  final String title;
  final String? creator;
  final List<EpubPage> pages;
  final bool isSourcePageAligned;
  final String sha256;

  EpubPage? pageForSourceIndex(int zeroBasedIndex) {
    if (isSourcePageAligned) {
      for (final page in pages) {
        if (page.sourcePageIndex == zeroBasedIndex) {
          return page;
        }
      }
      return null;
    }
    return zeroBasedIndex >= 0 && zeroBasedIndex < pages.length
        ? pages[zeroBasedIndex]
        : null;
  }

  static Future<EpubCandidate> load(String path) async {
    final file = File(path);
    if (!await file.exists()) {
      throw FormatException('EPUB file not found: $path');
    }
    final length = await file.length();
    if (length <= 0 || length > 256 * 1024 * 1024) {
      throw const FormatException('EPUB file is empty or exceeds the 256 MiB safety limit.');
    }
    final bytes = await file.readAsBytes();
    final archive = ZipDecoder().decodeBytes(bytes);
    if (archive.length > 20000) {
      throw const FormatException('EPUB archive contains too many entries.');
    }
    final entries = <String, ArchiveFile>{
      for (final entry in archive.files)
        if (entry.isFile) entry.name.replaceAll('\\', '/') : entry,
    };

    String readEntry(String name) {
      final entry = entries[name];
      if (entry == null) {
        throw FormatException('EPUB is missing $name');
      }
      if (entry.size > 32 * 1024 * 1024) {
        throw FormatException('EPUB entry is too large: $name');
      }
      final content = entry.readBytes();
      if (content == null) {
        throw FormatException('Could not read EPUB entry: $name');
      }
      return utf8.decode(content, allowMalformed: true);
    }

    final container = XmlDocument.parse(readEntry('META-INF/container.xml'));
    final rootfile = _descendants(container.rootElement)
        .where((element) => element.name.local == 'rootfile')
        .firstOrNull;
    final opfPath = rootfile?.getAttribute('full-path');
    if (opfPath == null || opfPath.isEmpty) {
      throw const FormatException('EPUB container does not point to an OPF package.');
    }
    final opf = XmlDocument.parse(readEntry(opfPath));
    final metadata = _descendants(opf.rootElement).where((e) => e.name.local == 'metadata');
    final metadataElement = metadata.firstOrNull ?? opf.rootElement;
    final title = _descendants(metadataElement)
            .where((e) => e.name.local == 'title')
            .map((e) => e.innerText.trim())
            .firstWhere((value) => value.isNotEmpty, orElse: () => '') ;
    final creator = _descendants(metadataElement)
        .where((e) => e.name.local == 'creator')
        .map((e) => e.innerText.trim())
        .firstWhere((value) => value.isNotEmpty, orElse: () => '');
    final manifest = <String, String>{};
    for (final item in _descendants(opf.rootElement).where((e) => e.name.local == 'item')) {
      final id = item.getAttribute('id');
      final href = item.getAttribute('href');
      final type = item.getAttribute('media-type') ?? '';
      if (id != null &&
          href != null &&
          (type == 'application/xhtml+xml' ||
              href.toLowerCase().endsWith('.xhtml') ||
              href.toLowerCase().endsWith('.html'))) {
        manifest[id] = _resolveZipPath(opfPath, href);
      }
    }
    final itemIds = _descendants(opf.rootElement)
        .where((e) => e.name.local == 'spine')
        .expand((spine) => _descendants(spine).where((e) => e.name.local == 'itemref'))
        .map((item) => item.getAttribute('idref'))
        .whereType<String>()
        .toList(growable: false);
    if (itemIds.isEmpty) {
      throw const FormatException('EPUB spine does not contain any readable items.');
    }

    final pages = <EpubPage>[];
    for (final id in itemIds) {
      final href = manifest[id];
      if (href == null) {
        continue;
      }
      final document = XmlDocument.parse(readEntry(href));
      final root = document.rootElement;
      final chapterTitle = _descendants(root)
          .where((e) => e.name.local == 'title')
          .map((e) => e.innerText.trim())
          .firstWhere((value) => value.isNotEmpty, orElse: () => _basename(href));
      final content = _extractText(root)
          .split('\n')
          .map((line) => line.replaceAll(RegExp(r'[ \t]+'), ' ').trim())
          .where((line) => line.isNotEmpty)
          .join('\n');
      final filename = _basename(href);
      final match = RegExp(r'^page-(\d+)\.(?:xhtml|html)$', caseSensitive: false)
          .firstMatch(filename);
      final pageNumber = match == null ? null : int.tryParse(match.group(1)!);
      pages.add(
        EpubPage(
          spineIndex: pages.length,
          sourcePageIndex: pageNumber == null ? null : pageNumber - 1,
          title: chapterTitle,
          text: content,
        ),
      );
    }
    if (pages.isEmpty) {
      throw const FormatException('EPUB spine items do not reference readable XHTML/HTML files.');
    }
    final sourcePageAligned = pages.every((page) => page.sourcePageIndex != null);
    return EpubCandidate(
      path: file.absolute.path,
      title: title.isEmpty ? _basename(path) : title,
      creator: creator.isEmpty ? null : creator,
      pages: List.unmodifiable(pages),
      isSourcePageAligned: sourcePageAligned,
      sha256: _sha256Hex(bytes),
    );
  }
}

Iterable<XmlElement> _descendants(XmlElement root) sync* {
  for (final node in root.descendants) {
    if (node is XmlElement) {
      yield node;
    }
  }
}

String _extractText(XmlNode node) {
  if (node is XmlText) {
    return node.value;
  }
  if (node is! XmlElement) {
    return '';
  }
  final tag = node.name.local.toLowerCase();
  if (const {'script', 'style', 'head', 'svg', 'noscript'}.contains(tag)) {
    return '';
  }
  final children = node.children.map(_extractText).join();
  if (const {
    'p', 'div', 'section', 'article', 'li', 'blockquote', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'br', 'tr',
  }.contains(tag)) {
    return '\n$children\n';
  }
  return children;
}

String _resolveZipPath(String opfPath, String href) {
  final decoded = Uri.decodeComponent(href.split('#').first.split('?').first);
  final pieces = <String>[];
  final parent = opfPath.split('/')..removeLast();
  for (final part in [...parent, ...decoded.split('/')]) {
    if (part.isEmpty || part == '.') {
      continue;
    }
    if (part == '..') {
      if (pieces.isEmpty) {
        throw const FormatException('EPUB resource escapes the archive root.');
      }
      pieces.removeLast();
    } else {
      pieces.add(part);
    }
  }
  return pieces.join('/');
}

String _basename(String path) => path.split('/').last;

String _sha256Hex(List<int> bytes) => sha256.convert(bytes).toString();
