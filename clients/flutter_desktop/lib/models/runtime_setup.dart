import 'dart:convert';

enum RuntimeSetupMessageType { progress, result }

class RuntimeSetupMessage {
  const RuntimeSetupMessage._({
    required this.type,
    this.backend,
    this.phase,
    this.state,
    this.step,
    this.completedSteps = 0,
    this.totalSteps = 0,
    this.action,
    this.success,
    this.message,
    this.backends = const <String, RuntimeSetupBackendResult>{},
  });

  final RuntimeSetupMessageType type;
  final String? backend;
  final String? phase;
  final String? state;
  final String? step;
  final int completedSteps;
  final int totalSteps;
  final String? action;
  final bool? success;
  final String? message;
  final Map<String, RuntimeSetupBackendResult> backends;

  bool get isProgress => type == RuntimeSetupMessageType.progress;
  double? get fractionComplete =>
      totalSteps > 0 ? completedSteps / totalSteps : null;

  factory RuntimeSetupMessage.fromJsonLine(String source) {
    final Object? decoded;
    try {
      decoded = jsonDecode(source);
    } on FormatException catch (error) {
      throw FormatException('Invalid setup JSON message: ${error.message}');
    }
    if (decoded is! Map) {
      throw const FormatException('Setup message must be a JSON object.');
    }
    final json = Map<String, dynamic>.from(decoded);
    if (json['schema_version'] != 1) {
      throw const FormatException('Unsupported setup protocol schema version.');
    }

    switch (json['type']) {
      case 'progress':
        final backend = _requiredString(json, 'backend');
        if (backend != 'mineru' && backend != 'paddleocr') {
          throw FormatException('Unknown OCR backend in setup progress: $backend');
        }
        final completedSteps = _requiredInt(json, 'completed_steps');
        final totalSteps = _requiredInt(json, 'total_steps');
        if (completedSteps < 0 || totalSteps < 0 || completedSteps > totalSteps) {
          throw const FormatException('Setup progress step counts are invalid.');
        }
        return RuntimeSetupMessage._(
          type: RuntimeSetupMessageType.progress,
          backend: backend,
          phase: _requiredString(json, 'phase'),
          state: _requiredString(json, 'state'),
          step: json['step'] is String ? json['step'] as String : null,
          completedSteps: completedSteps,
          totalSteps: totalSteps,
        );
      case 'result':
        final action = _requiredString(json, 'action');
        if (action != 'install' && action != 'repair') {
          throw FormatException('Unknown setup action: $action');
        }
        if (json['success'] is! bool) {
          throw const FormatException('Setup result must include a success flag.');
        }
        final rawBackends = json['backends'];
        if (rawBackends is! Map) {
          throw const FormatException('Setup result must include backend states.');
        }
        final backends = <String, RuntimeSetupBackendResult>{};
        for (final name in const <String>['mineru', 'paddleocr']) {
          final raw = rawBackends[name];
          if (raw is Map) {
            backends[name] = RuntimeSetupBackendResult.fromMap(
              Map<String, dynamic>.from(raw),
            );
          }
        }
        return RuntimeSetupMessage._(
          type: RuntimeSetupMessageType.result,
          backend: json['backend'] is String ? json['backend'] as String : null,
          action: action,
          success: json['success'] as bool,
          message: json['message'] is String ? json['message'] as String : null,
          backends: Map<String, RuntimeSetupBackendResult>.unmodifiable(backends),
        );
      default:
        throw FormatException('Unknown setup message type: ${json['type']}');
    }
  }

  static String _requiredString(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is String && value.isNotEmpty) return value;
    throw FormatException('Setup message field "$field" must be a string.');
  }

  static int _requiredInt(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is int) return value;
    throw FormatException('Setup message field "$field" must be an integer.');
  }
}

class RuntimeSetupBackendResult {
  const RuntimeSetupBackendResult({required this.state, this.failedStep});

  final String state;
  final String? failedStep;

  factory RuntimeSetupBackendResult.fromMap(Map<String, dynamic> json) {
    final state = json['state'];
    if (state is! String || state.isEmpty) {
      throw const FormatException('Backend setup result is missing its state.');
    }
    final failedStep = json['failed_step'];
    if (failedStep != null && failedStep is! String) {
      throw const FormatException('Backend failed_step must be a string or null.');
    }
    return RuntimeSetupBackendResult(
      state: state,
      failedStep: failedStep as String?,
    );
  }
}
