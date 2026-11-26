import 'package:flutter/material.dart';

import '../l10n/desktop_strings.dart';
import '../theme/scribe_theme.dart';

class WorkspaceDestination {
  const WorkspaceDestination(this.label, this.icon);

  final String label;
  final IconData icon;
}

/// Changes the navigation control without rebuilding the page branch. A resize
/// therefore doesn't reset a project's forms, scroll positions, or evidence.
class WorkspaceShell extends StatelessWidget {
  const WorkspaceShell({
    super.key,
    required this.destinations,
    required this.selectedIndex,
    required this.onSelect,
    required this.pages,
    required this.notices,
    required this.actions,
  }) : assert(destinations.length == pages.length);

  final List<WorkspaceDestination> destinations;
  final int selectedIndex;
  final ValueChanged<int> onSelect;
  final List<Widget> pages;
  final List<Widget> notices;
  final List<Widget> actions;

  static bool useCompactNavigation(Size size) =>
      size.width < 700 || size.height < 580;

  List<PopupMenuEntry<int>> _compactItems() => <PopupMenuEntry<int>>[
    for (var index = 0; index < destinations.length; index++)
      CheckedPopupMenuItem<int>(
        value: index,
        checked: index == selectedIndex,
        child: Text(destinations[index].label),
      ),
  ];

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final compact = useCompactNavigation(size);
    final extended = !compact && size.width >= 1040;
    final selected = destinations[selectedIndex];
    final theme = Theme.of(context);
    final strings = DesktopStrings.of(context);
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 56,
        titleSpacing: compact ? 0 : ScribeSpacing.x5,
        leading: compact
            ? PopupMenuButton<int>(
                key: const ValueKey('workspace-destination-menu'),
                icon: const Icon(Icons.menu),
                tooltip: strings.compactNavigationTooltip,
                initialValue: selectedIndex,
                onSelected: onSelect,
                itemBuilder: (_) => _compactItems(),
              )
            : null,
        title: Row(
          children: <Widget>[
            const Flexible(child: _WorkspaceBrand()),
            if (size.width >= 480) ...<Widget>[
              const SizedBox(width: ScribeSpacing.x4),
              const SizedBox(height: 18, child: VerticalDivider(width: 1)),
              const SizedBox(width: ScribeSpacing.x4),
              Flexible(
                child: Text(
                  selected.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleSmall,
                ),
              ),
            ],
          ],
        ),
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1),
        ),
        actions: actions,
      ),
      body: Row(
        children: <Widget>[
          if (!compact)
            NavigationRail(
              minWidth: 80,
              minExtendedWidth: 176,
              extended: extended,
              selectedIndex: selectedIndex,
              onDestinationSelected: onSelect,
              labelType: extended
                  ? NavigationRailLabelType.none
                  : NavigationRailLabelType.all,
              useIndicator: true,
              scrollable: true,
              leading: extended ? const _NavigationHeading() : null,
              destinations: <NavigationRailDestination>[
                for (final destination in destinations)
                  NavigationRailDestination(
                    icon: Icon(destination.icon),
                    label: Text(destination.label),
                  ),
              ],
            )
          else
            const SizedBox.shrink(),
          if (!compact)
            const VerticalDivider(width: 1)
          else
            const SizedBox.shrink(),
          Expanded(
            key: const ValueKey('workspace-page-container'),
            child: Column(
              children: <Widget>[
                ...notices,
                Expanded(
                  child: IndexedStack(index: selectedIndex, children: pages),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _WorkspaceBrand extends StatelessWidget {
  const _WorkspaceBrand();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        DecoratedBox(
          decoration: BoxDecoration(
            color: theme.colorScheme.primary,
            borderRadius: BorderRadius.circular(ScribeRadii.control),
          ),
          child: Padding(
            padding: const EdgeInsets.all(ScribeSpacing.x2),
            child: Icon(
              Icons.auto_stories_outlined,
              size: 18,
              color: theme.colorScheme.onPrimary,
            ),
          ),
        ),
        const SizedBox(width: ScribeSpacing.x3),
        Flexible(
          child: Text(
            'ScribeForge',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.titleMedium,
          ),
        ),
      ],
    );
  }
}

class _NavigationHeading extends StatelessWidget {
  const _NavigationHeading();

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(18, 19, 18, 10),
    child: Text('数字化工作台', style: Theme.of(context).textTheme.bodySmall),
  );
}

/// The source picker is a single row when there's enough content space and
/// stacks when a long path would otherwise leave no room for its label.
class WorkspaceFileField extends StatelessWidget {
  const WorkspaceFileField({
    super.key,
    required this.field,
    required this.action,
  });

  final Widget field;
  final Widget action;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      if (constraints.maxWidth < 420) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[field, const SizedBox(height: 8), action],
        );
      }
      return Row(
        children: <Widget>[
          Expanded(child: field),
          const SizedBox(width: 10),
          action,
        ],
      );
    },
  );
}
