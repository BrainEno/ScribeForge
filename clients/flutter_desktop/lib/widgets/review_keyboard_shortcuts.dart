import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class ReviewKeyboardShortcuts extends StatelessWidget {
  const ReviewKeyboardShortcuts({
    required this.onPreviousPage,
    required this.onNextPage,
    required this.onNextDifference,
    required this.child,
    super.key,
  });

  final VoidCallback onPreviousPage;
  final VoidCallback onNextPage;
  final VoidCallback onNextDifference;
  final Widget child;

  @override
  Widget build(BuildContext context) => Shortcuts(
        shortcuts: const <ShortcutActivator, Intent>{
          SingleActivator(LogicalKeyboardKey.arrowLeft, alt: true):
              _PreviousPageIntent(),
          SingleActivator(LogicalKeyboardKey.arrowRight, alt: true):
              _NextPageIntent(),
          SingleActivator(LogicalKeyboardKey.keyD, alt: true):
              _NextDifferenceIntent(),
        },
        child: Actions(
          actions: <Type, Action<Intent>>{
            _PreviousPageIntent: CallbackAction<_PreviousPageIntent>(
              onInvoke: (_) {
                onPreviousPage();
                return null;
              },
            ),
            _NextPageIntent: CallbackAction<_NextPageIntent>(
              onInvoke: (_) {
                onNextPage();
                return null;
              },
            ),
            _NextDifferenceIntent: CallbackAction<_NextDifferenceIntent>(
              onInvoke: (_) {
                onNextDifference();
                return null;
              },
            ),
          },
          child: child,
        ),
      );
}

class _PreviousPageIntent extends Intent {
  const _PreviousPageIntent();
}

class _NextPageIntent extends Intent {
  const _NextPageIntent();
}

class _NextDifferenceIntent extends Intent {
  const _NextDifferenceIntent();
}
