import 'dart:io';

/// Small file-system boundary used by the desktop workspace.
///
/// The UI only needs file/directory existence checks and a source-file
/// fingerprint. Keeping these operations replaceable makes widget workflows
/// deterministic in tests without tying them to the host file system.
abstract interface class WorkspaceFileSystem {
  Future<bool> fileExists(String path);

  Future<bool> directoryExists(String path);

  Future<WorkspaceFileMetadata?> stat(String path);

  Stream<List<int>> openRead(String path);
}

class WorkspaceFileMetadata {
  const WorkspaceFileMetadata({
    required this.size,
    required this.modifiedMilliseconds,
  });

  final int size;
  final int modifiedMilliseconds;
}

class IoWorkspaceFileSystem implements WorkspaceFileSystem {
  const IoWorkspaceFileSystem();

  @override
  Future<bool> fileExists(String path) => File(path).exists();

  @override
  Future<bool> directoryExists(String path) => Directory(path).exists();

  @override
  Future<WorkspaceFileMetadata?> stat(String path) async {
    final stat = await File(path).stat();
    if (stat.type != FileSystemEntityType.file) return null;
    return WorkspaceFileMetadata(
      size: stat.size,
      modifiedMilliseconds: stat.modified.millisecondsSinceEpoch,
    );
  }

  @override
  Stream<List<int>> openRead(String path) => File(path).openRead();
}
