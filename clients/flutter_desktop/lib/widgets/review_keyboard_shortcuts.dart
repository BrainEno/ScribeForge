import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class ReviewKeyboardShortcuts extends StatelessWidget {
  const ReviewKeyboardShortcuts({
    required this.onPreviousPage,
    required this.onNextPage,
    required this.onNextDifference,
    required this.child,
    this.onFocusDecisionInput,
    this.onFocusPageJump,
    super.key,
  });

  final VoidCallback onPreviousPage;
  final VoidCallback onNextPage;
  final VoidCallback onNextDifference;
  final VoidCallback? onFocusDecisionInput;
  final VoidCallback? onFocusPageJump;
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
          SingleActivator(LogicalKeyboardKey.keyJ, control: true):
              _FocusDecisionInputIntent(),
          SingleActivator(LogicalKeyboardKey.keyG, control: true):
              _FocusPageJumpIntent(),
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
            _FocusDecisionInputIntent:
                CallbackAction<_FocusDecisionInputIntent>(
              onInvoke: (_) {
                onFocusDecisionInput?.call();
                return null;
              },
            ),
            _FocusPageJumpIntent: CallbackAction<_FocusPageJumpIntent>(
              onInvoke: (_) {
                onFocusPageJump?.call();
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

class _FocusDecisionInputIntent extends Intent {
  const _FocusDecisionInputIntent();
}

class _FocusPageJumpIntent extends Intent {
  const _FocusPageJumpIntent();
}
