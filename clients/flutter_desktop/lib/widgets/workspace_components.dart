import 'package:flutter/material.dart';

import '../theme/scribe_theme.dart';

enum ScribeStatusKind { info, success, warning, error }

/// Shared page heading: clear hierarchy, restrained icon badge, optional action.
class ScribePageHeader extends StatelessWidget {
  const ScribePageHeader({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    this.trailing,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) {
          final compact = constraints.maxWidth < 640;
          final heading = Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(ScribeRadii.card),
                ),
                alignment: Alignment.center,
                child: Icon(
                  icon,
                  color: Theme.of(context).colorScheme.primary,
                  size: 24,
                ),
              ),
              const SizedBox(width: ScribeSpacing.x4),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(title, style: Theme.of(context).textTheme.headlineSmall),
                    const SizedBox(height: ScribeSpacing.x1),
                    Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
              if (trailing != null && !compact) ...<Widget>[
                const SizedBox(width: ScribeSpacing.x4),
                trailing!,
              ],
            ],
          );
          if (trailing == null || !compact) return heading;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              heading,
              const SizedBox(height: ScribeSpacing.x3),
              Align(alignment: AlignmentDirectional.centerEnd, child: trailing),
            ],
          );
        },
      );
}

/// A calm, low-elevation grouping for one related task or decision.
class ScribeSectionCard extends StatelessWidget {
  const ScribeSectionCard({
    super.key,
    required this.icon,
    required this.title,
    required this.child,
    this.description,
    this.trailing,
  });

  final IconData icon;
  final String title;
  final String? description;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final colorScheme = Theme.of(context).colorScheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(ScribeSpacing.x5),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(icon, color: colorScheme.primary, size: 22),
                const SizedBox(width: ScribeSpacing.x3),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(title, style: textTheme.titleMedium),
                      if (description != null) ...<Widget>[
                        const SizedBox(height: ScribeSpacing.x1),
                        Text(description!, style: textTheme.bodySmall),
                      ],
                    ],
                  ),
                ),
                if (trailing != null) ...<Widget>[
                  const SizedBox(width: ScribeSpacing.x3),
                  trailing!,
                ],
              ],
            ),
            const SizedBox(height: ScribeSpacing.x5),
            child,
          ],
        ),
      ),
    );
  }
}

/// Persistent operation-level status with a semantic icon, label, and message.
class ScribeStatusBanner extends StatelessWidget {
  const ScribeStatusBanner({
    super.key,
    required this.kind,
    required this.title,
    required this.message,
    this.icon,
  });

  final ScribeStatusKind kind;
  final String title;
  final String message;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final (foreground, background, defaultIcon) = switch (kind) {
      ScribeStatusKind.info =>
        (ScribePalette.info, ScribePalette.infoSurface, Icons.info_outline),
      ScribeStatusKind.success =>
        (ScribePalette.success, ScribePalette.successSurface, Icons.check_circle_outline),
      ScribeStatusKind.warning =>
        (ScribePalette.amber, ScribePalette.amberSurface, Icons.warning_amber_outlined),
      ScribeStatusKind.error =>
        (colorScheme.error, colorScheme.errorContainer, Icons.error_outline),
    };
    return Semantics(
      liveRegion: true,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(ScribeSpacing.x4),
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(ScribeRadii.card),
          border: Border.all(color: foreground.withValues(alpha: 0.18)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(icon ?? defaultIcon, color: foreground, size: 22),
            const SizedBox(width: ScribeSpacing.x3),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    title,
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          color: foreground,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                  if (message.isNotEmpty) ...<Widget>[
                    const SizedBox(height: ScribeSpacing.x1),
                    Text(message, style: Theme.of(context).textTheme.bodySmall),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Explicit prerequisite/next-step presentation for an empty work surface.
class ScribeEmptyState extends StatelessWidget {
  const ScribeEmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                color: colorScheme.primaryContainer,
                borderRadius: BorderRadius.circular(ScribeRadii.panel),
              ),
              alignment: Alignment.center,
              child: Icon(icon, color: colorScheme.primary, size: 28),
            ),
            const SizedBox(height: ScribeSpacing.x4),
            Text(
              title,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: ScribeSpacing.x2),
            Text(
              message,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall,
            ),
            if (action != null) ...<Widget>[
              const SizedBox(height: ScribeSpacing.x4),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}
