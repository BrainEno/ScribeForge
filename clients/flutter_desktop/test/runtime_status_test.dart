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

    test('parses optional technology-route backends (#90/#215)', () {
      final snapshot = RuntimeStatusSnapshot.fromJsonText('''
{
  "schema_version": 1,
  "runtime_root": "/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {},
  "paths": {},
  "backends": {
    "mineru": {"state": "ready", "failed_step": null,
      "environment_path": "/runtime/envs/mineru", "environment_exists": true,
      "optional": false},
    "paddleocr": {"state": "ready", "failed_step": null,
      "environment_path": "/runtime/envs/paddleocr", "environment_exists": true,
      "optional": false},
    "paddleocr-vl": {"state": "missing", "failed_step": null,
      "environment_path": "/runtime/envs/paddleocr-vl", "environment_exists": false,
      "optional": true},
    "surya": {"state": "missing", "failed_step": null,
      "environment_path": "/runtime/envs/surya", "environment_exists": false,
      "optional": true}
  }
}
''');
      expect(snapshot.backends.length, 4);
      expect(snapshot.backends['paddleocr-vl']?.optional, isTrue);
      expect(snapshot.backends['mineru']?.optional, isFalse);
      expect(snapshot.backendNames, [
        'mineru',
        'paddleocr',
        'paddleocr-vl',
        'surya',
      ]);
      expect(snapshot.coreBackendNames, ['mineru', 'paddleocr']);
      expect(snapshot.optionalBackendNames, ['paddleocr-vl', 'surya']);
    });

    test('parses the disk preflight and per-backend estimates (#214)', () {
      final snapshot = RuntimeStatusSnapshot.fromJsonText('''
{
  "schema_version": 1,
  "runtime_root": "/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {},
  "paths": {},
  "backends": {
    "mineru": {"state": "ready", "failed_step": null,
      "environment_path": "/runtime/envs/mineru", "environment_exists": true,
      "optional": false,
      "estimates": {"download_bytes": 6442450944, "install_bytes": 8589934592,
        "model_bytes": 4294967296, "total_bytes": 19327352832, "note": "含模型"}},
    "paddleocr": {"state": "missing", "failed_step": null,
      "environment_path": "/runtime/envs/paddleocr", "environment_exists": false,
      "optional": false, "estimates": {"download_bytes": 1, "install_bytes": 2,
        "model_bytes": 3, "total_bytes": 6, "note": ""}}
  },
  "disk": {
    "path": "/runtime",
    "runtime_root": "/runtime",
    "free_bytes": 5000000000,
    "total_bytes": 500000000000,
    "used_bytes": 495000000000,
    "required_bytes": 30000000000,
    "headroom_factor": 1.1,
    "sufficient": false,
    "missing_bytes": 25000000000,
    "advice": "预计需要约 27.9 GB，当前盘剩余 4.7 GB，还差约 23.3 GB。",
    "estimated": true,
    "tools_download_bytes": 31457280,
    "backends": {
      "mineru": {"download_bytes": 6442450944, "install_bytes": 8589934592,
        "model_bytes": 4294967296, "total_bytes": 19327352832, "note": "含模型"}
    }
  }
}
''');
      final disk = snapshot.disk;
      expect(disk, isNotNull);
      expect(disk!.path, '/runtime');
      expect(disk.freeBytes, 5000000000);
      expect(disk.requiredBytes, 30000000000);
      expect(disk.sufficient, isFalse);
      expect(disk.missingBytes, 25000000000);
      expect(disk.advice, contains('还差约'));
      expect(disk.backends['mineru']?.totalBytes, 19327352832);
      expect(snapshot.backends['mineru']?.estimates?.formatTotal(), '18.0 GB');
      expect(snapshot.backends['paddleocr']?.estimates?.totalBytes, 6);
    });

    test('disk preflight is null for older CLI reports', () {
      final snapshot = RuntimeStatusSnapshot.fromJsonText('''
{
  "schema_version": 1,
  "runtime_root": "/runtime",
  "runtime_root_exists": true,
  "uv_available": true,
  "hardware": {},
  "paths": {},
  "backends": {}
}
''');
      expect(snapshot.disk, isNull);
      expect(snapshot.backends, isEmpty);
    });
  });

  group('formatBytes', () {
    test('formats byte sizes in human units', () {
      expect(formatBytes(0), '0 B');
      expect(formatBytes(512), '512 B');
      expect(formatBytes(2048), '2.0 KB');
      expect(formatBytes(5 * 1024 * 1024), '5.0 MB');
      expect(
        formatBytes(12 * 1024 * 1024 * 1024 + 512 * 1024 * 1024),
        '12.5 GB',
      );
    });
  });
}
