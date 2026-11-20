import 'package:flutter/material.dart';

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
    final compact = useCompactNavigation(MediaQuery.sizeOf(context));
    final selected = destinations[selectedIndex];
    return Scaffold(
      appBar: AppBar(
        leading: compact
            ? PopupMenuButton<int>(
                key: const ValueKey('workspace-destination-menu'),
                icon: const Icon(Icons.menu),
                tooltip: '切换工作台页面',
                initialValue: selectedIndex,
                onSelected: onSelect,
                itemBuilder: (_) => _compactItems(),
              )
            : null,
        title: Text(
          selected.label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        actions: actions,
      ),
      body: Column(
        children: <Widget>[
          ...notices,
          Expanded(
            child: Row(
              children: <Widget>[
                SizedBox(
                  width: compact ? 0 : null,
                  child: compact
                      ? null
                      : NavigationRail(
                          selectedIndex: selectedIndex,
                          onDestinationSelected: onSelect,
                          labelType: NavigationRailLabelType.all,
                          destinations: <NavigationRailDestination>[
                            for (final destination in destinations)
                              NavigationRailDestination(
                                icon: Icon(destination.icon),
                                selectedIcon: Icon(
                                  destination.icon,
                                  color: Theme.of(context).colorScheme.primary,
                                ),
                                label: Text(destination.label),
                              ),
                          ],
                        ),
                ),
                if (compact)
                  const SizedBox.shrink()
                else
                  const VerticalDivider(thickness: 1, width: 1),
                Expanded(
                  key: const ValueKey('workspace-page-container'),
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
