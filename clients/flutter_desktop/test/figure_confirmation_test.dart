import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/evidence/layout_preview_dialog.dart';
import 'package:scribeforge_desktop/services/cli_executor.dart';
import 'package:scribeforge_desktop/services/figure_confirmation_service.dart';
import 'package:scribeforge_desktop/services/layout_preview_service.dart';

final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGPgMkr5DwACigGgBE3sHAAAAABJRU5ErkJggg==',
);
final _contextPng = _png;
const _token =
    'fig:v8:r5:b2:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:0.1:0.2:0.3:0.4';

class _Core implements ScribeForgeCliExecutor {
  _Core({this.exit = 0, this.mutate});
  final int exit;
  final void Function(Map<String, dynamic>)? mutate;
  final calls = <List<String>>[];
  final writtenCrops = <List<int>>[];

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
    if (arguments.first == 'layout-preview') {
      final path = arguments[arguments.indexOf('--out') + 1];
      final contextPath = arguments[arguments.indexOf('--context-out') + 1];
      await File(path).writeAsBytes(_png);
      await File(contextPath).writeAsBytes(_contextPng);
      final result = <String, dynamic>{
        'schema_version': 1,
        'status': 'unverified_preview',
        'project_id': 7,
        'page_index': 17,
        'verification_id': 8,
        'aligned_pair_count': 3,
        'run_id': 5,
        'engine': 'PaddleOCR',
        'block_index': 2,
        'block_type': 'figure',
        'source_sha256': 'a' * 64,
        'frame_kind': 'original',
        'frame_size': [100, 200],
        'normalized_box': [0.1, 0.2, 0.3, 0.4],
        'pixel_box': [10, 20, 11, 21],
        'preview_sha256': sha256.convert(_png).toString(),
        'output': path,
        'confirmation_token': _token,
        'context': {
          'output': contextPath,
          'sha256': sha256.convert(_contextPng).toString(),
          'size': [100, 200],
        },
      };
      onOutput(jsonEncode(result));
      return 0;
    }
    if (exit != 0) {
      onError('anchor is past the aligned pairs');
      return exit;
    }
    final cropPath = arguments[arguments.indexOf('--crop') + 1];
    writtenCrops.add(await File(cropPath).readAsBytes());
    final result = <String, dynamic>{
      'schema_version': 1,
      'status': 'figure_confirmed',
      'id': 11,
      'project_id': 7,
      'page_index': 17,
      'verification_id': 8,
      'run_id': 5,
      'block_index': 2,
      'anchor_pair_index': int.parse(
        arguments[arguments.indexOf('--anchor-pair') + 1],
      ),
      'crop_sha256': sha256.convert(_png).toString(),
      'source_sha256': 'a' * 64,
      'caption': arguments.contains('--caption')
          ? arguments[arguments.indexOf('--caption') + 1]
          : '',
      'reason': arguments[arguments.indexOf('--reason') + 1],
    };
    mutate?.call(result);
    onOutput(jsonEncode(result));
    return 0;
  }
}

class _WidgetPreviewService extends LayoutPreviewService {
  _WidgetPreviewService({ScribeForgeCliExecutor? executor})
    : super(
        executor: executor ?? _Core(),
        executable: 'core',
        storePath: '/project',
        projectId: 7,
      );
  @override
  Future<LayoutPreview> create({
    required int page,
    required int run,
    required int block,
    required String blockType,
  }) async => LayoutPreview(
    bytes: _png,
    contextBytes: _contextPng,
    contextSha256: sha256.convert(_contextPng).toString(),
    contextSize: const [100, 200],
    verificationId: 8,
    engine: 'PaddleOCR',
    sourceSha256: 'a' * 64,
    previewSha256: sha256.convert(_png).toString(),
    pixelBox: const [10, 20, 11, 21],
    frameSize: const [100, 200],
    confirmationToken: _token,
    alignedPairCount: 3,
  );
}

class _FakeConfirmService extends FigureConfirmationService {
  _FakeConfirmService()
    : super(
        executor: _Core(),
        executable: 'core',
        storePath: '/project',
        projectId: 7,
      );
  final calls = <Map<String, Object>>[];

  @override
  Future<FigureConfirmation> confirm({
    required int page,
    required int run,
    required int block,
    required List<int> cropBytes,
    required String confirmationToken,
    required int anchorPair,
    required String reason,
    String caption = '',
  }) async {
    calls.add({
      'page': page,
      'run': run,
      'block': block,
      'anchor': anchorPair,
      'reason': reason,
      'caption': caption,
      'token': confirmationToken,
      'crop': cropBytes.length,
    });
    if (reason.contains('拒绝')) {
      throw const FigureConfirmationFailure('确认失败（退出码 2）：anchor past pairs');
    }
    return FigureConfirmation(
      id: 11,
      page: page,
      anchorPair: anchorPair,
      cropSha256: sha256.convert(cropBytes).toString(),
      caption: caption,
      reason: reason,
    );
  }
}

void main() {
  late Directory root;
  setUp(() async {
    root = await Directory.systemTemp.createTemp('confirm-test-');
  });
  tearDown(() async {
    await root.delete(recursive: true);
  });

  FigureConfirmationService service(_Core core) => FigureConfirmationService(
    executor: core,
    executable: 'core',
    storePath: '/project',
    projectId: 7,
    tempRoot: root,
  );

  test(
    'confirm sends bound arguments, verifies receipt and cleans up',
    () async {
      final core = _Core();
      final receipt = await service(core).confirm(
        page: 17,
        run: 5,
        block: 2,
        cropBytes: _png,
        confirmationToken: _token,
        anchorPair: 2,
        reason: '  checked against scan  ',
        caption: '图一',
      );
      expect(receipt.id, 11);
      expect(receipt.page, 17);
      expect(receipt.anchorPair, 2);
      expect(receipt.caption, '图一');
      expect(receipt.reason, 'checked against scan');
      expect(core.writtenCrops.single, _png);
      expect(
        core.calls.single,
        containsAllInOrder([
          'confirm-figure',
          '--store',
          '/project',
          '--project-id',
          '7',
          '--page',
          '17',
          '--run',
          '5',
          '--block',
          '2',
          '--crop',
          '--confirmation-token',
          _token,
          '--anchor-pair',
          '2',
          '--caption',
          '图一',
          '--reason',
          'checked against scan',
          '--json',
        ]),
      );
      expect(await root.list().toList(), isEmpty);
    },
  );

  test(
    'rejects bad identity, token, anchor and reason before invoking',
    () async {
      final core = _Core();
      for (final args in [
        (-1, 5, 2, _token, 0, 'reason'),
        (17, 0, 2, _token, 0, 'reason'),
        (17, 5, -1, _token, 0, 'reason'),
        (17, 5, 2, 'nope', 0, 'reason'),
        (17, 5, 2, _token, -1, 'reason'),
        (17, 5, 2, _token, 0, '   '),
      ]) {
        await expectLater(
          service(core).confirm(
            page: args.$1,
            run: args.$2,
            block: args.$3,
            cropBytes: _png,
            confirmationToken: args.$4,
            anchorPair: args.$5,
            reason: args.$6,
          ),
          throwsArgumentError,
        );
      }
      expect(core.calls, isEmpty);
      expect(await root.list().toList(), isEmpty);
    },
  );

  test(
    'surfaces CLI refusal and rejects mismatched receipt with cleanup',
    () async {
      await expectLater(
        service(_Core(exit: 2)).confirm(
          page: 17,
          run: 5,
          block: 2,
          cropBytes: _png,
          confirmationToken: _token,
          anchorPair: 9,
          reason: 'checked',
        ),
        throwsA(isA<FigureConfirmationFailure>()),
      );
      expect(await root.list().toList(), isEmpty);

      await expectLater(
        service(_Core(mutate: (v) => v['status'] = 'unverified_preview'))
            .confirm(
              page: 17,
              run: 5,
              block: 2,
              cropBytes: _png,
              confirmationToken: _token,
              anchorPair: 1,
              reason: 'checked',
            ),
        throwsFormatException,
      );
      expect(await root.list().toList(), isEmpty);
    },
  );

  testWidgets('dialog exposes the confirmation form after preview', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final core = _Core();
    final confirmService = _FakeConfirmService();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: LayoutPreviewDialog(
            service: _WidgetPreviewService(executor: core),
            confirmationService: confirmService,
            page: 17,
            run: 5,
            block: 2,
            blockType: 'figure',
          ),
        ),
      ),
    );
    expect(find.byKey(const ValueKey('confirm-inline-figure')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('create-layout-preview')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('confirm-inline-figure')), findsOneWidget);
    expect(find.byKey(const ValueKey('confirm-anchor-pair')), findsOneWidget);
    expect(find.byKey(const ValueKey('confirm-reason')), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey('confirm-anchor-pair')),
      '2',
    );
    await tester.enterText(
      find.byKey(const ValueKey('confirm-reason')),
      '与原页对照，裁切完整',
    );
    await tester.enterText(find.byKey(const ValueKey('confirm-caption')), '图一');
    await tester.tap(find.byKey(const ValueKey('confirm-inline-figure')));
    await tester.pumpAndSettle();
    expect(find.textContaining('已确认插图 #11'), findsOneWidget);
    expect(confirmService.calls.single['anchor'], 2);
    expect(confirmService.calls.single['caption'], '图一');
    expect(confirmService.calls.single['token'], _token);
    expect(confirmService.calls.single['crop'], _png.length);
    expect(await root.list().toList(), isEmpty);
  });

  testWidgets('dialog blocks confirm until anchor and reason are valid', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1000, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final core = _Core();
    final confirmService = _FakeConfirmService();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: LayoutPreviewDialog(
            service: _WidgetPreviewService(executor: core),
            confirmationService: confirmService,
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
    await tester.enterText(
      find.byKey(const ValueKey('confirm-anchor-pair')),
      '99',
    );
    await tester.enterText(find.byKey(const ValueKey('confirm-reason')), 'x');
    await tester.tap(find.byKey(const ValueKey('confirm-inline-figure')));
    await tester.pumpAndSettle();
    expect(find.textContaining('必须在 0 到 3 之间'), findsOneWidget);
    expect(confirmService.calls, isEmpty);
    expect(core.calls.every((call) => call.first == 'layout-preview'), isTrue);
  });
}
