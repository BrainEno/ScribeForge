import 'dart:convert';

class RuntimeStatusSnapshot {
  const RuntimeStatusSnapshot({
    required this.runtimeRoot,
    required this.runtimeRootExists,
    required this.uvAvailable,
    required this.hardware,
    required this.paths,
    required this.backends,
  });

  final String runtimeRoot;
  final bool runtimeRootExists;
  final bool uvAvailable;
  final Map<String, dynamic> hardware;
  final Map<String, dynamic> paths;
  final Map<String, RuntimeBackendSnapshot> backends;

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
  });

  final String state;
  final String? failedStep;
  final String environmentPath;
  final bool environmentExists;

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
      );
}
