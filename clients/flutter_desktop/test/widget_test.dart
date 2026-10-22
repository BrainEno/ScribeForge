import 'package:flutter_test/flutter_test.dart';
import 'package:scribeforge_desktop/main.dart';

void main() {
  testWidgets('desktop workspace starts with source and candidate actions', (tester) async {
    await tester.pumpWidget(const ScribeForgeDesktopApp());

    expect(find.text('ScribeForge'), findsOneWidget);
    expect(find.text('Open source PDF'), findsOneWidget);
    expect(find.text('Open Candidate A EPUB'), findsOneWidget);
    expect(find.text('Open Candidate B EPUB'), findsOneWidget);
    expect(find.text('Human evaluation'), findsOneWidget);
  });
}
