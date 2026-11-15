import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/cli_path_validator.dart';

FileStat _fileStat({
  FileSystemEntityType type = FileSystemEntityType.file,
  int permissions = 0o755,
}) =>
    _FakeStat(type: type, permissions: permissions);

class _FakeStat implements FileStat {
  _FakeStat({required this.type, required this.permissions});

  @override
  final FileSystemEntityType type;

  @override
  final int permissions;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  group('CliPathValidator', () {
    test('rejects an empty candidate with an actionable hint', () async {
      const validator = CliPathValidator();
      final result = await validator.probe('   ');
      expect(result.status, CliPathProbeStatus.empty);
      expect(result.ok, isFalse);
      expect(result.hint, isNotEmpty);
    });

    test('rejects a path that does not exist', () async {
      final validator = CliPathValidator(
        stat: (_) async => null,
        processRunner: (executable, arguments, {isCancelled}) async {
          throw StateError('probe must not run for a missing path');
        },
      );
      final result = await validator.probe('/no/such/scribeforge');
      expect(result.status, CliPathProbeStatus.notFound);
      expect(result.hint, contains('/no/such/scribeforge'));
    });

    test('rejects a directory', () async {
      final validator = CliPathValidator(
        stat: (_) async => _fileStat(type: FileSystemEntityType.directory),
        processRunner: (executable, arguments, {isCancelled}) async {
          throw StateError('probe must not run for a directory');
        },
      );
      final result = await validator.probe('/opt/scribeforge');
      expect(result.status, CliPathProbeStatus.isDirectory);
    });

    test('rejects a non-executable file on POSIX', () async {
      final validator = CliPathValidator(
        operatingSystem: 'linux',
        stat: (_) async => _fileStat(permissions: 0o644),
        processRunner: (executable, arguments, {isCancelled}) async {
          throw StateError('probe must not run for a non-executable file');
        },
      );
      final result = await validator.probe('/opt/scribeforge/bin/cli');
      expect(result.status, CliPathProbeStatus.notExecutable);
    });

    test('accepts a .exe file on Windows without a permission bit', () async {
      var ranWith = '';
      final validator = CliPathValidator(
        operatingSystem: 'windows',
        stat: (_) async => _fileStat(permissions: 0o644),
        processRunner: (executable, arguments, {isCancelled}) async {
          ranWith = '$executable ${arguments.join(' ')}';
          return const CliProcessOutcome(
            exitCode: 0,
            stdout: 'usage: scribeforge',
            stderr: '',
          );
        },
      );
      final result = await validator.probe(r'C:\ScribeForge\scribeforge.exe');
      expect(result.ok, isTrue);
      expect(result.exitCode, 0);
      expect(ranWith, contains('--help'));
      expect(result.hint, contains('已检测到'));
    });

    test('reports launchFailed when the process cannot start', () async {
      final validator = CliPathValidator(
        operatingSystem: 'linux',
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          throw ProcessException('no such file', executable);
        },
      );
      final result = await validator.probe('/opt/scribeforge/cli');
      expect(result.status, CliPathProbeStatus.launchFailed);
      expect(result.hint, contains('无法启动'));
    });

    test('reports launchFailed from an outcome launch error', () async {
      final validator = CliPathValidator(
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          return CliProcessOutcome.launchFailed('permission denied');
        },
      );
      final result = await validator.probe('/opt/scribeforge/cli');
      expect(result.status, CliPathProbeStatus.launchFailed);
      expect(result.output, contains('permission denied'));
    });

    test('rejects a binary whose probe exits non-zero', () async {
      final validator = CliPathValidator(
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          return const CliProcessOutcome(
            exitCode: 2,
            stdout: '',
            stderr: 'boom',
          );
        },
      );
      final result = await validator.probe('/opt/broken/cli');
      expect(result.status, CliPathProbeStatus.rejected);
      expect(result.exitCode, 2);
      expect(result.hint, contains('退出码为 2'));
    });

    test('reports timedOut probes', () async {
      final validator = CliPathValidator(
        timeout: const Duration(milliseconds: 1),
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          await Future<void>.delayed(const Duration(seconds: 5));
          return const CliProcessOutcome(exitCode: 0, stdout: '', stderr: '');
        },
      );
      final result = await validator.probe('/opt/slow/cli');
      expect(result.status, CliPathProbeStatus.timedOut);
    });

    test('honours cancellation before saving', () async {
      final validator = CliPathValidator(
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          return const CliProcessOutcome(
            exitCode: 0,
            stdout: '',
            stderr: '',
            cancelled: true,
          );
        },
      );
      final result = await validator.probe(
        '/opt/scribeforge/cli',
        isCancelled: () => true,
      );
      expect(result.status, CliPathProbeStatus.cancelled);
      expect(result.ok, isFalse);
    });

    test('supports a custom probe command such as python --version', () async {
      var probedArgs = '';
      final validator = CliPathValidator(
        operatingSystem: 'linux',
        probeArguments: const <String>['--version'],
        stat: (_) async => _fileStat(),
        processRunner: (executable, arguments, {isCancelled}) async {
          probedArgs = arguments.join(' ');
          return const CliProcessOutcome(
            exitCode: 0,
            stdout: 'Python 3.12.4',
            stderr: '',
          );
        },
      );
      final result = await validator.probe('/usr/bin/python3');
      expect(result.ok, isTrue);
      expect(probedArgs, '--version');
      expect(result.output, 'Python 3.12.4');
    });

    test('a bare command name skips the file checks and probes PATH', () async {
      final validator = CliPathValidator(
        stat: (_) async => throw StateError('bare names must not be stat-ed'),
        processRunner: (executable, arguments, {isCancelled}) async {
          expect(executable, 'scribeforge');
          return const CliProcessOutcome(
            exitCode: 0,
            stdout: 'usage: scribeforge',
            stderr: '',
          );
        },
      );
      final result = await validator.probe('scribeforge');
      expect(result.ok, isTrue);
    });
  });
}
