# Flutter Desktop UI Design Pattern

This document is the UI contract for `clients/flutter_desktop`. New screens and reusable widgets should follow it; update the shared tokens and components before adding one-off visual rules.

## Product character

ScribeForge is a local-first editorial workbench for protecting and reviewing source evidence. Its interface should feel calm, trustworthy, and precise: warm paper surfaces, deep botanical green for primary action, restrained amber for attention, and clear typography. Avoid dashboard chrome, decorative gradients, gratuitous animation, and color-only state indicators.

Visual polish must never blur workflow semantics. OCR candidates are evidence, not accepted text. The user explicitly records each trusted decision. VLM review remains opt-in. Audit is read-only, backups are verified, and restore must never overwrite an existing project.

## Design tokens

Use `ScribeTheme`, `ScribePalette`, `ScribeSpacing`, `ScribeRadii`, and the shared component library; do not introduce new raw color, spacing, or radius values in screens unless a component needs a documented exception.

### Color roles

| Role | Token | Intent |
| --- | --- | --- |
| Primary | `ScribePalette.forest` | Main action, selected navigation, focus |
| Primary container | `ScribePalette.mist` | Selected/quiet emphasis behind icons and badges |
| Canvas | `ScribePalette.canvas` | Main app background; a soft neutral, not pure white |
| Surface | `ScribePalette.paper` | Cards, dialogs, inputs |
| Ink | `ScribePalette.ink` | Primary text |
| Muted ink | `ScribePalette.mutedInk` | Supporting text and metadata |
| Border | `ScribePalette.border` | Low-contrast separators and card outlines |
| Success | `ScribePalette.success` | Completed, healthy, verified |
| Attention | `ScribePalette.amber` | Warnings and review-needed states |
| Error | `ColorScheme.error` | Failed operation or blocking integrity issue |

Use semantic `ColorScheme` roles in widgets (`primary`, `surface`, `error`, etc.). Success, attention, and error states must include an icon and text label, not just a color change. Candidate content must carry an explicit “未验证”/“候选” label.

### Spacing and shape

Use `ScribeSpacing`'s four-point rhythm (`x1`–`x10`: `4`, `8`, `12`, `16`, `20`, `24`, `32`, and `40` px). Typical desktop page padding is `x6`; section gaps are `x5`–`x6`; card padding is `x5`; compact control gaps are `x2`–`x3`.

Use `ScribeRadii.control` (`8` px) for controls, `ScribeRadii.card` (`12` px) for cards, and `ScribeRadii.panel` (`16` px) for large grouped panels. Use thin borders and no elevation for ordinary workspace cards. Reserve elevation for menus, dialogs, and transient overlays.

### Typography

Use the platform sans-serif through the shared `TextTheme`—no per-screen font imports.

| Use | Style |
| --- | --- |
| Page title | `headlineSmall`, semibold |
| Section title | `titleMedium`, semibold |
| Main body and controls | `bodyMedium` |
| Supporting copy | `bodySmall`, `onSurfaceVariant` |
| IDs, paths, hashes, timestamps | `labelSmall` or selectable body text |

Do not use long all-caps labels. Keep explanatory copy concise and action-oriented. Preserve source text exactly; display formatting must not modify stored evidence.

## Layout rules

- Keep the persistent application shell: one top app bar, one navigation rail, one active work surface.
- Each page starts with `ScribePageHeader` (icon, title, one-line purpose, optional action).
- Group related controls inside `ScribeSectionCard`; do not draw a card around every field.
- On wide windows, use two-column cards only when both groups remain independently understandable. At narrower widths, let `Wrap`/`LayoutBuilder` stack them without horizontal scrolling.
- Use a single vertical page scroll for long forms. Avoid nested vertical scroll views unless each is an explicitly bounded evidence pane.
- Keep source/project identity, operation status, and next action visible near the controls they affect.
- Empty states explain the prerequisite and the next safe action; they are not blank pages.

## Shared components

Implement these in `lib/widgets/workspace_components.dart` and reuse them rather than restyling each page.

### `ScribePageHeader`

Required: icon, title, subtitle. Optional: trailing action. The icon sits in a 44–48 px primary-container badge. Title and subtitle align on the same left edge. Subtitle explains what this page does, not the whole product.

### `ScribeSectionCard`

Required: icon, title, child. Optional: description and trailing action. Use the shared card shape, header spacing, and body padding. One card corresponds to one task or decision group.

### `ScribeStatusBanner`

Kinds: info, success, warning, error. Each banner has an icon, concise title, and message. Errors describe what failed and the retry/repair path. Warnings do not imply failure. Use one operation-level banner; do not stack duplicate snackbars and persistent banners for the same result.

### `ScribeEmptyState`

Required: icon, title, message. Optional: primary action. State the missing prerequisite and provide a direct next step when possible.

### Buttons and fields

Use themed Material 3 `FilledButton` for the primary action, `OutlinedButton` for a secondary action, and `TextButton` for low-emphasis navigation. Use `IconButton` only for a single, discoverable icon action with a tooltip. Inputs use `InputDecorationTheme`; labels remain visible when the value is empty. Disable unavailable actions and explain the prerequisite nearby.

## Interaction and operation states

Every asynchronous operation has four visible states: idle, running, success, and failure. While running, disable duplicate invocation, show an inline progress indicator, and retain the operation's context. On failure, preserve the current project and entered values, show actionable stderr/diagnostics, and allow retry without re-importing or reprocessing. Do not show success until the CLI has returned a successful structured result.

Destructive or identity-changing actions require a confirmation that names the exact source and destination. Restore always targets a new directory. Backup never overwrites by default. Audit is visibly read-only. Confirming an action means only that action; it must not implicitly accept OCR text or clear review items.

Use local child-process arguments, never shell command strings. Stream process output through the injected CLI executor. Treat malformed JSON and unsupported schema versions as errors; do not guess a report from human-readable output.

## Accessibility and keyboard

- Target at least 44 × 44 px for primary interactive controls.
- Keep keyboard focus visible; preserve existing review shortcuts and text-entry behavior.
- Provide tooltips for icon-only actions and meaningful semantic labels for status/progress.
- Meet WCAG AA contrast for text and controls; never communicate status through color alone.
- Respect system text scaling and allow long paths/errors to wrap or be selected/copied.

## Motion

Prefer no animation for evidence or status changes. If motion clarifies a transition, keep it short (about 120–200 ms), respect reduced-motion settings, and never animate OCR text in a way that could look like content was rewritten.

## Code review checklist

Before adding or changing a screen, verify:

1. It uses shared tokens and components; no ad-hoc palette or spacing system was added.
2. It has a useful loading, empty, success, and failure state.
3. Every operation is reachable from the Flutter UI and uses the replaceable CLI/file-picker boundaries.
4. Retry preserves user work and never starts unrelated pipeline stages.
5. Human decisions still require an explicit action; no navigation or shortcut auto-confirms them.
6. VLM remains off unless the user explicitly enabled it.
7. Source images, OCR runs, and accepted decisions are not rewritten by presentation code.
8. Widget tests cover the main success and failure paths with fakes, without model downloads or real OCR.
