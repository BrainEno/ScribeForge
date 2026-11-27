import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

/// Small first-stage desktop localization catalog (issue #223).
///
/// Keep this resource-only surface independent of app state so widgets and
/// tests can select a locale deterministically. Unlocalized feature screens
/// continue to use Chinese until their strings are migrated in follow-up work.
@immutable
class DesktopStrings {
  const DesktopStrings(this.locale);

  static const supportedLocales = <Locale>[Locale('zh'), Locale('en')];
  static const delegate = DesktopStringsDelegate();

  final Locale locale;
  bool get isEnglish => locale.languageCode == 'en';

  static DesktopStrings of(BuildContext context) =>
      Localizations.of<DesktopStrings>(context, DesktopStrings) ??
      const DesktopStrings(Locale('zh'));

  String get books => isEnglish ? 'Library' : '书籍';
  String get process => isEnglish ? 'Process' : '处理';
  String get review => isEnglish ? 'Review' : '复核';
  String get candidates => isEnglish ? 'Candidates' : '候选比较';
  String get export => isEnglish ? 'Export' : '导出';
  String get safety => isEnglish ? 'Project safety' : '项目安全';
  String get settings => isEnglish ? 'Settings' : '设置';
  String get helpTooltip => isEnglish ? 'Help' : '使用帮助';
  String get closeErrorTooltip => isEnglish ? 'Dismiss error' : '关闭错误提示';
  String get compactNavigationTooltip =>
      isEnglish ? 'Switch workspace page' : '切换工作台页面';
  String get nextStepPrefix => isEnglish ? 'Next: ' : '下一步：';

  String actionButton(String action) {
    if (!isEnglish) {
      return switch (action) {
        'configureBackend' => '去设置',
        'initEnvironment' => '下载并配置',
        'importBook' => '去导入',
        'resumeLastProject' => '继续上次项目',
        'relinkSource' => '去关联源文件',
        'goProcess' => '去处理',
        'goReview' => '去复核',
        'goExport' => '去导出',
        _ => '',
      };
    }
    return switch (action) {
      'configureBackend' => 'Settings',
      'initEnvironment' => 'Set up OCR',
      'importBook' => 'Import book',
      'resumeLastProject' => 'Resume project',
      'relinkSource' => 'Relink source',
      'goProcess' => 'Process pages',
      'goReview' => 'Review queue',
      'goExport' => 'Export book',
      _ => '',
    };
  }
}

class DesktopStringsDelegate extends LocalizationsDelegate<DesktopStrings> {
  const DesktopStringsDelegate();

  @override
  bool isSupported(Locale locale) => DesktopStrings.supportedLocales.any(
    (item) => item.languageCode == locale.languageCode,
  );

  @override
  Future<DesktopStrings> load(Locale locale) =>
      SynchronousFuture<DesktopStrings>(
        DesktopStrings(
          DesktopStrings.supportedLocales.firstWhere(
            (item) => item.languageCode == locale.languageCode,
            orElse: () => const Locale('zh'),
          ),
        ),
      );

  @override
  bool shouldReload(DesktopStringsDelegate old) => false;
}
