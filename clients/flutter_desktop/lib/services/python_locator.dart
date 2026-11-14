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

typedef PythonProbe = Future<String?> Function(
  String executable,
  List<String> arguments,
);

/// Lists the entries of a directory (full paths); injectable for tests.
typedef DirectoryLister = Future<List<String>> Function(String path);

/// Output produced by the Windows "App execution alias" stub when Python is
/// not really installed; such an interpreter exits 9009 on real work even
/// when `--version` appears to succeed.
const String storeAliasMarker = 'Microsoft Store';

/// Probe verifying that an interpreter can actually create virtual
/// environments (``venv`` + ``ensurepip`` must be importable).
const String venvCapabilityCheck = 'import venv, ensurepip';

/// Finds a usable Python 3 interpreter on the user's machine.
///
/// Desktop users must not have to type a path: the locator probes the usual
/// launchers in a deterministic order and returns the first one that answers
/// `--version` *and* the venv-capability check. Two Windows traps are
/// explicitly rejected:
///
/// 1. the Microsoft Store app-execution alias (`python3`/`python` stubs that
///    print a Store pitch and exit 9009 on real work);
/// 2. interpreters without `venv`/`ensurepip` (e.g. Debian's bare
///    `python3` without `python3-venv`).
///
/// Every probe and directory listing is injectable so tests never spawn real
/// processes.
class PythonLocator {
  const PythonLocator({
    this.probe,
    this.operatingSystem,
    this.environment,
    this.listDirectory,
  });

  final PythonProbe? probe;
  final String? operatingSystem;
  final Map<String, String>? environment;
  final DirectoryLister? listDirectory;

  String get _os => operatingSystem ?? Platform.operatingSystem;

  static Future<String?> _defaultProbe(
    String executable,
    List<String> arguments,
  ) async {
    try {
      final result = await Process.run(executable, arguments);
      if (result.exitCode != 0) return null;
      final output = '${result.stdout}${result.stderr}'.trim();
      if (output.isEmpty) return null;
      if (output.contains(storeAliasMarker)) return null;
      return output.split('\n').first;
    } on Object {
      // ProcessException (not found) and anything else: just try the next one.
      return null;
    }
  }

  static Future<List<String>> _defaultListDirectory(String path) async {
    try {
      final dir = Directory(path);
      if (!dir.existsSync()) return const <String>[];
      return await dir
          .list()
          .map((entity) => entity.path)
          .toList();
    } on Object {
      return const <String>[];
    }
  }

  /// Candidate (executable, launcher arguments) pairs from PATH and the
  /// well-known per-user install locations, best first.
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

  /// Microsoft Store package installs of Python live under
  /// `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.*\...`.
  Future<List<(String, List<String>)>> _storePackageCandidates() async {
    if (_os != 'windows') return const <(String, List<String>)>[];
    final localAppData = environment?['LOCALAPPDATA'] ?? '';
    if (localAppData.isEmpty) return const <(String, List<String>)>[];
    final lister = listDirectory ?? _defaultListDirectory;
    final packages = await lister('$localAppData\\Packages');
    final result = <(String, List<String>)>[];
    for (final package in packages) {
      final name = package.split(RegExp(r'[\\/]')).last;
      if (!name.startsWith('PythonSoftwareFoundation.Python.')) continue;
      final localPackages =
          await lister('$package\\LocalCache\\local-packages');
      for (final versionDir in localPackages) {
        final versionName = versionDir.split(RegExp(r'[\\/]')).last;
        if (!versionName.startsWith('Python3')) continue;
        result.add((
          '$versionDir\\python.exe',
          const <String>[],
        ));
      }
    }
    return result;
  }

  /// Returns the first working, venv-capable interpreter, or null when none
  /// is installed.
  Future<DetectedPython?> detect() async {
    final runProbe = probe ?? _defaultProbe;
    final all = <(String, List<String>)>[
      ...candidates(),
      ...await _storePackageCandidates(),
    ];
    for (final (executable, arguments) in all) {
      final versionLine = await runProbe(executable, <String>[
        ...arguments,
        '--version',
      ]);
      if (versionLine == null) continue;
      if (versionLine.contains(storeAliasMarker)) continue;
      // The interpreter must be able to create virtual environments; a bare
      // alias stub or a venv-less system Python would fail later with a
      // confusing exit code (9009 on Windows).
      final capability = await runProbe(executable, <String>[
        ...arguments,
        '-c',
        venvCapabilityCheck,
      ]);
      if (capability == null) continue;
      return DetectedPython(executable, arguments, versionLine);
    }
    return null;
  }
}
