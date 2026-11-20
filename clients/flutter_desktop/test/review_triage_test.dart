import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/models/review_queue.dart';
import 'package:scribeforge_desktop/models/review_triage.dart';
import 'package:scribeforge_desktop/screens/review/review_workspace_page.dart';
import 'package:scribeforge_desktop/widgets/workspace_shell.dart';

ReviewQueueItem item({
  required int page,
  required int pair,
  required double score,
  String? primary,
  String? secondary,
  String? crop,
  List<String> reasons = const [],
}) => ReviewQueueItem(
  projectId: 1,
  projectTitle: 'Book',
  pageIndex: page - 1,
  pairIndex: pair,
  score: score,
  reasons: reasons,
  furnitureReason: null,
  primaryText: primary,
  secondaryText: secondary,
  vlmText: null,
  vlmUncertain: false,
  cropPath: crop,
  sourceImagePath: '',
);

void main() {
  test('parses inclusive, single-page and open page ranges', () {
    expect(ReviewPageRange.parse(' '), isNull);
    expect(ReviewPageRange.parse('4')!.contains(4), isTrue);
    expect(ReviewPageRange.parse('4')!.contains(5), isFalse);
    expect(ReviewPageRange.parse('4–')!.contains(700), isTrue);
    expect(ReviewPageRange.parse('–12')!.contains(13), isFalse);
    expect(() => ReviewPageRange.parse('12–4'), throwsFormatException);
    expect(() => ReviewPageRange.parse('0'), throwsFormatException);
    expect(() => ReviewPageRange.parse('4–a'), throwsFormatException);
  });

  test('filters by evidence and keeps navigation in the displayed order', () {
    final queue = <ReviewQueueItem>[
      item(page: 9, pair: 0, score: 0.8, primary: 'Beta', crop: '/crop'),
      item(page: 2, pair: 0, score: 0.8, secondary: 'BETA', crop: '/crop'),
      item(page: 5, pair: 0, score: 1, primary: 'Other', crop: '/crop'),
      item(page: 1, pair: 0, score: 0.6, primary: 'beta'),
    ];
    final query = ReviewTriageQuery(
      text: 'beta',
      minimumRisk: 0.7,
      evidence: ReviewEvidenceFilter.crop,
      order: ReviewOrder.highestRisk,
      pages: ReviewPageRange.parse('2–10'),
      markedOnly: true,
    );
    final marks = <ReviewItemKey>{for (final row in queue) reviewItemKey(row)};
    final result = query.apply(queue, marks);
    expect(result.indexes, [1, 0]);
    expect(result.visibleIndex(2), 1);
    expect(result.move(1, 1), 0);
    expect(result.firstOnPage(9), 0);
    expect(result.firstOnPage(5), isNull);
    expect(result.markedCount, 4);
    expect(queue.map((row) => row.pageIndex), [8, 1, 4, 0]);
  });

  testWidgets('filter and browse never write a decision', (tester) async {
    tester.view.physicalSize = const Size(1000, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final focus = FocusNode();
    final controller = TextEditingController();
    addTearDown(focus.dispose);
    addTearDown(controller.dispose);
    final queue = [
      item(page: 1, pair: 0, score: 0.1, primary: 'One'),
      item(page: 2, pair: 0, score: 0.9, primary: 'Two'),
    ];
    var selected = 0;
    var decisions = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => ReviewWorkspacePage(
              storePath: '/store',
              projectId: 1,
              queue: queue,
              selectedIndex: selected,
              busy: false,
              loading: false,
              includePageFurniture: false,
              decisionFocusNode: focus,
              controllerFor: (_) => controller,
              onRecord: (_, _) async {
                decisions++;
              },
              onRefresh: () async {},
              onSelect: (index) => setState(() => selected = index),
              onIncludePageFurniture: (_) {},
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('review-triage-filters')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, 'Two');
    await tester.tap(find.text('应用筛选'));
    await tester.pumpAndSettle();
    expect(find.textContaining('显示 1 / 2 项'), findsOneWidget);
    expect(find.text('第 2 页 · 第 1 组'), findsOneWidget);
    await tester.tap(find.byTooltip('浏览当前队列'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('第 2 页 · 第 1 组').last);
    await tester.pumpAndSettle();
    expect(selected, 1);
    expect(decisions, 0);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('narrow shell switches pages without losing the active route', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(420, 440);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var index = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: StatefulBuilder(
          builder: (context, setState) => WorkspaceShell(
            destinations: const [
              WorkspaceDestination('一', Icons.looks_one),
              WorkspaceDestination('二', Icons.looks_two),
            ],
            selectedIndex: index,
            onSelect: (value) => setState(() => index = value),
            pages: const [Text('第一页'), Text('第二页')],
            notices: const [],
            actions: const [],
          ),
        ),
      ),
    );
    expect(find.byType(NavigationRail), findsNothing);
    await tester.tap(find.byKey(const ValueKey('workspace-destination-menu')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('二'));
    await tester.pumpAndSettle();
    expect(index, 1);
    expect(tester.takeException(), isNull);
  });
}
