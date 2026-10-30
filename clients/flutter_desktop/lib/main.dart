import 'package:flutter/material.dart';
import 'package:pdfrx/pdfrx.dart';

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
        home: const UnifiedWorkspace(),
      );
}
