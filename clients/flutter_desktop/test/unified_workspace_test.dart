import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/unified_workspace.dart';
import 'package:scribeforge_desktop/services/backend_resolver.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

void main() {
  Future<void> showDesktopWorkspace(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: UnifiedWorkspace(
          cliExecutor: _FakeCliExecutor(),
          settingsStore: _MemorySettingsStore(),
          backendResolver: DesktopBackendResolver(
            fileExists: (_) async => false,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> openTab(WidgetTester tester, String label) async {
    final navigationRail = find.byType(NavigationRail);
    await tester.tap(
      find.descendant(of: navigationRail, matching: find.text(label)),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('shows the library and seven workflow destinations', (
    tester,
  ) async {
    await showDesktopWorkspace(tester);

    final navigationRail = find.byType(NavigationRail);
    for (final label in <String>[
      '书籍',
      '处理',
      '复核',
      '候选比较',
      '导出',
      '项目安全',
      '设置',
    ]) {
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

  testWidgets('shows file import and resumable process controls', (
    tester,
  ) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '处理');

    expect(find.text('导入并处理书籍'), findsOneWidget);
    expect(find.text('选择文件'), findsOneWidget);
    expect(find.text('开始处理'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows evidence review controls and keyboard shortcuts', (
    tester,
  ) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '复核');

    expect(find.text('复核队列'), findsOneWidget);
    expect(find.text('刷新队列'), findsOneWidget);
    final furnitureFilter = find.byKey(
      const ValueKey('review-include-page-furniture'),
    );
    expect(tester.widget<FilterChip>(furnitureFilter).selected, isFalse);
    await tester.tap(furnitureFilter);
    await tester.pumpAndSettle();
    expect(tester.widget<FilterChip>(furnitureFilter).selected, isTrue);
    expect(find.text('请先导入书籍或打开一个项目。'), findsOneWidget);
    expect(
      find.text(
        '快捷键：Alt+←/→ 上一项/下一项；Ctrl+J 聚焦决策输入；Ctrl+G 聚焦跳页；Alt+1/2/3 填入候选；Ctrl+Enter 明确记录。',
      ),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('opens candidate comparison from the unified workspace', (
    tester,
  ) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '候选比较');

    expect(find.text('OCR 候选比较'), findsOneWidget);
    expect(find.text('请先导入书籍或打开项目，再比较 OCR 候选。'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows EPUB/DOCX metadata and output folder controls', (
    tester,
  ) async {
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
    expect(find.text('保留页眉、页脚与页码'), findsOneWidget);
    expect(find.text('开始导出'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('provides runtime inspection and setup in settings', (
    tester,
  ) async {
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

  testWidgets('settings exposes one-click backend environment provisioning', (
    tester,
  ) async {
    await showDesktopWorkspace(tester);
    await openTab(tester, '设置');

    expect(find.text('后端环境（Python 虚拟环境）'), findsOneWidget);
    expect(find.text('选择 Python 解释器'), findsOneWidget);
    expect(find.text('选择源码目录'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('provision-backend-button')),
      findsOneWidget,
    );
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

class _MemorySettingsStore extends DesktopSettingsStore {
  DesktopVlmSettings _vlmSettings = const DesktopVlmSettings();
  String _backendPython = '';
  String _backendSourcePath = '';

  @override
  Future<String> loadCliExecutable() async =>
      DesktopSettingsStore.defaultCliExecutable;

  @override
  Future<String> loadBackendPython() async => _backendPython;

  @override
  Future<void> saveBackendPython(String pythonExecutable) async {
    _backendPython = pythonExecutable;
  }

  @override
  Future<String> loadBackendSourcePath() async => _backendSourcePath;

  @override
  Future<void> saveBackendSourcePath(String sourcePath) async {
    _backendSourcePath = sourcePath;
  }

  @override
  Future<DesktopVlmSettings> loadVlmSettings() async => _vlmSettings;

  @override
  Future<void> saveVlmSettings(DesktopVlmSettings settings) async {
    _vlmSettings = settings;
  }
}

class _FakeCliExecutor implements ScribeForgeCliExecutor {
  @override
  Future<void> cancel() async {}

  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String chunk) onOutput,
    required void Function(String chunk) onError,
  }) async {
    if (arguments.first == 'runtime-status') {
      onOutput(
        '{"schema_version":1,"runtime_root":"/fake/runtime",'
        '"runtime_root_exists":true,"uv_available":true,'
        '"hardware":{},"paths":{},"backends":{'
        '"mineru":{"state":"ready","failed_step":null,'
        '"environment_path":"/fake/mineru","environment_exists":true},'
        '"paddleocr":{"state":"ready","failed_step":null,'
        '"environment_path":"/fake/paddle","environment_exists":true}}}',
      );
      return 0;
    }
    onError('Unexpected UI-only fake command: ${arguments.first}');
    return 2;
  }
}
