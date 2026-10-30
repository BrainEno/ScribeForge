import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/main.dart';

void main() {
  testWidgets('unified workspace navigates through import, process, review, export and setup',
      (tester) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const ScribeForgeDesktopApp());
    await tester.pumpAndSettle();

    expect(find.text('已导入的书籍'), findsOneWidget);
    expect(find.text('导入书籍'), findsOneWidget);
    expect(find.text('打开项目文件夹'), findsOneWidget);

    await tester.tap(find.text('处理').last);
    await tester.pumpAndSettle();
    expect(find.text('导入并处理书籍'), findsOneWidget);
    expect(find.text('选择文件'), findsOneWidget);
    expect(find.text('开始处理'), findsOneWidget);

    await tester.tap(find.text('复核').last);
    await tester.pumpAndSettle();
    expect(find.text('复核队列'), findsOneWidget);
    expect(find.text('刷新队列'), findsOneWidget);
    expect(find.text('快捷键：Alt+1/2/3 填入候选；Ctrl+Enter 记录人工决策。'), findsOneWidget);

    await tester.tap(find.text('导出').last);
    await tester.pumpAndSettle();
    expect(find.text('导出成品'), findsOneWidget);
    expect(find.text('开始导出'), findsOneWidget);

    await tester.tap(find.text('设置').last);
    await tester.pumpAndSettle();
    expect(find.text('命令行工具'), findsOneWidget);
    expect(find.text('运行时配置 (setup)'), findsOneWidget);
    expect(find.text('刷新运行时状态'), findsOneWidget);

    expect(tester.takeException(), isNull);
  });
}
