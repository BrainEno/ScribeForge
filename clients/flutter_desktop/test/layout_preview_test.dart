import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/evidence/layout_preview_dialog.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/layout_preview_service.dart';

final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGPgMkr5DwACigGgBE3sHAAAAABJRU5ErkJggg==',
);

class _Core implements ScribeForgeCliExecutor {
  _Core({this.change, this.exit = 0, this.raw, this.write = true});
  final void Function(Map<String, dynamic>)? change;
  final int exit;
  final String? raw;
  final bool write;
  final calls = <List<String>>[];
  @override
  Future<void> cancel() async {}
  @override
  Future<int> run({
    required String executable,
    required List<String> arguments,
    required void Function(String) onOutput,
    required void Function(String) onError,
  }) async {
    calls.add(arguments);
    if (exit != 0) {
      onError('unsafe frame');
      return exit;
    }
    final path = arguments[arguments.indexOf('--out') + 1];
    if (write) await File(path).writeAsBytes(_png);
    final result = <String, dynamic>{
      'schema_version': 1,
      'status': 'unverified_preview',
      'project_id': 7,
      'page_index': 17,
      'run_id': 5,
      'block_index': 2,
      'block_type': 'figure',
      'frame_kind': 'original',
      'output': path,
      'verification_id': 8,
      'engine': 'PaddleOCR',
      'source_sha256': 'a' * 64,
      'preview_sha256': sha256.convert(_png).toString(),
      'frame_size': [100, 200],
      'pixel_box': [10, 20, 11, 21],
    };
    change?.call(result);
    onOutput(raw ?? jsonEncode(result));
    return 0;
  }
}

class _WidgetPreviewService extends LayoutPreviewService {
  _WidgetPreviewService({this.fail = false})
    : super(
        executor: _Core(),
        executable: 'core',
        storePath: '/project',
        projectId: 7,
      );
  final bool fail;
  int calls = 0;
  @override
  Future<LayoutPreview> create({
    required int page,
    required int run,
    required int block,
    required String blockType,
  }) async {
    calls++;
    if (fail) throw const LayoutPreviewFailure('unsafe frame');
    return LayoutPreview(
      bytes: _png,
      verificationId: 8,
      engine: 'PaddleOCR',
      sourceSha256: 'a' * 64,
      previewSha256: sha256.convert(_png).toString(),
      pixelBox: const [10, 20, 11, 21],
      frameSize: const [100, 200],
    );
  }
}

void main() {
  late Directory root;
  setUp(() async {
    root = await Directory.systemTemp.createTemp('layout-test-');
  });
  tearDown(() async {
    await root.delete(recursive: true);
  });
  LayoutPreviewService service(_Core core) => LayoutPreviewService(
    executor: core,
    executable: 'core',
    storePath: '/project',
    projectId: 7,
    tempRoot: root,
  );
  Future<LayoutPreview> create(LayoutPreviewService service) =>
      service.create(page: 17, run: 5, block: 2, blockType: 'figure');
  Future<void> empty() async => expect(await root.list().toList(), isEmpty);

  test(
    'valid preview is digest checked and temporary files are removed',
    () async {
      final core = _Core();
      final preview = await create(service(core));
      expect(preview.bytes, _png);
      expect(preview.verificationId, 8);
      expect(preview.pixelBox, [10, 20, 11, 21]);
      expect(
        core.calls.single,
        containsAllInOrder([
          'layout-preview',
          '--store',
          '/project',
          '--project',
          '7',
          '--page',
          '17',
          '--run',
          '5',
          '--block',
          '2',
          '--out',
        ]),
      );
      await empty();
    },
  );

  test(
    'rejects wrong identity, frame, digest and unsafe coordinates with cleanup',
    () async {
      final mutations = <void Function(Map<String, dynamic>)>[
        (v) => v['project_id'] = 8,
        (v) => v['page_index'] = 18,
        (v) => v['run_id'] = 6,
        (v) => v['block_index'] = 3,
        (v) => v['block_type'] = 'table',
        (v) => v['frame_kind'] = 'preprocessed',
        (v) => v['output'] = '/other',
        (v) => v['verification_id'] = 0,
        (v) => v['source_sha256'] = 'invalid',
        (v) => v['preview_sha256'] = 'b' * 64,
        (v) => v['pixel_box'] = [0, 0, 101, 100],
        (v) => v['pixel_box'] = [0, 0, 0, 1],
        (v) => v['frame_size'] = [100, -1],
        (v) => v['pixel_box'] = [1.0, 2, 3, 4],
      ];
      for (final mutate in mutations) {
        await expectLater(
          create(service(_Core(change: mutate))),
          throwsFormatException,
        );
        await empty();
      }
    },
  );

  test(
    'handles backend failures, invalid JSON, missing file and response limits',
    () async {
      final cases = [
        _Core(exit: 2),
        _Core(raw: '{'),
        _Core(write: false),
        _Core(raw: 'x' * (LayoutPreviewService.maxResponseChars + 1)),
      ];
      for (final core in cases) {
        await expectLater(create(service(core)), throwsA(isA<Object>()));
        await empty();
      }
    },
  );

  test('invalid inputs cannot invoke core or create files', () async {
    final core = _Core();
    for (final args in [
      (-1, 5, 2, 'figure'),
      (17, 0, 2, 'figure'),
      (17, 5, -1, 'figure'),
      (17, 5, 2, 'text'),
    ]) {
      await expectLater(
        service(core).create(
          page: args.$1,
          run: args.$2,
          block: args.$3,
          blockType: args.$4,
        ),
        throwsArgumentError,
      );
    }
    expect(core.calls, isEmpty);
    await empty();
  });

  testWidgets('preview is explicit and explains limitations before executing', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final mock = _WidgetPreviewService();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: LayoutPreviewDialog(
            service: mock,
            page: 17,
            run: 5,
            block: 2,
            blockType: 'figure',
          ),
        ),
      ),
    );
    expect(mock.calls, 0);
    expect(find.textContaining('不会将插图加入 EPUB'), findsOneWidget);
    expect(find.textContaining('异常退出可能留下文件'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('create-layout-preview')));
    await tester.pumpAndSettle();
    expect(mock.calls, 1);
    expect(find.textContaining('最新核验 #8'), findsOneWidget);
    expect(find.textContaining('原页 SHA-256'), findsOneWidget);
  });

  testWidgets('backend refusal is visible and can be retried', (tester) async {
    final mock = _WidgetPreviewService(fail: true);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: LayoutPreviewDialog(
            service: mock,
            page: 17,
            run: 5,
            block: 2,
            blockType: 'figure',
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('create-layout-preview')));
    await tester.pumpAndSettle();
    expect(find.textContaining('unsafe frame'), findsOneWidget);
    expect(mock.calls, 1);
  });
}
