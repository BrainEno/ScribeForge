import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/theme/scribe_theme.dart';
import 'package:scribeforge_desktop/widgets/workspace_components.dart';
import 'package:scribeforge_desktop/widgets/workspace_shell.dart';

void main() {
  testWidgets(
    'workbench lands on the same draft after resizing and navigation',
    (tester) async {
      tester.view.physicalSize = const Size(1360, 920);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      const draft = ValueKey('visible-draft');
      const mainPane = ValueKey('main-pane');
      const sidePane = ValueKey('side-pane');
      final scale = ValueNotifier(1.0);
      addTearDown(scale.dispose);
      var active = 0;
      await tester.pumpWidget(
        MaterialApp(
          theme: ScribeTheme.light,
          builder: (context, child) => ValueListenableBuilder<double>(
            valueListenable: scale,
            builder: (context, value, _) {
              final media = MediaQuery.of(context);
              return MediaQuery(
                data: media.copyWith(textScaler: TextScaler.linear(value)),
                child: child!,
              );
            },
          ),
          home: StatefulBuilder(
            builder: (context, setState) => WorkspaceShell(
              destinations: const [
                WorkspaceDestination('测试导出', Icons.publish),
                WorkspaceDestination('测试书籍', Icons.book),
              ],
              selectedIndex: active,
              onSelect: (value) => setState(() => active = value),
              pages: const [
                ScribePageBody(
                  children: [
                    ScribePageHeader(
                      icon: Icons.publish,
                      title: '导出成品',
                      subtitle: '填写书籍信息',
                    ),
                    SizedBox(height: 16),
                    ScribeAdaptivePair(
                      primary: ScribeSectionCard(
                        key: mainPane,
                        icon: Icons.book,
                        title: '书籍信息',
                        child: TextField(
                          key: draft,
                          decoration: InputDecoration(labelText: '书名'),
                        ),
                      ),
                      secondary: ScribeSectionCard(
                        key: sidePane,
                        icon: Icons.tune,
                        title: '版式选项',
                        child: Text('按章节输出'),
                      ),
                    ),
                  ],
                ),
                Text('书库页面'),
              ],
              notices: const [],
              actions: const [],
            ),
          ),
        ),
      );
      Offset position(Key key) => tester.getTopLeft(find.byKey(key));
      expect(
        tester.widget<NavigationRail>(find.byType(NavigationRail)).extended,
        isTrue,
      );
      expect(position(mainPane).dy, position(sidePane).dy);
      expect(position(sidePane).dx, greaterThan(position(mainPane).dx));
      await tester.enterText(find.byKey(draft), '尚未提交的书名');

      tester.view.physicalSize = const Size(404, 460);
      scale.value = 1.45;
      await tester.pumpAndSettle();
      expect(find.byType(NavigationRail), findsNothing);
      expect(position(sidePane).dy, greaterThan(position(mainPane).dy));
      expect(position(mainPane).dx, position(sidePane).dx);
      expect(find.text('尚未提交的书名'), findsOneWidget);
      final menu = find.byKey(const ValueKey('workspace-destination-menu'));
      await tester.tap(menu);
      await tester.pumpAndSettle();
      await tester.tap(find.text('测试书籍'));
      await tester.pumpAndSettle();
      expect(find.text('书库页面'), findsOneWidget);

      tester.view.physicalSize = const Size(840, 700);
      scale.value = 1.0;
      await tester.pumpAndSettle();
      final rail = find.byType(NavigationRail);
      expect(tester.widget<NavigationRail>(rail).extended, isFalse);
      await tester.tap(find.descendant(of: rail, matching: find.text('测试导出')));
      await tester.pumpAndSettle();
      expect(find.text('尚未提交的书名'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
