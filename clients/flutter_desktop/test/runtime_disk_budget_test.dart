import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_location.dart';
import 'package:scribeforge_desktop/models/runtime_status.dart';
import 'package:scribeforge_desktop/screens/runtime_setup_dialog.dart';
import 'package:scribeforge_desktop/screens/unified_workspace.dart';
import 'package:scribeforge_desktop/services/backend_resolver.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/settings_store.dart';

const String _statusWithDisk = '''
{
  "schema_version": 1,
  "runtime_root": "/fake/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {"os": "linux"},
  "paths": {},
  "backends": {
    "mineru": {"state": "missing", "failed_step": null,
      "environment_path": "/fake/mineru", "environment_exists": false,
      "optional": false,
      "estimates": {"download_bytes": 6000000000, "install_bytes": 8000000000,
        "model_bytes": 4000000000, "total_bytes": 18000000000, "note": "含模型"}},
    "paddleocr": {"state": "missing", "failed_step": null,
      "environment_path": "/fake/paddle", "environment_exists": false,
      "optional": false,
      "estimates": {"download_bytes": 600000000, "install_bytes": 1000000000,
        "model_bytes": 100000000, "total_bytes": 1700000000, "note": ""}},
    "paddleocr-vl": {"state": "missing", "failed_step": null,
      "environment_path": "/fake/vl", "environment_exists": false, "optional": true}
  },
  "disk": {
    "path": "/fake/runtime",
    "runtime_root": "/fake/runtime",
    "free_bytes": 5000000000,
    "total_bytes": 500000000000,
    "used_bytes": 495000000000,
    "required_bytes": 21000000000,
    "headroom_factor": 1.1,
    "sufficient": false,
    "missing_bytes": 16000000000,
    "advice": "预计需要约 19.6 GB，当前盘剩余 4.7 GB，还差约 14.9 GB。请清理磁盘。",
    "estimated": true,
    "tools_download_bytes": 31457280,
    "backends": {
      "mineru": {"download_bytes": 6000000000, "install_bytes": 8000000000,
        "model_bytes": 4000000000, "total_bytes": 18000000000, "note": "含模型"}
    }
  }
}
''';

const String _statusDiskOk = '''
{
  "schema_version": 1,
  "runtime_root": "/fake/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {"os": "linux"},
  "paths": {},
  "backends": {
    "mineru": {"state": "ready", "failed_step": null,
      "environment_path": "/fake/mineru", "environment_exists": true,
      "optional": false},
    "paddleocr": {"state": "ready", "failed_step": null,
      "environment_path": "/fake/paddle", "environment_exists": true,
      "optional": false}
  },
  "disk": {
    "path": "/fake/runtime",
    "runtime_root": "/fake/runtime",
    "free_bytes": 900000000000,
    "total_bytes": 1000000000000,
    "used_bytes": 100000000000,
    "required_bytes": 21000000000,
    "headroom_factor": 1.1,
    "sufficient": true,
    "missing_bytes": 0,
    "advice": "预计需要约 19.6 GB，当前盘剩余 838.9 GB，可以开始安装。",
    "estimated": true,
    "tools_download_bytes": 31457280,
    "backends": {}
  }
}
''';

class _FakeExecutor implements ScribeForgeCliExecutor {
  _FakeExecutor(this.statusJson);

  final String statusJson;

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
      onOutput(statusJson);
      return 0;
    }
    if (arguments.first == 'runtime-clean') {
      onOutput('{"freed_bytes": 2147483648, "removed": [], "kept": []}');
      return 0;
    }
    onError('Unexpected fake command: ${arguments.first}');
    return 2;
  }
}

class _MemorySettingsStore extends DesktopSettingsStore {
  @override
  Future<RuntimeLocationPreferences> loadRuntimeLocations() async =>
      const RuntimeLocationPreferences();

  DesktopVlmSettings _vlmSettings = const DesktopVlmSettings();
  String _cliExecutable = '';
  String _backendPython = '';
  String _backendSourcePath = '';
  String? _lastProjectStore;

  @override
  Future<String> loadCliExecutable() async => _cliExecutable.isEmpty
      ? DesktopSettingsStore.defaultCliExecutable
      : _cliExecutable;

  @override
  Future<SettingsSaveResult> saveCliExecutable(String executable) async {
    _cliExecutable = executable;
    return SettingsSaveResult.ok();
  }

  @override
  Future<String> loadBackendPython() async => _backendPython;

  @override
  Future<SettingsSaveResult> saveBackendPython(String python) async {
    _backendPython = python;
    return SettingsSaveResult.ok();
  }

  @override
  Future<String> loadBackendSourcePath() async => _backendSourcePath;

  @override
  Future<SettingsSaveResult> saveBackendSourcePath(String sourcePath) async {
    _backendSourcePath = sourcePath;
    return SettingsSaveResult.ok();
  }

  @override
  Future<String?> loadLastProjectStore() async => _lastProjectStore;

  @override
  Future<SettingsSaveResult> saveLastProjectStore(String storePath) async {
    _lastProjectStore = storePath.trim().isEmpty ? null : storePath.trim();
    return SettingsSaveResult.ok();
  }

  @override
  Future<DesktopVlmSettings> loadVlmSettings() async => _vlmSettings;

  @override
  Future<SettingsSaveResult> saveVlmSettings(
    DesktopVlmSettings settings,
  ) async {
    _vlmSettings = settings;
    return SettingsSaveResult.ok();
  }
}

Future<void> _showSetupDialog(
  WidgetTester tester,
  RuntimeStatusSnapshot status, {
  required bool Function() wasStarted,
  bool autoStart = false,
}) async {
  tester.view.physicalSize = const Size(1280, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => showDialog<bool>(
              context: context,
              barrierDismissible: false,
              builder: (_) => RuntimeSetupDialog(
                runtimeStatus: status,
                autoStart: autoStart,
                runSetup:
                    ({
                      required backend,
                      required onMessage,
                      required onDiagnostic,
                    }) async {
                      wasStarted();
                      return 0;
                    },
              ),
            ),
            child: const Text('打开配置'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('打开配置'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('setup dialog blocks the first run when disk is insufficient', (
    tester,
  ) async {
    var started = false;
    final status = RuntimeStatusSnapshot.fromJsonText(_statusWithDisk);
    await _showSetupDialog(
      tester,
      status,
      wasStarted: () => started = true,
      autoStart: true,
    );

    expect(
      find.byKey(const ValueKey('disk-insufficient-card')),
      findsOneWidget,
    );
    expect(find.textContaining('磁盘预算'), findsWidgets);
    expect(find.textContaining('MinerU：下载 5.6 GB'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('preflight-insufficient-advice')),
      findsOneWidget,
    );
    expect(find.text('磁盘空间不足，无法开始'), findsOneWidget);

    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('start-setup-button')),
    );
    expect(button.onPressed, isNull);
    expect(started, isFalse);
    expect(tester.takeException(), isNull);
  });

  testWidgets('setup dialog allows the first run when disk is sufficient', (
    tester,
  ) async {
    final status = RuntimeStatusSnapshot.fromJsonText(_statusDiskOk);
    await _showSetupDialog(tester, status, wasStarted: () => false);

    expect(find.byKey(const ValueKey('disk-budget-card')), findsOneWidget);
    expect(find.text('开始安装全部后端'), findsOneWidget);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('start-setup-button')),
    );
    expect(button.onPressed, isNotNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('settings card shows the budget and disables 初始化环境', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: UnifiedWorkspace(
          cliExecutor: _FakeExecutor(_statusWithDisk),
          settingsStore: _MemorySettingsStore(),
          backendResolver: DesktopBackendResolver(
            fileExists: (_) async => false,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('尚未就绪：需要配置 OCR 环境'), findsOneWidget);
    expect(find.textContaining('处理需要 MinerU 和 PaddleOCR'), findsOneWidget);

    // Open the settings tab.
    final rail = find.byType(NavigationRail);
    await tester.tap(find.descendant(of: rail, matching: find.text('设置')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('disk-budget-line')), findsOneWidget);
    expect(find.text('MinerU：未安装（基本处理必需）'), findsOneWidget);
    expect(find.text('PaddleOCR-VL：未安装（可选，不影响基本处理）'), findsOneWidget);
    expect(find.textContaining('完整安装预估：网络下载 5.6 GB'), findsOneWidget);
    expect(find.textContaining('当前无法判断可升级版本或增量大小'), findsOneWidget);
    expect(find.textContaining('还差约'), findsWidgets);
    expect(
      find.byKey(const ValueKey('disk-insufficient-advice')),
      findsOneWidget,
    );
    final initButton = tester.widget<FilledButton>(
      find.byKey(const ValueKey('initialize-runtime-button')),
    );
    expect(initButton.onPressed, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('settings can clean runtime temp files and show freed space', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: UnifiedWorkspace(
          cliExecutor: _FakeExecutor(_statusDiskOk),
          settingsStore: _MemorySettingsStore(),
          backendResolver: DesktopBackendResolver(
            fileExists: (_) async => false,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final rail = find.byType(NavigationRail);
    await tester.tap(find.descendant(of: rail, matching: find.text('设置')));
    await tester.pumpAndSettle();

    final cleanTile = find.text('清理运行时临时文件');
    await tester.ensureVisible(cleanTile);
    await tester.pumpAndSettle();
    await tester.tap(cleanTile);
    await tester.pumpAndSettle();
    expect(find.textContaining('释放约 2.0 GB'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('process page shows the current engine combination (#215)', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: UnifiedWorkspace(
          cliExecutor: _FakeExecutor(_statusDiskOk),
          settingsStore: _MemorySettingsStore(),
          backendResolver: DesktopBackendResolver(
            fileExists: (_) async => false,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final rail = find.byType(NavigationRail);
    await tester.tap(find.descendant(of: rail, matching: find.text('处理')));
    await tester.pumpAndSettle();

    expect(find.text('当前引擎组合：MinerU + PaddleOCR'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
