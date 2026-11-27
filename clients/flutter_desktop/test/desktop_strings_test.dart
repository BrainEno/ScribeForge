import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/l10n/desktop_strings.dart';

void main() {
  group('DesktopStrings', () {
    test('ships Chinese shell strings as the default locale', () {
      const strings = DesktopStrings(Locale('zh'));

      expect(strings.books, '书籍');
      expect(strings.settings, '设置');
      expect(strings.helpTooltip, '使用帮助');
      expect(strings.actionButton('goReview'), '去复核');
    });

    test('provides English shell labels and actions', () {
      const strings = DesktopStrings(Locale('en'));

      expect(strings.books, 'Library');
      expect(strings.process, 'Process');
      expect(strings.review, 'Review');
      expect(strings.candidates, 'Candidates');
      expect(strings.export, 'Export');
      expect(strings.safety, 'Project safety');
      expect(strings.settings, 'Settings');
      expect(strings.helpTooltip, 'Help');
      expect(strings.compactNavigationTooltip, 'Switch workspace page');
      expect(strings.nextStepPrefix, 'Next: ');
      expect(strings.actionButton('goReview'), 'Review queue');
      expect(strings.actionButton('unrecognized'), '');
    });

    test('delegate only supports the declared languages', () {
      const delegate = DesktopStringsDelegate();

      expect(delegate.isSupported(const Locale('en', 'GB')), isTrue);
      expect(delegate.isSupported(const Locale('zh', 'TW')), isTrue);
      expect(delegate.isSupported(const Locale('ja')), isFalse);
    });
  });
}
