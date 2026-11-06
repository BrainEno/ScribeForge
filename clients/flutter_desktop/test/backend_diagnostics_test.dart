import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/backend_diagnostics.dart';

void main() {
  test('a missing core executable produces an initialization action', () {
    final diagnosis = diagnoseCoreLaunchFailure(
      const ProcessException('scribeforge', <String>['runtime-status']),
      'scribeforge',
    );

    expect(diagnosis.requiresRuntimeSetup, isTrue);
    expect(diagnosis.message, contains('处理核心'));
    expect(diagnosis.message, contains('设置'));
    expect(diagnosis.message, contains('初始化'));
  });

  test('a missing worker module is translated into a repairable setup error', () {
    final diagnosis = diagnoseProcessingFailure(
      'Traceback: ModuleNotFoundError: No module named \'scribeforge.workers\'',
    );

    expect(diagnosis.requiresRuntimeSetup, isTrue);
    expect(diagnosis.message, contains('OCR 运行环境'));
    expect(diagnosis.message, contains('初始化环境'));
    expect(diagnosis.message, contains('ModuleNotFoundError'));
  });

  test('a not-ready managed backend is also routed to initialization', () {
    final diagnosis = diagnoseProcessingFailure(
      'error: backend paddleocr is not ready (state=missing)',
    );

    expect(diagnosis.requiresRuntimeSetup, isTrue);
    expect(diagnosis.message, contains('尚未就绪'));
  });

  test('an unrelated processing error keeps its original meaning', () {
    const output = 'error: source PDF SHA-256 does not match project evidence';
    final diagnosis = diagnoseProcessingFailure(output);

    expect(diagnosis.requiresRuntimeSetup, isFalse);
    expect(diagnosis.message, output);
  });
}
