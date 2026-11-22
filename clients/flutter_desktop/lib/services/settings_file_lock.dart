import 'dart:async';
import 'dart:io';

import 'settings_posix_gate.dart';

/// A cooperative OS lock for a settings directory. The lock file is permanent:
/// deleting or replacing it while another process holds it breaks exclusion.
/// Kernel locks are released automatically when a process terminates.
class SettingsFileLock {
  const SettingsFileLock({
    this.timeout = const Duration(seconds: 5),
    this.retryInterval = const Duration(milliseconds: 25),
  });

  final Duration timeout;
  final Duration retryInterval;

  Future<T> protect<T>(Directory directory, Future<T> Function() task) async {
    final timer = Stopwatch()..start();
    if (Platform.isLinux || Platform.isMacOS) {
      return SettingsPosixGate().protect(
        directory,
        timer,
        timeout,
        retryInterval,
        () => _protectFile(directory, timer, task),
      );
    }
    return _protectFile(directory, timer, task);
  }

  Future<T> _protectFile<T>(
    Directory directory,
    Stopwatch timer,
    Future<T> Function() task,
  ) async {
    final lockFile = File(
      '${directory.path}${Platform.pathSeparator}settings.lock',
    );
    final handle = await lockFile.open(mode: FileMode.append);
    var acquired = false;
    try {
      while (true) {
        try {
          // Nonblocking is essential: Future.timeout on a blocking lock can
          // leave a pending acquisition alive after the caller has given up.
          await handle.lock(FileLock.exclusive);
          acquired = true;
          break;
        } on FileSystemException catch (error) {
          if (!_isContention(error)) rethrow;
          if (timer.elapsed >= timeout) {
            throw FileSystemException(
              '设置文件正被其他 ScribeForge 实例使用，请稍后重试。',
              lockFile.path,
              error.osError,
            );
          }
          final remaining = timeout - timer.elapsed;
          await Future<void>.delayed(
            remaining < retryInterval ? remaining : retryInterval,
          );
        }
      }
      return await task();
    } finally {
      if (acquired) {
        try {
          await handle.unlock();
        } finally {
          await handle.close();
        }
      } else {
        await handle.close();
      }
    }
  }

  bool _isContention(FileSystemException error) {
    final code = error.osError?.errorCode;
    if (Platform.isWindows) return code == 32 || code == 33;
    // EACCES or EAGAIN from fcntl indicate a lock held by another process.
    return code == 11 || code == 13 || (Platform.isMacOS && code == 35);
  }
}
