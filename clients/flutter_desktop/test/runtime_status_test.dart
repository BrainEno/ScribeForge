import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/runtime_status.dart';

void main() {
  group('RuntimeStatusSnapshot', () {
    test('parses hardware, paths, backend state and failed step', () {
      final snapshot = RuntimeStatusSnapshot.fromJsonText('''
{
  "schema_version": 1,
  "runtime_root": "/tmp/ScribeForge/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {
    "os": "linux",
    "architecture": "x86_64",
    "gpu_name": "NVIDIA RTX 5080",
    "nvidia_driver": "580.00",
    "vram_mb": 16384,
    "ram_mb": 24576
  },
  "paths": {
    "models": "/tmp/ScribeForge/runtime/models",
    "models_exists": true,
    "cache": "/tmp/ScribeForge/runtime/cache",
    "cache_exists": false,
    "tools": "/tmp/ScribeForge/runtime/tools",
    "tools_exists": true
  },
  "backends": {
    "mineru": {
      "state": "ready",
      "failed_step": null,
      "environment_path": "/tmp/ScribeForge/runtime/envs/mineru",
      "environment_exists": true
    },
    "paddleocr": {
      "state": "failed",
      "failed_step": "models:download",
      "environment_path": "/tmp/ScribeForge/runtime/envs/paddleocr",
      "environment_exists": true
    }
  }
}
''');

      expect(snapshot.runtimeRoot, '/tmp/ScribeForge/runtime');
      expect(snapshot.runtimeRootExists, isTrue);
      expect(snapshot.uvAvailable, isTrue);
      expect(snapshot.hardware['gpu_name'], 'NVIDIA RTX 5080');
      expect(snapshot.hardware['vram_mb'], 16384);
      expect(snapshot.paths['cache_exists'], isFalse);
      expect(snapshot.backends['mineru']?.state, 'ready');
      expect(snapshot.backends['paddleocr']?.failedStep, 'models:download');
    });

    test('rejects an unsupported schema', () {
      expect(
        () => RuntimeStatusSnapshot.fromJsonText('{"schema_version": 2}'),
        throwsFormatException,
      );
    });

    test('defaults absent backend records to an empty map', () {
      final snapshot = RuntimeStatusSnapshot.fromJsonText('''
{
  "schema_version": 1,
  "runtime_root": "/runtime",
  "runtime_root_exists": false,
  "uv_available": false,
  "hardware": {},
  "paths": {},
  "backends": {}
}
''');
      expect(snapshot.backends, isEmpty);
    });
  });
}
