import 'package:flutter/material.dart';

/// Shared semantic palette for the ScribeForge desktop workbench.
abstract final class ScribePalette {
  static const Color forest = Color(0xFF23594B);
  static const Color mist = Color(0xFFE4EFE9);
  static const Color canvas = Color(0xFFF4F5F0);
  static const Color paper = Color(0xFFFFFFFF);
  static const Color ink = Color(0xFF202B27);
  static const Color mutedInk = Color(0xFF515F58);
  static const Color border = Color(0xFFDCE3DC);
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
  /// System CJK fonts tried in order, so Chinese UI text never renders as
  /// tofu on machines whose default font lacks CJK glyphs (#184).
  static const List<String> cjkFontFallback = <String>[
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    'PingFang SC',
    'Noto Sans CJK SC',
    'Noto Sans SC',
    'WenQuanYi Micro Hei',
    'sans-serif',
  ];

  static final ThemeData light = _buildLightTheme();

  static ThemeData _buildLightTheme() {
    final colorScheme =
        ColorScheme.fromSeed(
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
          fontSize: 26,
          height: 1.28,
          fontWeight: FontWeight.w700,
        ),
        titleLarge: TextStyle(
          fontSize: 20,
          height: 1.35,
          fontWeight: FontWeight.w600,
        ),
        titleMedium: TextStyle(
          fontSize: 16,
          height: 1.4,
          fontWeight: FontWeight.w600,
        ),
        titleSmall: TextStyle(
          fontSize: 14,
          height: 1.4,
          fontWeight: FontWeight.w600,
        ),
        bodyLarge: TextStyle(fontSize: 15, height: 1.5),
        bodyMedium: TextStyle(fontSize: 14, height: 1.5),
        bodySmall: TextStyle(
          fontSize: 13,
          height: 1.5,
          color: ScribePalette.mutedInk,
        ),
        labelLarge: TextStyle(
          fontSize: 14,
          height: 1.35,
          fontWeight: FontWeight.w600,
        ),
        labelMedium: TextStyle(
          fontSize: 13,
          height: 1.35,
          fontWeight: FontWeight.w500,
        ),
        labelSmall: TextStyle(
          fontSize: 12,
          height: 1.35,
          fontWeight: FontWeight.w500,
        ),
      ).apply(fontFamily: 'Segoe UI', fontFamilyFallback: cjkFontFallback),
      appBarTheme: const AppBarThemeData(
        backgroundColor: ScribePalette.paper,
        foregroundColor: ScribePalette.ink,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
      ),
      navigationRailTheme: const NavigationRailThemeData(
        backgroundColor: ScribePalette.paper,
        indicatorColor: ScribePalette.mist,
        selectedLabelTextStyle: TextStyle(
          color: ScribePalette.forest,
          fontWeight: FontWeight.w700,
        ),
        unselectedLabelTextStyle: TextStyle(color: ScribePalette.mutedInk),
        selectedIconTheme: IconThemeData(color: ScribePalette.forest, size: 21),
        unselectedIconTheme: IconThemeData(
          color: ScribePalette.mutedInk,
          size: 21,
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: ScribePalette.border,
        thickness: 1,
      ),
      dialogTheme: const DialogThemeData(
        backgroundColor: ScribePalette.paper,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(ScribeRadii.panel)),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          elevation: 0,
          minimumSize: const Size(44, 42),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(ScribeRadii.control),
          ),
        ),
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
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(ScribeRadii.control),
          borderSide: const BorderSide(color: ScribePalette.forest, width: 2),
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
