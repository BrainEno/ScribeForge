import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/main.dart';

void main() {
  Future<void> showDesktopWorkspace(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const ScribeForgeDesktopApp());
    await tester.pumpAndSettle();
  }

  Future<void> openTab(WidgetTester tester, String label) async {
    final navigationRail = find.byType(NavigationRail);
    await tester.tap(
      find.descendant(of: navigationRail, matching: find.text(label)),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('shows the library and six workflow destinations', (tester) async {
    await showDesktopWorkspace(tester);

    final navigationRail = find.byType(NavigationRail);
    for (final label in <String>['书籍', '处理', '复核', '候选比较', '导出', '设置']) {
      expect(
        find.descendant(of: navigationRail, matching: find.text(label)),
        findsOneWidget,
      );
    }
    expect(find.text('已导入的书籍'), findsOneWidget);
    expect(find.text('导入书籍'), findsOneWidget);
    expect(find.text('打开项目文件夹'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows file import and resumable process controls', (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '处理');

    expect(find.text('导入并处理书籍'), findsOneWidget);
    expect(find.text('选择文件'), findsOneWidget);
    expect(find.text('开始处理'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows evidence review controls and keyboard shortcuts', (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '复核');

    expect(find.text('复核队列'), findsOneWidget);
    expect(find.text('刷新队列'), findsOneWidget);
    expect(find.text('请先导入书籍或打开一个项目。'), findsOneWidget);
    expect(
      find.text(
        '快捷键：Alt+←/→ 上一项/下一项；Ctrl+J 聚焦决策输入；Ctrl+G 聚焦跳页；Alt+1/2/3 填入候选；Ctrl+Enter 明确记录。',
      ),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('opens candidate comparison from the unified workspace',
      (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '候选比较');

    expect(find.text('OCR 候选比较'), findsOneWidget);
    expect(
      find.text('请先导入书籍或打开项目，再比较 OCR 候选。'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows EPUB/DOCX metadata and output folder controls', (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '导出');

    expect(find.text('导出格式'), findsOneWidget);
    expect(find.text('EPUB 3'), findsOneWidget);
    expect(find.text('输出文件'), findsOneWidget);
    expect(find.text('选择导出文件夹，文件名按书名和格式生成'), findsOneWidget);
    expect(find.text('语言（BCP 47，如 zh）'), findsOneWidget);
    expect(find.text('书名（可选）'), findsOneWidget);
    expect(find.text('作者（可选）'), findsOneWidget);
    expect(find.text('按章节布局（EPUB）'), findsOneWidget);
    expect(find.text('严格接受校验'), findsOneWidget);
    expect(find.text('开始导出'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('provides runtime inspection and setup in settings', (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '设置');

    expect(find.text('命令行工具'), findsOneWidget);
    expect(find.text('视觉模型复核'), findsOneWidget);
    expect(find.textContaining('默认关闭；未明确启用时不会发送复核裁切图。'), findsOneWidget);
    expect(find.text('配置 VLM 复核'), findsOneWidget);
    expect(find.text('运行时配置 (setup)'), findsOneWidget);
    expect(find.text('查看后端、阶段与步骤进度，或修复单个后端'), findsOneWidget);
    expect(find.text('刷新运行时状态'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('opens the Flutter-native runtime setup guide', (tester) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '设置');
    await tester.tap(find.text('运行时配置 (setup)'));
    await tester.pumpAndSettle();

    expect(find.text('首次 OCR 运行时配置'), findsOneWidget);
    expect(find.text('开始安装全部后端'), findsOneWidget);
    expect(find.text('只修复 MinerU'), findsOneWidget);
    expect(find.text('只修复 PaddleOCR'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('稍后配置'));
    await tester.pumpAndSettle();
    expect(find.text('首次 OCR 运行时配置'), findsNothing);
  });
}
