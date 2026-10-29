import 'dart:ui' show Size;

import 'package:flutter/material.dart' show CheckboxListTile, ElevatedButton;
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/main.dart';

/// BDD 风格的一体化工作台测试。
///
/// 测试按 Given-When-Then 组织，验证新手使用者在无需接触命令行的
/// 前提下，能否完成“导入书籍 → 运行处理 → 复核决策 → 导出”全流程。
///
/// 注意：ScribeForgeCli.run 会启动真实的 Python CLI 进程，测试环境
/// 中未必安装该 CLI，因此测试聚焦于 UI 结构渲染与不依赖 CLI 调用的
/// 交互（标签页切换、表单字段、下拉选择、复选框），不点击“开始处理”
///、“开始导出”等会启动子进程的按钮。
void main() {
  group('UnifiedWorkspace', () {
    testWidgets(
      'Given a fresh workspace, When the app starts, '
      'Then it shows the five navigation tabs and the library page',
      (tester) async {
        // Given: a desktop viewport
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        // When: the app starts
        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // Then: the five navigation tabs are present
        expect(find.text('书籍'), findsOneWidget);
        expect(find.text('处理'), findsOneWidget);
        expect(find.text('复核'), findsOneWidget);
        expect(find.text('导出'), findsOneWidget);
        expect(find.text('设置'), findsOneWidget);

        // And: the library page shows no project yet
        expect(find.text('暂无项目。'), findsOneWidget);

        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the library page, When the user switches to the process tab, '
      'Then the import UI is shown',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the "处理" navigation destination
        await tester.tap(find.text('处理'));
        await tester.pumpAndSettle();

        // Then: the process page shows the file picker button and start button
        expect(find.text('选择文件'), findsOneWidget);
        expect(find.text('开始处理'), findsOneWidget);
        expect(find.text('源文件'), findsOneWidget);

        final startButtonFinder = find.ancestor(
          of: find.text('开始处理'),
          matching: find.byType(ElevatedButton),
        );
        expect(startButtonFinder, findsOneWidget);
        expect(tester.getSize(startButtonFinder).width.isFinite, isTrue);
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the process page, When the user switches to the review tab, '
      'Then the review queue UI is shown',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the "复核" navigation destination
        await tester.tap(find.text('复核'));
        await tester.pumpAndSettle();

        // Then: the review page shows the refresh button
        expect(find.text('刷新队列'), findsOneWidget);
        expect(find.text('暂无待处理配对。'), findsOneWidget);

        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the review page, When the user switches to the export tab, '
      'Then the export form is shown with format, language, title fields',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the "导出" navigation destination
        await tester.tap(find.text('导出'));
        await tester.pumpAndSettle();

        // Then: the export form fields are present
        expect(find.text('导出格式'), findsOneWidget);
        expect(find.text('EPUB 3'), findsOneWidget);
        expect(find.text('DOCX'), findsOneWidget);
        expect(find.text('输出文件'), findsOneWidget);
        expect(find.text('语言（BCP 47，如 zh）'), findsOneWidget);
        expect(find.text('书名（可选）'), findsOneWidget);
        expect(find.text('作者（可选）'), findsOneWidget);
        expect(find.text('按章节布局（EPUB）'), findsOneWidget);
        expect(find.text('严格接受校验'), findsOneWidget);
        expect(find.text('开始导出'), findsOneWidget);

        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the export page, When the user switches format to DOCX, '
      'Then the DOCX option is selected',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the export tab, then the format dropdown
        await tester.tap(find.text('导出'));
        await tester.pumpAndSettle();

        await tester.tap(find.text('EPUB 3'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('DOCX'));
        await tester.pumpAndSettle();

        // Then: DOCX is the selected export format
        expect(find.text('DOCX'), findsOneWidget);
        expect(find.text('EPUB 3'), findsNothing);
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the export page, When the user toggles strict acceptance, '
      'Then the checkbox state changes',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the export tab, then the strict acceptance checkbox
        await tester.tap(find.text('导出'));
        await tester.pumpAndSettle();

        final checkboxFinder = find.ancestor(
          of: find.text('严格接受校验'),
          matching: find.byType(CheckboxListTile),
        );
        expect(checkboxFinder, findsOneWidget);
        expect(tester.widget<CheckboxListTile>(checkboxFinder).value, isFalse);

        await tester.tap(find.text('严格接受校验'));
        await tester.pumpAndSettle();

        // Then: the strict acceptance setting changes in the actual widget state
        expect(tester.widget<CheckboxListTile>(checkboxFinder).value, isTrue);
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets(
      'Given the settings page, When the user switches to it, '
      'Then the CLI executable and runtime setup UI is shown',
      (tester) async {
        tester.view.physicalSize = const Size(1440, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        await tester.pumpWidget(const ScribeForgeDesktopApp());
        await tester.pumpAndSettle();

        // When: the user taps the "设置" navigation destination
        await tester.tap(find.text('设置'));
        await tester.pumpAndSettle();

        // Then: the settings page shows CLI and runtime configuration
        expect(find.text('命令行工具'), findsOneWidget);
        expect(find.text('运行时配置 (setup)'), findsOneWidget);
        expect(find.text('检测硬件、安装 OCR 运行环境与模型'), findsOneWidget);
        expect(find.text('刷新运行时状态'), findsOneWidget);

        expect(tester.takeException(), isNull);
      },
    );
  });
}
