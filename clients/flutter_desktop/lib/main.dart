import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:pdfrx/pdfrx.dart';

import 'l10n/desktop_strings.dart';
import 'screens/unified_workspace.dart';
import 'theme/scribe_theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await pdfrxFlutterInitialize();
  runApp(const ScribeForgeDesktopApp());
}

class ScribeForgeDesktopApp extends StatelessWidget {
  const ScribeForgeDesktopApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'ScribeForge Desktop',
    debugShowCheckedModeBanner: false,
    theme: ScribeTheme.light,
    localizationsDelegates: const <LocalizationsDelegate<dynamic>>[
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
      DesktopStrings.delegate,
    ],
    supportedLocales: DesktopStrings.supportedLocales,
    localeResolutionCallback: (deviceLocale, supportedLocales) {
      final languageCode = deviceLocale?.languageCode;
      return supportedLocales.firstWhere(
        (locale) => locale.languageCode == languageCode,
        orElse: () => const Locale('zh'),
      );
    },
    home: const UnifiedWorkspace(),
  );
}
