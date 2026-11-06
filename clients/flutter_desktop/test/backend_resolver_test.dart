import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/services/backend_resolver.dart';

void main() {
  test('prefers the side-by-side backend over a developer PATH install', () async {
    const bundled = '/opt/ScribeForge/backend/scribeforge';
    final resolution = await DesktopBackendResolver(
      applicationExecutablePath: '/opt/ScribeForge/scribeforge_desktop',
      operatingSystem: 'linux',
      fileExists: (path) async => path == bundled,
    ).resolve('scribeforge');

    expect(resolution.executable, bundled);
    expect(resolution.origin, DesktopBackendOrigin.bundled);
    expect(resolution.usesBundledBackend, isTrue);
  });

  test('uses the macOS app Resources backend when present', () async {
    const bundled =
        '/Applications/ScribeForge.app/Contents/Resources/backend/scribeforge';
    final resolution = await DesktopBackendResolver(
      applicationExecutablePath:
          '/Applications/ScribeForge.app/Contents/MacOS/ScribeForge',
      operatingSystem: 'macos',
      fileExists: (path) async => path == bundled,
    ).resolve('scribeforge');

    expect(resolution.executable, bundled);
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
