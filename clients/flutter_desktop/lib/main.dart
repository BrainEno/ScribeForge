import 'package:flutter/material.dart';
import 'package:pdfrx/pdfrx.dart';

import 'screens/unified_workspace.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await pdfrxFlutterInitialize();
  runApp(const ScribeForgeDesktopApp());
}

class ScribeForgeDesktopApp extends StatelessWidget {
  const ScribeForgeDesktopApp({super.key});

  @override
  Widget build(BuildContext context) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF236B5D),
      brightness: Brightness.light,
      surface: const Color(0xFFFFFFFF),
    );
    return MaterialApp(
      title: 'ScribeForge Desktop',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: colorScheme,
        scaffoldBackgroundColor: const Color(0xFFF3F5F4),
        dividerColor: const Color(0xFFE2E7E4),
        cardTheme: const CardThemeData(
          color: Colors.white,
          elevation: 0,
          margin: EdgeInsets.zero,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(12)),
            side: BorderSide(color: Color(0xFFE2E7E4)),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: Color(0xFFD8E0DC)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: Color(0xFFD8E0DC)),
          ),
          contentPadding: const EdgeInsets.symmetric(horizontal: 11, vertical: 10),
        ),
      ),
      home: const UnifiedWorkspace(),
    );
  }
}
