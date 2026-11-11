import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/screens/unified_workspace.dart';

void main() {
  group('nextStepGuidance', () {
    NextStepGuidance guidance({
      bool backendChecked = true,
      bool backendLaunchFailed = false,
      bool runtimeSetupRequired = false,
      bool hasProject = false,
      bool hasSource = false,
      bool sourceMismatch = false,
      int pagesWithBothOcr = 0,
      int pagesTotal = 0,
      int reviewQueued = 0,
    }) {
      return nextStepGuidance(
        backendChecked: backendChecked,
        backendLaunchFailed: backendLaunchFailed,
        runtimeSetupRequired: runtimeSetupRequired,
        hasProject: hasProject,
        hasSource: hasSource,
        sourceMismatch: sourceMismatch,
        pagesWithBothOcr: pagesWithBothOcr,
        pagesTotal: pagesTotal,
        reviewQueued: reviewQueued,
      );
    }

    test('backend launch failure points at the settings page', () {
      final step = guidance(backendLaunchFailed: true);
      expect(step.action, NextStepAction.configureBackend);
      expect(step.message, contains('设置'));
    });

    test('runtime setup required offers one-click initialization first', () {
      final step = guidance(runtimeSetupRequired: true);
      expect(step.action, NextStepAction.initEnvironment);
      expect(step.message, contains('初始化'));
    });

    test('setup requirement wins over a missing book', () {
      final step = guidance(runtimeSetupRequired: true, hasProject: false);
      expect(step.action, NextStepAction.initEnvironment);
    });

    test('no project and no source asks for a book import', () {
      final step = guidance();
      expect(step.action, NextStepAction.importBook);
      expect(step.message, contains('导入'));
    });

    test('a selected source without a project goes to processing', () {
      final step = guidance(hasSource: true);
      expect(step.action, NextStepAction.goProcess);
    });

    test('source mismatch asks to relink before anything else', () {
      final step = guidance(
        hasProject: true,
        sourceMismatch: true,
        pagesWithBothOcr: 3,
        pagesTotal: 3,
      );
      expect(step.action, NextStepAction.relinkSource);
    });

    test('unfinished OCR pages go to the processing page', () {
      final step = guidance(
        hasProject: true,
        pagesWithBothOcr: 2,
        pagesTotal: 5,
        reviewQueued: 1,
      );
      expect(step.action, NextStepAction.goProcess);
    });

    test('finished OCR with a queued review goes to the review page', () {
      final step = guidance(
        hasProject: true,
        pagesWithBothOcr: 5,
        pagesTotal: 5,
        reviewQueued: 3,
      );
      expect(step.action, NextStepAction.goReview);
      expect(step.count, 3);
      expect(step.message, contains('3'));
    });

    test('everything done points at export', () {
      final step = guidance(
        hasProject: true,
        pagesWithBothOcr: 5,
        pagesTotal: 5,
        reviewQueued: 0,
      );
      expect(step.action, NextStepAction.goExport);
      expect(step.message, contains('导出'));
    });

    test('a zero-page project goes straight to export, not processing', () {
      final step = guidance(hasProject: true, pagesTotal: 0);
      expect(step.action, NextStepAction.goExport);
    });

    test('nothing to say while the backend status is not checked yet', () {
      final step = guidance(backendChecked: false);
      expect(step.action, NextStepAction.none);
    });
  });
}
