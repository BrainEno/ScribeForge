import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/backend_resolver.dart';

void main() {
  test('prefers the side-by-side backend over a developer PATH install', () async {
    final root = await Directory.systemTemp.createTemp(
      'scribeforge-backend-resolver-',
    );
    addTearDown(() => root.delete(recursive: true));
    final application = File(
      '${root.path}${Platform.pathSeparator}scribeforge_desktop',
    );
    final bundled = File(
      '${root.path}${Platform.pathSeparator}backend${Platform.pathSeparator}scribeforge',
    );
    await bundled.create(recursive: true);

    final resolution = await DesktopBackendResolver(
      applicationExecutablePath: application.path,
      operatingSystem: 'linux',
    ).resolve('scribeforge');

    expect(resolution.executable, bundled.path);
    expect(resolution.origin, DesktopBackendOrigin.bundled);
    expect(resolution.usesBundledBackend, isTrue);
  });

  test('uses the macOS app Resources backend when present', () async {
    final root = await Directory.systemTemp.createTemp(
      'scribeforge-macos-backend-',
    );
    addTearDown(() => root.delete(recursive: true));
    final contents = Directory(
      '${root.path}${Platform.pathSeparator}ScribeForge.app${Platform.pathSeparator}Contents',
    );
    final application = File(
      '${contents.path}${Platform.pathSeparator}MacOS${Platform.pathSeparator}ScribeForge',
    );
    final bundled = File(
      '${contents.path}${Platform.pathSeparator}Resources${Platform.pathSeparator}backend${Platform.pathSeparator}scribeforge',
    );
    await bundled.create(recursive: true);

    final resolution = await DesktopBackendResolver(
      applicationExecutablePath: application.path,
      operatingSystem: 'macos',
    ).resolve('scribeforge');

    expect(resolution.executable, bundled.path);
    expect(resolution.origin, DesktopBackendOrigin.bundled);
  });

  test(
    'an explicit developer override wins without being silently replaced',
    () async {
      final resolution = await DesktopBackendResolver(
        applicationExecutablePath: '/opt/ScribeForge/scribeforge_desktop',
        operatingSystem: 'linux',
        fileExists: (_) async => true,
      ).resolve('/work/venv/bin/scribeforge');

      expect(resolution.executable, '/work/venv/bin/scribeforge');
      expect(resolution.origin, DesktopBackendOrigin.custom);
      expect(resolution.usesBundledBackend, isFalse);
    },
  );

  test(
    'falls back to PATH only for development when no bundle exists',
    () async {
      final resolution = await DesktopBackendResolver(
        applicationExecutablePath: '/opt/ScribeForge/scribeforge_desktop',
        operatingSystem: 'linux',
        fileExists: (_) async => false,
      ).resolve('scribeforge');

      expect(resolution.executable, 'scribeforge');
      expect(resolution.origin, DesktopBackendOrigin.developmentPath);
      expect(resolution.usesBundledBackend, isFalse);
    },
  );

  test(
    'resolves the Windows executable name inside the backend folder',
    () async {
      String? inspectedPath;
      final resolution = await DesktopBackendResolver(
        applicationExecutablePath: r'C:\ScribeForge\scribeforge_desktop.exe',
        operatingSystem: 'windows',
        fileExists: (path) async {
          inspectedPath = path;
          return true;
        },
      ).resolve('scribeforge');

      expect(inspectedPath, endsWith(r'backend\scribeforge.exe'));
      expect(resolution.origin, DesktopBackendOrigin.bundled);
    },
  );
}
