import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_setup.dart';

void main() {
  group('RuntimeSetupMessage', () {
    test('parses a per-step progress event', () {
      final event = RuntimeSetupMessage.fromJsonLine(
        '{"schema_version":1,"type":"progress","backend":"mineru",'
        '"phase":"models","state":"step_started","step":"download",'
        '"completed_steps":2,"total_steps":5}',
      );

      expect(event.type, RuntimeSetupMessageType.progress);
      expect(event.backend, 'mineru');
      expect(event.phase, 'models');
      expect(event.state, 'step_started');
      expect(event.step, 'download');
      expect(event.completedSteps, 2);
      expect(event.totalSteps, 5);
      expect(event.fractionComplete, 0.4);
    });

    test('parses a final result with backend-specific failed steps', () {
      final event = RuntimeSetupMessage.fromJsonLine(
        '{"schema_version":1,"type":"result","action":"repair",'
        '"success":false,"message":"network unavailable","backends":{'
        '"mineru":{"state":"ready","failed_step":null},'
        '"paddleocr":{"state":"failed","failed_step":"models:download"}}}',
      );

      expect(event.type, RuntimeSetupMessageType.result);
      expect(event.action, 'repair');
      expect(event.success, isFalse);
      expect(event.message, 'network unavailable');
      expect(event.backends['mineru']?.state, 'ready');
      expect(event.backends['paddleocr']?.failedStep, 'models:download');
    });

    test('rejects unsupported or malformed protocol messages', () {
      expect(
        () => RuntimeSetupMessage.fromJsonLine(
          '{"schema_version":2,"type":"progress"}',
        ),
        throwsFormatException,
      );
      expect(
        () => RuntimeSetupMessage.fromJsonLine('not-json'),
        throwsFormatException,
      );
    });
  });
}
