import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/widgets/workspace_help_dialog.dart';

void main() {
  testWidgets('workspace help dialog lists the workflow and all shortcuts', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              key: const ValueKey('open-help'),
              onPressed: () => showWorkspaceHelpDialog(context),
              child: const Text('打开帮助'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('open-help')));
    await tester.pumpAndSettle();

    expect(find.text('使用帮助'), findsOneWidget);
    // Workflow steps.
    expect(find.textContaining('开始处理'), findsOneWidget);
    expect(find.textContaining('复核'), findsOneWidget);
    expect(find.textContaining('导出'), findsOneWidget);
    // Review shortcuts.
    expect(find.textContaining('Alt+←/→'), findsOneWidget);
    expect(find.textContaining('Ctrl+J'), findsOneWidget);
    expect(find.textContaining('Ctrl+G'), findsOneWidget);
    expect(find.textContaining('Alt+1/2/3'), findsOneWidget);
    expect(find.textContaining('Ctrl+Enter 记录人工决策'), findsOneWidget);
    // The safety rule: filling a candidate is not a decision.
    expect(find.textContaining('不会记录决策'), findsOneWidget);

    await tester.tap(find.text('知道了'));
    await tester.pumpAndSettle();
    expect(find.text('使用帮助'), findsNothing);
  });
}
