import 'dart:convert';

class RuntimeStatusSnapshot {
  const RuntimeStatusSnapshot({
    required this.runtimeRoot,
    required this.runtimeRootExists,
    required this.uvAvailable,
    required this.hardware,
    required this.paths,
    required this.backends,
    this.disk,
  });

  final String runtimeRoot;
  final bool runtimeRootExists;
  final bool uvAvailable;
  final Map<String, dynamic> hardware;
  final Map<String, dynamic> paths;
  final Map<String, RuntimeBackendSnapshot> backends;

  /// Disk preflight for the first run (issue #214); null when the CLI did
  /// not report it (older CLI builds).
  final DiskPreflight? disk;

  factory RuntimeStatusSnapshot.fromJsonText(String source) {
    final decoded = jsonDecode(source);
    if (decoded is! Map) {
      throw const FormatException('Runtime status must be a JSON object.');
    }
    final json = Map<String, dynamic>.from(decoded);
    if (json['schema_version'] != 1) {
      throw const FormatException('Unsupported runtime status schema version.');
    }
    final rawBackends = _asMap(json['backends']);
    final backends = <String, RuntimeBackendSnapshot>{};
    for (final entry in rawBackends.entries) {
      if (entry.value is Map) {
        backends[entry.key] = RuntimeBackendSnapshot.fromMap(
          Map<String, dynamic>.from(entry.value as Map),
        );
      }
    }
    return RuntimeStatusSnapshot(
      runtimeRoot: json['runtime_root'] is String
          ? json['runtime_root'] as String
          : '',
      runtimeRootExists: json['runtime_root_exists'] == true,
      uvAvailable: json['uv_available'] == true,
      hardware: _asMap(json['hardware']),
      paths: _asMap(json['paths']),
      backends: backends,
      disk: json['disk'] is Map
          ? DiskPreflight.fromMap(
              Map<String, dynamic>.from(json['disk'] as Map),
            )
          : null,
    );
  }

  /// All backend names reported by the CLI, core engines first, then the
  /// optional technology-route backends (issue #90 / #215).
  List<String> get backendNames => <String>[
    for (final name in const <String>['mineru', 'paddleocr'])
      if (backends.containsKey(name)) name,
    for (final entry in backends.entries)
      if (entry.value.optional) entry.key,
  ];

  /// Core engines installed by "开始安装全部后端".
  List<String> get coreBackendNames => <String>[
    for (final name in backendNames)
      if (backends[name]?.optional != true) name,
  ];

  /// Optional technology-route backends (PaddleOCR-VL, Surya, …), shown in
  /// the advanced section and installed only on explicit request.
  List<String> get optionalBackendNames => <String>[
    for (final name in backendNames)
      if (backends[name]?.optional == true) name,
  ];

  static Map<String, dynamic> _asMap(Object? value) =>
      value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};
}

class RuntimeBackendSnapshot {
  const RuntimeBackendSnapshot({
    required this.state,
    required this.failedStep,
    required this.environmentPath,
    required this.environmentExists,
    this.optional = false,
    this.estimates,
  });

  final String state;
  final String? failedStep;
  final String environmentPath;
  final bool environmentExists;

  /// Three-phase size estimate (issue #214); null for older CLI builds.
  final BackendSizeEstimate? estimates;

  /// True for the optional technology-route backends (issue #90) that are
  /// not part of the default first-run install.
  final bool optional;

  factory RuntimeBackendSnapshot.fromMap(Map<String, dynamic> json) =>
      RuntimeBackendSnapshot(
        state: json['state'] is String ? json['state'] as String : 'unknown',
        failedStep: json['failed_step'] is String
            ? json['failed_step'] as String
            : null,
        environmentPath: json['environment_path'] is String
            ? json['environment_path'] as String
            : '',
        environmentExists: json['environment_exists'] == true,
        optional: json['optional'] == true,
        estimates: json['estimates'] is Map
            ? BackendSizeEstimate.fromMap(
                Map<String, dynamic>.from(json['estimates'] as Map),
              )
            : null,
      );
}

/// Three-phase size estimate for one backend (issue #214): network download,
/// on-disk environment, and model weights. All values are estimates, never
/// measurements — the UI must present them as 预估.
class BackendSizeEstimate {
  const BackendSizeEstimate({
    required this.downloadBytes,
    required this.installBytes,
    required this.modelBytes,
    required this.totalBytes,
    this.note = '',
  });

  final int downloadBytes;
  final int installBytes;
  final int modelBytes;
  final int totalBytes;
  final String note;

  factory BackendSizeEstimate.fromMap(Map<String, dynamic> json) {
    int read(String key) => json[key] is num ? (json[key] as num).toInt() : 0;
    return BackendSizeEstimate(
      downloadBytes: read('download_bytes'),
      installBytes: read('install_bytes'),
      modelBytes: read('model_bytes'),
      totalBytes: read('total_bytes'),
      note: json['note'] is String ? json['note'] as String : '',
    );
  }

  String formatTotal() => formatBytes(totalBytes);
}

/// Disk preflight result for the first run (issue #214).
class DiskPreflight {
  const DiskPreflight({
    required this.path,
    required this.freeBytes,
    required this.totalBytes,
    required this.requiredBytes,
    required this.sufficient,
    required this.missingBytes,
    required this.advice,
    this.backends = const <String, BackendSizeEstimate>{},
  });

  final String path;
  final int freeBytes;
  final int totalBytes;
  final int requiredBytes;
  final bool sufficient;
  final int missingBytes;
  final String advice;
  final Map<String, BackendSizeEstimate> backends;

  factory DiskPreflight.fromMap(Map<String, dynamic> json) {
    int read(String key) => json[key] is num ? (json[key] as num).toInt() : 0;
    final rawBackends = json['backends'];
    final backends = <String, BackendSizeEstimate>{};
    if (rawBackends is Map) {
      for (final entry in rawBackends.entries) {
        if (entry.value is Map) {
          backends[entry.key] = BackendSizeEstimate.fromMap(
            Map<String, dynamic>.from(entry.value as Map),
          );
        }
      }
    }
    return DiskPreflight(
      path: json['path'] is String ? json['path'] as String : '',
      freeBytes: read('free_bytes'),
      totalBytes: read('total_bytes'),
      requiredBytes: read('required_bytes'),
      sufficient: json['sufficient'] == true,
      missingBytes: read('missing_bytes'),
      advice: json['advice'] is String ? json['advice'] as String : '',
      backends: backends,
    );
  }
}

/// Human-readable byte size, e.g. `12.5 GB`.
String formatBytes(int value) {
  var size = value.toDouble();
  for (final unit in const <String>['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024 || unit == 'TB') {
      return unit == 'B'
          ? '${size.toInt()} $unit'
          : '${size.toStringAsFixed(1)} $unit';
    }
    size /= 1024;
  }
  return '${size.toStringAsFixed(1)} TB';
}
