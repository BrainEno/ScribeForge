import 'dart:io';

/// One probed Python interpreter: the executable plus any launcher arguments
/// (the Windows `py` launcher needs `-3`, so it cannot be used as a bare path).
class DetectedPython {
  const DetectedPython(this.executable, this.arguments, this.versionLine);

  final String executable;
  final List<String> arguments;
  final String versionLine;

  /// Display label for logs and notices, e.g. `python3 (Python 3.12.4)`.
  String get label {
    final version = versionLine.trim();
    return version.isEmpty ? executable : '$executable ($version)';
  }
}

typedef PythonProbe =
    Future<String?> Function(String executable, List<String> arguments);

/// Finds a usable Python 3 interpreter on the user's machine.
///
/// Desktop users must not have to type a path: the locator probes the usual
/// launchers in a deterministic order and returns the first one that answers
/// `--version`. Every probe is injectable so tests never spawn real processes.
class PythonLocator {
  const PythonLocator({this.probe, this.operatingSystem, this.environment});

  final PythonProbe? probe;
  final String? operatingSystem;
  final Map<String, String>? environment;

  String get _os => operatingSystem ?? Platform.operatingSystem;

  static Future<String?> _defaultProbe(
    String executable,
    List<String> arguments,
  ) async {
    try {
      final result = await Process.run(executable, arguments);
      if (result.exitCode != 0) return null;
      final output = '${result.stdout}${result.stderr}'.trim();
      return output.isEmpty ? null : output.split('\n').first;
    } on Object {
      // ProcessException (not found) and anything else: just try the next one.
      return null;
    }
  }

  /// Candidate (executable, launcher arguments) pairs, best first.
  List<(String, List<String>)> candidates() {
    if (_os == 'windows') {
      final localAppData = environment?['LOCALAPPDATA'] ?? '';
      final versioned = <String>[
        if (localAppData.isNotEmpty)
          for (final version in const <String>[
            'Python314',
            'Python313',
            'Python312',
            'Python311',
            'Python310',
          ])
            '$localAppData\\Programs\\Python\\$version\\python.exe',
      ];
      return <(String, List<String>)>[
        ('py', const <String>['-3']),
        ('python', const <String>[]),
        ('python3', const <String>[]),
        for (final path in versioned) (path, const <String>[]),
      ];
    }
    return <(String, List<String>)>[
      ('python3', const <String>[]),
      ('python', const <String>[]),
    ];
  }

  /// Returns the first working interpreter, or null when none is installed.
  Future<DetectedPython?> detect() async {
    final runProbe = probe ?? _defaultProbe;
    for (final (executable, arguments) in candidates()) {
      final versionLine = await runProbe(
        executable,
        <String>[...arguments, '--version'],
      );
      if (versionLine != null) {
        return DetectedPython(executable, arguments, versionLine);
      }
    }
    return null;
  }
}
