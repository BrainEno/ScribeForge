import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/widgets/review_keyboard_shortcuts.dart';

void main() {
  testWidgets('Alt+Left, Alt+Right and Alt+D invoke review navigation',
      (tester) async {
    var previousCount = 0;
    var nextCount = 0;
    var differenceCount = 0;

    await tester.pumpWidget(
      MaterialApp(
        home: ReviewKeyboardShortcuts(
          onPreviousPage: () => previousCount++,
          onNextPage: () => nextCount++,
          onNextDifference: () => differenceCount++,
          child: const Scaffold(
            body: Focus(
              autofocus: true,
              child: Text('Focus target'),
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    Future<void> pressAltKey(LogicalKeyboardKey key) async {
      await tester.sendKeyDownEvent(LogicalKeyboardKey.altLeft);
      await tester.sendKeyDownEvent(key);
      await tester.sendKeyUpEvent(key);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.altLeft);
      await tester.pump();
    }

    await pressAltKey(LogicalKeyboardKey.arrowLeft);
    await pressAltKey(LogicalKeyboardKey.arrowRight);
    await pressAltKey(LogicalKeyboardKey.keyD);

    expect(previousCount, 1);
    expect(nextCount, 1);
    expect(differenceCount, 1);
    expect(tester.takeException(), isNull);
  });
}
