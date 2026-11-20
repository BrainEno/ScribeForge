import 'dart:convert';

import 'runtime_status.dart';

/// Empty paths deliberately track platform defaults, not a snapshot of them.
class RuntimeLocation {
  const RuntimeLocation({this.runtimeRoot = '', this.modelsRoot = ''});

  factory RuntimeLocation.fromJson(Object? json) {
    if (json is! Map) return const RuntimeLocation();
    return RuntimeLocation(
      runtimeRoot: _path(json['runtime_root']),
      modelsRoot: _path(json['models_root']),
    );
  }

  final String runtimeRoot;
  final String modelsRoot;

  bool get isDefault => runtimeRoot.isEmpty && modelsRoot.isEmpty;
  bool get usesExistingModels => modelsRoot.isNotEmpty;

  static String _path(Object? value) => value is String ? value.trim() : '';

  Map<String, String> toJson() => <String, String>{
    'runtime_root': runtimeRoot,
    'models_root': modelsRoot,
  };

  List<String> argumentsFor(List<String> arguments, {bool check = false}) {
    if (arguments.isEmpty) return arguments;
    if (!check && (isDefault || !locationCommands.contains(arguments.first))) {
      return arguments;
    }
    return <String>[
      if (!check) '--location-profile',
      if (usesExistingModels) ...<String>['--model-location', modelsRoot],
      ...arguments,
      if (runtimeRoot.isNotEmpty &&
          !arguments.contains('--runtime-root')) ...<String>[
        '--runtime-root',
        runtimeRoot,
      ],
    ];
  }

  static const Set<String> locationCommands = <String>{
    'runtime-status',
    'runtime-clean',
    'setup',
    'process',
    'repair',
  };

  @override
  bool operator ==(Object other) =>
      other is RuntimeLocation &&
      runtimeRoot == other.runtimeRoot &&
      modelsRoot == other.modelsRoot;

  @override
  int get hashCode => Object.hash(runtimeRoot, modelsRoot);
}

class RuntimeLocationPreferences {
  const RuntimeLocationPreferences({
    this.active = const RuntimeLocation(),
    this.previous,
  });

  factory RuntimeLocationPreferences.fromJson(Object? json) {
    if (json is! Map || json['schema_version'] != 1) {
      return const RuntimeLocationPreferences();
    }
    return RuntimeLocationPreferences(
      active: RuntimeLocation.fromJson(json['active']),
      previous: json['previous'] is Map
          ? RuntimeLocation.fromJson(json['previous'])
          : null,
    );
  }

  final RuntimeLocation active;
  final RuntimeLocation? previous;

  RuntimeLocationPreferences activate(RuntimeLocation location) =>
      location == active
      ? this
      : RuntimeLocationPreferences(active: location, previous: active);

  Map<String, Object?> toJson() => <String, Object?>{
    'schema_version': 1,
    'active': active.toJson(),
    'previous': previous?.toJson(),
  };
}

class RuntimeLocationCheckFailure implements Exception {
  const RuntimeLocationCheckFailure(this.code, this.detail);

  final String code;
  final String detail;

  String get title => switch (code) {
    'relative_path' => '请输入完整目录路径',
    'drive_root' => '请选择磁盘下面的一个文件夹',
    'unavailable_drive' => '找不到此磁盘，请检查连接',
    'not_writable' => '这个位置不可写入',
    'not_directory' => '选择的路径不是文件夹',
    'overlapping_paths' => '运行目录与已有模型目录必须分开',
    'project_directory' => '不能在书籍项目内安装运行环境',
    'not_dedicated' => '请选择空目录或已有的专用运行目录',
    'binding_mismatch' || 'already_managed' => '该运行目录已绑定其他模型位置',
    'missing_models' || 'missing_model_files' => '找不到完整的已有模型',
    _ => '无法检查候选位置',
  };

  @override
  String toString() => '$title：$detail';
}

class RuntimeLocationCheck {
  const RuntimeLocationCheck({
    required this.runtimeRoot,
    required this.modelsRoot,
    required this.externalModels,
    required this.disk,
    required this.backends,
    required this.checkedDirectories,
  });

  factory RuntimeLocationCheck.fromOutput(String output) {
    final decoded = jsonDecode(output);
    if (decoded is! Map || decoded['schema_version'] != 1) {
      throw const FormatException('不支持的运行目录检查响应。');
    }
    if (decoded['ok'] == false) {
      throw RuntimeLocationCheckFailure(
        decoded['code'] as String? ?? 'unknown',
        decoded['message'] as String? ?? '未返回失败原因',
      );
    }
    final disk = decoded['disk'];
    final backends = decoded['backends'];
    final directories = decoded['checked_model_directories'];
    final runtimeRoot = decoded['runtime_root'];
    final modelsRoot = decoded['models_root'];
    if (decoded['ok'] != true ||
        runtimeRoot is! String ||
        runtimeRoot.isEmpty ||
        modelsRoot is! String ||
        modelsRoot.isEmpty ||
        disk is! Map<String, dynamic> ||
        disk['sufficient'] is! bool ||
        disk['required_bytes'] is! num ||
        disk['free_bytes'] is! num ||
        decoded['external_models'] is! bool ||
        backends is! Map ||
        directories is! List ||
        !directories.every((entry) => entry is String)) {
      throw const FormatException('候选运行目录报告不完整。');
    }
    return RuntimeLocationCheck(
      runtimeRoot: runtimeRoot,
      modelsRoot: modelsRoot,
      externalModels: decoded['external_models'] as bool,
      disk: DiskPreflight.fromMap(disk),
      backends: <String, String>{
        for (final name in coreBackends)
          name: backends[name] as String? ?? 'unknown',
      },
      checkedDirectories: List<String>.unmodifiable(directories.cast<String>()),
    );
  }

  final String runtimeRoot;
  final String modelsRoot;
  final bool externalModels;
  final DiskPreflight disk;
  final Map<String, String> backends;
  final List<String> checkedDirectories;

  static const List<String> coreBackends = <String>['mineru', 'paddleocr'];

  bool get bothReady => coreBackends.every((name) => backends[name] == 'ready');

  RuntimeLocation canonicalize(RuntimeLocation selection) => RuntimeLocation(
    runtimeRoot: selection.runtimeRoot.isEmpty ? '' : runtimeRoot,
    modelsRoot: externalModels ? modelsRoot : '',
  );

  bool sameTarget(RuntimeLocationCheck other) =>
      runtimeRoot == other.runtimeRoot &&
      modelsRoot == other.modelsRoot &&
      externalModels == other.externalModels;
}
