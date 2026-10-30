import 'package:flutter/material.dart';

/// Shared semantic palette for the ScribeForge desktop workbench.
abstract final class ScribePalette {
  static const Color forest = Color(0xFF236B5D);
  static const Color mist = Color(0xFFE8F2EE);
  static const Color canvas = Color(0xFFF3F5F4);
  static const Color paper = Color(0xFFFFFFFF);
  static const Color ink = Color(0xFF19332D);
  static const Color mutedInk = Color(0xFF63736D);
  static const Color border = Color(0xFFE2E7E4);
  static const Color success = Color(0xFF28734D);
  static const Color successSurface = Color(0xFFEAF5ED);
  static const Color amber = Color(0xFF9B5C08);
  static const Color amberSurface = Color(0xFFFFF4DF);
  static const Color info = Color(0xFF315F8C);
  static const Color infoSurface = Color(0xFFEAF2FA);
}

/// Four-point spacing rhythm for workspace layout and shared components.
abstract final class ScribeSpacing {
  static const double x1 = 4;
  static const double x2 = 8;
  static const double x3 = 12;
  static const double x4 = 16;
  static const double x5 = 20;
  static const double x6 = 24;
  static const double x8 = 32;
  static const double x10 = 40;
}

/// Semantic corner radii used by controls, cards, and grouped panels.
abstract final class ScribeRadii {
  static const double control = 8;
  static const double card = 12;
  static const double panel = 16;
}

/// Material 3 theme shared by all desktop screens and dialogs.
abstract final class ScribeTheme {
  static final ThemeData light = _buildLightTheme();

  static ThemeData _buildLightTheme() {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: ScribePalette.forest,
      brightness: Brightness.light,
      surface: ScribePalette.paper,
    ).copyWith(
      primary: ScribePalette.forest,
      onPrimary: ScribePalette.paper,
      primaryContainer: ScribePalette.mist,
      onPrimaryContainer: ScribePalette.ink,
      surface: ScribePalette.paper,
      onSurface: ScribePalette.ink,
      onSurfaceVariant: ScribePalette.mutedInk,
      outline: ScribePalette.border,
      error: const Color(0xFFB3261E),
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: ScribePalette.canvas,
      dividerColor: ScribePalette.border,
      textTheme: const TextTheme(
        headlineSmall: TextStyle(
          color: ScribePalette.ink,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.3,
        ),
        titleMedium: TextStyle(
          color: ScribePalette.ink,
          fontWeight: FontWeight.w600,
        ),
        bodyMedium: TextStyle(color: ScribePalette.ink, height: 1.4),
        bodySmall: TextStyle(color: ScribePalette.mutedInk, height: 1.4),
        labelSmall: TextStyle(color: ScribePalette.mutedInk),
      ),
      cardTheme: const CardThemeData(
        color: ScribePalette.paper,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(ScribeRadii.card)),
          side: BorderSide(color: ScribePalette.border),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: ScribePalette.paper,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(ScribeRadii.control),
          borderSide: const BorderSide(color: ScribePalette.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(ScribeRadii.control),
          borderSide: const BorderSide(color: ScribePalette.border),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: ScribeSpacing.x4,
          vertical: ScribeSpacing.x3,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(44, 44),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(ScribeRadii.control),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(44, 44),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(ScribeRadii.control),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(44, 44),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(ScribeRadii.control),
          ),
        ),
      ),
    );
  }
}
