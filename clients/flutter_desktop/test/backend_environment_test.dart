import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/backend_environment.dart';

void main() {
  group('BackendVenvPaths', () {
    test('uses Scripts on windows and bin elsewhere', () {
      const windows = BackendVenvPaths(
        r'C:\Users\alice\AppData\Roaming\ScribeForge\backend-venv',
        operatingSystem: 'windows',
      );
      expect(
        windows.pythonExecutable,
        r'C:\Users\alice\AppData\Roaming\ScribeForge\backend-venv\Scripts\python.exe',
      );
      expect(
        windows.cliExecutable,
        r'C:\Users\alice\AppData\Roaming\ScribeForge\backend-venv\Scripts\scribeforge.exe',
      );

      const linux = BackendVenvPaths(
        '/home/alice/.config/scribeforge/backend-venv',
        operatingSystem: 'linux',
      );
      expect(
        linux.pythonExecutable,
        '/home/alice/.config/scribeforge/backend-venv/bin/python',
      );
      expect(
        linux.cliExecutable,
        '/home/alice/.config/scribeforge/backend-venv/bin/scribeforge',
      );
    });
  });

  group('BackendEnvironmentProvisioner.detectBackendSourcePath', () {
    test(
      'walks up to the checkout whose pyproject declares scribeforge',
      () async {
        final files = <String, String>{
          '/repo/pyproject.toml': '[project]\nname = "scribeforge"\n',
          '/repo/clients/flutter_desktop/pyproject.toml': 'name = "other"',
        };
        final provisioner = BackendEnvironmentProvisioner(
          operatingSystem: 'linux',
          fileExists: (path) async => files.containsKey(path),
          readFile: (path) async => files[path] ?? '',
        );

        expect(
          await provisioner.detectBackendSourcePath(
            startDirectory: '/repo/clients/flutter_desktop',
          ),
          '/repo',
        );
      },
    );

    test('returns null when no scribeforge checkout is found', () async {
      final provisioner = BackendEnvironmentProvisioner(
        operatingSystem: 'linux',
        fileExists: (path) async => path == '/x/pyproject.toml',
        readFile: (path) async => 'name = "other"',
      );

      expect(
        await provisioner.detectBackendSourcePath(startDirectory: '/x/y'),
        isNull,
      );
    });
  });

  group('BackendEnvironmentProvisioner.provision', () {
    BackendEnvironmentProvisioner provisionerFor({
      required List<List<String>> calls,
      required Map<String, bool> existingFiles,
      Map<String, int> exitCodes = const {},
    }) {
      return BackendEnvironmentProvisioner(
        operatingSystem: 'linux',
        fileExists: (path) async => existingFiles[path] ?? false,
        runProcess: (executable, arguments, {onOutput}) async {
          calls.add(<String>[executable, ...arguments]);
          onOutput?.call('output of ${arguments.join(' ')}');
          final key = '$executable ${arguments.join(' ')}';
          return exitCodes[key] ?? 0;
        },
      );
    }

    test('rejects a directory without pyproject.toml', () async {
      final calls = <List<String>>[];
      final provisioner = provisionerFor(calls: calls, existingFiles: {});

      final result = await provisioner.provision(
        pythonExecutable: 'python3',
        backendSourcePath: '/not-a-repo',
        venvDirectory: '/settings/backend-venv',
      );

      expect(result.ok, isFalse);
      expect(result.error, contains('pyproject.toml'));
      expect(calls, isEmpty);
    });

    test(
      'creates the venv, installs editable, and smoke-checks the CLI',
      () async {
        final calls = <List<String>>[];
        final cli = '/settings/backend-venv/bin/scribeforge';
        final provisioner = provisionerFor(
          calls: calls,
          existingFiles: {'/repo/pyproject.toml': true, cli: true},
        );

        final result = await provisioner.provision(
          pythonExecutable: 'python3',
          backendSourcePath: '/repo',
          venvDirectory: '/settings/backend-venv',
        );

        expect(result.ok, isTrue);
        expect(result.cliExecutable, cli);
        expect(calls, <List<String>>[
          <String>['python3', '-m', 'venv', '/settings/backend-venv'],
          <String>[
            '/settings/backend-venv/bin/python',
            '-m',
            'pip',
            'install',
            '--quiet',
            '-e',
            '/repo',
          ],
          <String>[cli, '--help'],
        ]);
        expect(result.log, contains('后端环境就绪'));
      },
    );

    test('passes launcher arguments through to the venv command', () async {
      final calls = <List<String>>[];
      final cli = '/settings/backend-venv/bin/scribeforge';
      final provisioner = provisionerFor(
        calls: calls,
        existingFiles: {'/repo/pyproject.toml': true, cli: true},
      );

      final result = await provisioner.provision(
        pythonExecutable: 'py',
        pythonArgs: const <String>['-3'],
        backendSourcePath: '/repo',
        venvDirectory: '/settings/backend-venv',
      );

      expect(result.ok, isTrue);
      expect(
        calls.first,
        <String>['py', '-3', '-m', 'venv', '/settings/backend-venv'],
      );
    });

    test('fails closed when pip install exits non-zero', () async {
      final calls = <List<String>>[];
      final provisioner = provisionerFor(
        calls: calls,
        existingFiles: {'/repo/pyproject.toml': true},
        exitCodes: {
          '/settings/backend-venv/bin/python -m pip install --quiet -e /repo':
              1,
        },
      );

      final result = await provisioner.provision(
        pythonExecutable: 'python3',
        backendSourcePath: '/repo',
        venvDirectory: '/settings/backend-venv',
      );

      expect(result.ok, isFalse);
      expect(result.error, contains('退出码 1'));
      expect(calls.length, 2); // venv + pip, no smoke check
    });

    test(
      'fails closed when the CLI executable is missing after install',
      () async {
        final calls = <List<String>>[];
        final provisioner = provisionerFor(
          calls: calls,
          existingFiles: {'/repo/pyproject.toml': true},
        );

        final result = await provisioner.provision(
          pythonExecutable: 'python3',
          backendSourcePath: '/repo',
          venvDirectory: '/settings/backend-venv',
        );

        expect(result.ok, isFalse);
        expect(result.error, contains('未找到'));
      },
    );
  });
}
