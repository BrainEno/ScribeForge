# Windows desktop distribution plan

**Status:** proposal; no end-user installer is published yet. This document is a release design, not a claim that the current development build is toolchain-free.

## Goal

A new Windows user should be able to install ScribeForge and open the desktop app without installing Git, Flutter, Python, or an OCR toolchain. The app should continue to keep projects and downloaded OCR environments separate. First-run setup must explain the network, download-size, and disk-space requirements before installation begins.

## Recommended first release

- **Target:** Windows 11 x64, per-user installation, no administrator permission. Add Windows 10 and ARM64 only after explicit smoke testing.
- **Installer:** signed Inno Setup per-user installer (`ScribeForge-Setup-x64.exe`) with Start Menu and optional desktop shortcuts. Inno Setup is the initial choice because it can package a Flutter desktop folder and a side-by-side CLI without requiring a Store account or a machine-wide install.
- **Desktop client:** Flutter Windows release output, built from the repository's pinned stable Flutter version on `windows-latest`.
- **CLI:** package `scribeforge` as a Windows executable using a reproducible, pinned PyInstaller build, preferably one-folder rather than one-file. Place it beside the Flutter executable and have the app resolve that bundled executable before falling back to the developer's configured CLI path. Include and smoke-test package data and native dependencies.
- **Python:** do not require or modify a system Python installation. The CLI's existing verified `uv` bootstrap provisions the private Python 3.12 environments on first OCR setup.
- **OCR weights:** do not bundle model weights or CUDA/Paddle packages in the installer. Their size and machine-specific CPU/GPU variants would make the initial download excessive. Download them on demand into the user's private runtime, with the native setup dialog showing backend, phase, step, and failures.
- **Storage boundaries:** app files under the user's per-user Programs directory; settings under `%APPDATA%\ScribeForge`; CLI-managed tools, Python environments, caches, and models under `%LOCALAPPDATA%\ScribeForge\runtime`; book project evidence remains in the project folder selected by the user.

The resulting installer can remove the Flutter/Python *development-toolchain* requirement, but not the first-run network, model-download, or storage requirements. Do not advertise it as offline or zero-download software.

## Release pipeline

1. Pin Flutter, Python, PyInstaller, and Inno Setup versions. Build the Python CLI executable in a clean Windows runner from the locked project dependencies; record the source commit and dependency versions.
2. Run CLI smoke checks against the frozen executable: `--help`, `runtime-status --json`, and a mocked/test-only setup protocol check. Do not download production OCR models in CI.
3. Build the Flutter Windows release and assemble a staging folder with the Flutter runner, required Flutter DLLs/data, and the frozen CLI. Verify the application discovers the adjacent CLI without a Python environment or PATH entry.
4. Build the per-user installer. Verify install, launch, settings/CLI discovery, upgrade-in-place, uninstall, and preservation of user runtime/project data in a fresh Windows 11 x64 VM that has no Python or Flutter installed.
5. Publish the installer and checksums as release assets only after the Core and Flutter Desktop CI jobs and the Windows packaging job pass. Sign both the installer and executables when the release signing certificate is available; until then, clearly mark artifacts as unsigned and do not imply SmartScreen reputation.

## Acceptance gates

- Fresh-user install and launch succeeds without Python, Flutter, Git, or administrator permission.
- The app reports a missing backend clearly, starts `uv` bootstrap and OCR setup from the native UI, and displays step-level progress and actionable backend/step failures.
- A failed backend can be repaired alone without reinstalling the other backend.
- Canceling before setup leaves runtime data untouched; closing after a run refreshes the displayed state.
- Upgrade and uninstall never silently delete projects, runtime models, caches, or user settings. Runtime cleanup, if later offered, must be a separate explicit action.
- Installer size, model download size, expected disk space, supported Windows versions, and signing status are published before release.

## Open implementation work

1. Add a bundled-CLI resolver to the Flutter settings layer with a user override and clear missing-executable diagnostics.
2. Create a Windows packaging workflow and checked-in installer recipe; add lockfiles/pins for the build tools.
3. Verify PyInstaller inclusion of ScribeForge package resources and all Windows native dependencies in a clean VM.
4. Add a Windows release smoke-test job, installer upgrade/uninstall tests, artifact checksums, and signing once credentials are provisioned in release CI.
5. Document supported Windows versions and storage/download estimates from measured release artifacts.
