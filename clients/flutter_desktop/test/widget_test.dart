import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/main.dart';

void main() {
  testWidgets('desktop workspace starts with source, candidate and OCR actions', (tester) async {
    // This is a desktop-first layout, so assert its baseline at a desktop viewport
    // rather than Flutter's default 800x600 test canvas.
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const ScribeForgeDesktopApp());
    await tester.pumpAndSettle();

    expect(find.text('ScribeForge'), findsOneWidget);
    expect(find.text('Open source PDF'), findsOneWidget);
    expect(find.text('Open project'), findsOneWidget);
    expect(find.text('Open Candidate A EPUB'), findsOneWidget);
    expect(find.text('Open Candidate B EPUB'), findsOneWidget);
    expect(find.text('Set up OCR'), findsOneWidget);
    expect(find.text('Run OCR'), findsOneWidget);
    expect(find.text('Build A/B EPUBs'), findsOneWidget);
    expect(find.text('Human evaluation'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
