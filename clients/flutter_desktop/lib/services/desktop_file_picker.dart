import 'package:file_picker/file_picker.dart';

/// Small file-dialog boundary so desktop workflows can be tested without
/// platform channels and cancelled selections can be exercised deterministically.
abstract interface class DesktopFilePicker {
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  });

  Future<String?> getDirectoryPath({String? dialogTitle});
}

class PluginDesktopFilePicker implements DesktopFilePicker {
  const PluginDesktopFilePicker();

  @override
  Future<String?> pickFile({
    String? dialogTitle,
    required List<String> allowedExtensions,
  }) async {
    final result = await FilePicker.pickFile(
      dialogTitle: dialogTitle,
      type: FileType.custom,
      allowedExtensions: allowedExtensions,
    );
    return result?.path;
  }

  @override
  Future<String?> getDirectoryPath({String? dialogTitle}) =>
      FilePicker.getDirectoryPath(dialogTitle: dialogTitle);
}
