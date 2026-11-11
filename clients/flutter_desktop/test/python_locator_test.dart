import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/python_locator.dart';

void main() {
  group('PythonLocator on linux/macOS', () {
    test('prefers python3 over python', () async {
      final locator = PythonLocator(
        operatingSystem: 'linux',
        probe: (executable, arguments) async {
          if (executable == 'python3' && arguments.last == '--version') {
            return 'Python 3.12.4';
          }
          if (executable == 'python') return 'Python 3.11.9';
          return null;
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
        probe: (executable, arguments) async =>
            executable == 'python' ? 'Python 3.13.0' : null,
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
            return 'Python 3.12.4';
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

    test('falls through to versioned LOCALAPPDATA installs', () async {
      final locator = PythonLocator(
        operatingSystem: 'windows',
        environment: const <String, String>{
          'LOCALAPPDATA': r'C:\Users\a\AppData\Local',
        },
        probe: (executable, arguments) async {
          if (executable.endsWith(r'Python312\python.exe')) {
            return 'Python 3.12.4';
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
  });
}
