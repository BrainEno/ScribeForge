# Windows desktop distribution plan

**Status:** the bundled-backend resolver, frozen-backend build, per-user installer recipe, and Windows packaging workflow are implemented in source. No end-user release is published by this change. A green packaging workflow validates an unsigned CI artifact, not a fresh-VM installation, signing, upgrade, or real OCR model run.

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

## Implemented build path

1. Flutter resolves an explicit developer override first, then `backend/scribeforge(.exe)` beside the application (or in `Contents/Resources/backend` on macOS), and only then falls back to PATH for development. Settings identifies the selected source and never presents the PATH fallback as an end-user bundle.
2. `packaging/scribeforge_backend.spec` freezes the CLI with its native PDF dependencies. It also ships ordinary Worker source under `_internal/worker-src`, because the separate MinerU/PaddleOCR interpreters cannot import PyInstaller's private module archive.
3. `.github/workflows/windows-desktop.yml` builds the backend, checks `--help` and `runtime-status --json` with Python removed from PATH, verifies external Worker imports, builds Flutter, assembles the portable folder, compiles `packaging/windows/ScribeForge.iss`, and uploads checksums. CI artifacts are unsigned.
4. The Flutter settings page provides an initialization entry point. Processing is gated on a connected core and ready managed backends, so a missing Worker module becomes a repairable setup action rather than an unexplained yellow-bar exception.

## First-run initialization troubleshooting (#212)

Two failure modes were reported from fresh Windows machines and are handled in
the client:

1. **`python3 -m venv` fails with exit code 9009** — `python3`/`python` on
   Windows may be the Microsoft Store *app-execution alias* stub, which prints
   "Python was not found; install from the Microsoft Store" and cannot create
   virtual environments. The client now probes interpreters in the order
   `py -3`, `python`, `python3`, per-user `...
   \Programs\Python\Python3xx\python.exe`, then Microsoft Store package
   installs under `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.*`;
   it rejects any probe whose output mentions the Microsoft Store and requires
   a successful `import venv, ensurepip` check before provisioning. When venv
   creation still fails with 9009, the settings dialog shows this exact
   diagnosis plus the fixes (reinstall from python.org with "Add python.exe to
   PATH", use the `py` launcher, or disable the app-execution aliases under
   Settings → Apps → Advanced app settings → App execution aliases).
2. **`ProcessException: 系统找不到指定的文件 (scribeforge runtime-status --json)`** —
   no bundled backend and no `scribeforge` on PATH. Backend resolution now also
   checks the provisioned `backend-venv` CLI next to `settings.json` before
   falling back to PATH, and the banner for a launch failure tells the user to
   run 「设置 → 初始化环境」.

## Remaining release work

1. Test installation, launch, upgrade-in-place, and uninstall in a fresh Windows 11 x64 VM without Python or Flutter; verify project/runtime preservation and native PDF import.
2. Exercise the complete first-run download on controlled test networks and record artifact size, model download size, disk requirements, and failure/recovery behavior. These are not inferred from unit tests.
3. Publish signed release assets only after signing credentials and SmartScreen/status documentation are available. Keep CI artifacts clearly marked unsigned.
