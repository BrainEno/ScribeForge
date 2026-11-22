import 'dart:async';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';

typedef _OpenNative = Int32 Function(Pointer<Uint8>, Int32);
typedef _OpenDart = int Function(Pointer<Uint8>, int);
typedef _FlockNative = Int32 Function(Int32, Int32);
typedef _FlockDart = int Function(int, int);
typedef _CloseNative = Int32 Function(Int32);
typedef _CloseDart = int Function(int);
typedef _MallocNative = Pointer<Void> Function(IntPtr);
typedef _MallocDart = Pointer<Void> Function(int);
typedef _FreeNative = Void Function(Pointer<Void>);
typedef _FreeDart = void Function(Pointer<Void>);
typedef _ErrnoNative = Pointer<Int32> Function();
typedef _ErrnoDart = Pointer<Int32> Function();

/// `flock` is associated with an open file description, unlike POSIX fcntl
/// record locks, which are shared by all isolates in the same process.
/// The gate uses a different inode from the pre-existing settings.lock, so
/// opening and closing a competing gate descriptor cannot release that
/// process-scoped record lock. Both files must remain in place.
class SettingsPosixGate {
  SettingsPosixGate() : _libc = DynamicLibrary.process();

  final DynamicLibrary _libc;
  late final _OpenDart _open = _libc.lookupFunction<_OpenNative, _OpenDart>(
    'open',
  );
  late final _FlockDart _flock = _libc.lookupFunction<_FlockNative, _FlockDart>(
    'flock',
  );
  late final _CloseDart _close = _libc.lookupFunction<_CloseNative, _CloseDart>(
    'close',
  );
  late final _MallocDart _malloc = _libc
      .lookupFunction<_MallocNative, _MallocDart>('malloc');
  late final _FreeDart _free = _libc.lookupFunction<_FreeNative, _FreeDart>(
    'free',
  );
  late final _ErrnoDart _errno = _libc.lookupFunction<_ErrnoNative, _ErrnoDart>(
    Platform.isMacOS ? '__error' : '__errno_location',
  );

  Future<T> protect<T>(
    Directory directory,
    Stopwatch timer,
    Duration timeout,
    Duration retryInterval,
    Future<T> Function() task,
  ) async {
    final file = File(
      '${directory.path}${Platform.pathSeparator}settings.isolate.lock',
    );
    // Creating the file separately lets open use its non-variadic arguments
    // on both Linux and macOS, including macOS ARM64.
    await file.create();
    final readErrno = _errno;
    final path = utf8.encode(file.path);
    final memory = _malloc(path.length + 1).cast<Uint8>();
    if (memory == nullptr) throw StateError('无法为设置锁分配内存。');
    late final int descriptor;
    var openError = 0;
    try {
      memory.asTypedList(path.length + 1)
        ..setAll(0, path)
        ..[path.length] = 0;
      // O_RDONLY is zero; O_CLOEXEC prevents a child process from retaining
      // the gate after the application releases it.
      final closeOnExec = Platform.isMacOS ? 0x1000000 : 0x80000;
      descriptor = _open(memory, closeOnExec);
      if (descriptor < 0) openError = readErrno().value;
    } finally {
      _free(memory.cast<Void>());
    }
    if (descriptor < 0) {
      throw FileSystemException('无法打开设置隔离锁', file.path, OSError('', openError));
    }
    var acquired = false;
    try {
      while (true) {
        // LOCK_EX | LOCK_NB; no blocking FFI call can outlive a timeout.
        if (_flock(descriptor, 6) == 0) {
          acquired = true;
          break;
        }
        final code = readErrno().value;
        if (code != 4 && code != 11 && !(Platform.isMacOS && code == 35)) {
          throw FileSystemException('无法取得设置隔离锁', file.path, OSError('', code));
        }
        if (timer.elapsed >= timeout) {
          throw FileSystemException(
            '设置文件正被其他 ScribeForge 实例使用，请稍后重试。',
            file.path,
          );
        }
        final remaining = timeout - timer.elapsed;
        await Future<void>.delayed(
          remaining < retryInterval ? remaining : retryInterval,
        );
      }
      return await task();
    } finally {
      if (acquired) _flock(descriptor, 8); // LOCK_UN
      _close(descriptor);
    }
  }
}
