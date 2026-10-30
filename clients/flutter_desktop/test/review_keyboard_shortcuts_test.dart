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
    var focusDecisionCount = 0;
    var focusPageJumpCount = 0;

    await tester.pumpWidget(
      MaterialApp(
        home: ReviewKeyboardShortcuts(
          onPreviousPage: () => previousCount++,
          onNextPage: () => nextCount++,
          onNextDifference: () => differenceCount++,
          onFocusDecisionInput: () => focusDecisionCount++,
          onFocusPageJump: () => focusPageJumpCount++,
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
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyJ);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyJ);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyG);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyG);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();

    expect(previousCount, 1);
    expect(nextCount, 1);
    expect(differenceCount, 1);
    expect(focusDecisionCount, 1);
    expect(focusPageJumpCount, 1);
    expect(tester.takeException(), isNull);
  });
}
