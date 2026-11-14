import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/python_locator.dart';

void main() {
  group('PythonLocator on linux/macOS', () {
    test('prefers python3 over python', () async {
      final locator = PythonLocator(
        operatingSystem: 'linux',
        probe: (executable, arguments) async {
          if (arguments.contains('--version')) {
            if (executable == 'python3') return 'Python 3.12.4';
            if (executable == 'python') return 'Python 3.11.9';
            return null;
          }
          // venv capability probe
          return '';
        },
      );

      final detected = await locator.detect();
      expect(detected, isNotNull);
      expect(detected!.executable, 'python3');
      expect(detected.arguments, isEmpty);
      expect(detected.label, 'python3 (Python 3.12.4)');
    });

    test('falls back to python when python3 is missing', () async {
      final locator = PythonLocator(
        operatingSystem: 'macos',
        probe: (executable, arguments) async {
          if (executable != 'python') return null;
          return arguments.contains('--version') ? 'Python 3.13.0' : '';
        },
      );

      final detected = await locator.detect();
      expect(detected, isNotNull);
      expect(detected!.executable, 'python');
    });

    test('returns null when no interpreter answers', () async {
      final locator = PythonLocator(
        operatingSystem: 'linux',
        probe: (executable, arguments) async => null,
      );

      expect(await locator.detect(), isNull);
    });

    test('skips an interpreter whose venv capability probe fails', () async {
      final locator = PythonLocator(
        operatingSystem: 'linux',
        probe: (executable, arguments) async {
          if (arguments.contains('--version')) return 'Python 3.12.4';
          return null; // no venv/ensurepip (e.g. missing python3-venv)
        },
      );

      expect(await locator.detect(), isNull);
    });

    test('skips interpreters printing only the Store pitch', () async {
      final locator = PythonLocator(
        operatingSystem: 'linux',
        probe: (executable, arguments) async {
          if (arguments.contains('--version')) {
            return 'Python was not found; install from the Microsoft Store';
          }
          return '';
        },
      );

      expect(await locator.detect(), isNull);
    });
  });

  group('PythonLocator on windows', () {
    test('probes the py launcher first with -3', () async {
      final seen = <String>[];
      final locator = PythonLocator(
        operatingSystem: 'windows',
        environment: const <String, String>{
          'LOCALAPPDATA': r'C:\Users\a\AppData\Local',
        },
        probe: (executable, arguments) async {
          seen.add('$executable ${arguments.join(' ')}');
          if (executable == 'py' && arguments.contains('-3')) {
            return arguments.contains('--version')
                ? 'Python 3.12.4'
                : '';
          }
          return null;
        },
      );

      final detected = await locator.detect();
      expect(detected, isNotNull);
      expect(detected!.executable, 'py');
      expect(detected.arguments, const <String>['-3']);
      expect(seen.first, 'py -3 --version');
    });

    test('rejects the Store alias stub even when --version exits 0', () async {
      final locator = PythonLocator(
        operatingSystem: 'windows',
        environment: const <String, String>{
          'LOCALAPPDATA': r'C:\Users\a\AppData\Local',
        },
        probe: (executable, arguments) async {
          if (arguments.contains('--version')) {
            return 'Python was not found; run without arguments to install '
                'from the Microsoft Store';
          }
          return '';
        },
      );

      expect(await locator.detect(), isNull);
    });

    test('falls through to versioned LOCALAPPDATA installs', () async {
      final locator = PythonLocator(
        operatingSystem: 'windows',
        environment: const <String, String>{
          'LOCALAPPDATA': r'C:\Users\a\AppData\Local',
        },
        probe: (executable, arguments) async {
          if (executable.endsWith(r'Python312\python.exe')) {
            return arguments.contains('--version') ? 'Python 3.12.4' : '';
          }
          return null;
        },
      );

      final detected = await locator.detect();
      expect(detected, isNotNull);
      expect(
        detected!.executable,
        r'C:\Users\a\AppData\Local\Programs\Python\Python312\python.exe',
      );
      expect(detected.arguments, isEmpty);
    });

    test('discovers Microsoft Store package installs of Python', () async {
      final locator = PythonLocator(
        operatingSystem: 'windows',
        environment: const <String, String>{
          'LOCALAPPDATA': r'C:\Users\a\AppData\Local',
        },
        listDirectory: (path) async {
          if (path == r'C:\Users\a\AppData\Local\Packages') {
            return <String>[
              r'C:\Users\a\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qz8...',
              r'C:\Users\a\AppData\Local\Packages\SomeOther.App_abc',
            ];
          }
          if (path.endsWith(r'LocalCache\local-packages')) {
            return <String>[
              r'C:\Users\a\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qz8...\LocalCache\local-packages\Python312',
            ];
          }
          return const <String>[];
        },
        probe: (executable, arguments) async {
          if (executable.endsWith(r'Python312\python.exe')) {
            return arguments.contains('--version') ? 'Python 3.12.11' : '';
          }
          return null;
        },
      );

      final detected = await locator.detect();
      expect(detected, isNotNull);
      expect(
        detected!.executable,
        r'C:\Users\a\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qz8...\LocalCache\local-packages\Python312\python.exe',
      );
    });
  });
}
